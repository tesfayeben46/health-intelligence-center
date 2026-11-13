/*
    v_facility_resolution_asof: the facility masters as they were at a point
    in time, so a pinned report can be reproduced.

    The live view (v_facility_resolution) reads the current decision log, which
    is right for operations: a steward's verdict is reflected on the next read.
    That also means a number published last month cannot be re-derived today if
    a decision has landed since. This view is the reproducible counterpart:
    same derivation, but the decision log is read as of a timestamp:

        recorded_from <= T AND (recorded_to IS NULL OR recorded_to > T)

    which is exactly "the decisions the stewards had made at T", straight out
    of the bitemporal log. Supersession is respected: a decision made before T
    and reverted before T is correctly absent.

    Pinned per session, so one deployed view answers any point in time without
    a rebuild:

        SET nhdw.release_as_of = '2026-09-20T20:30:00Z';
        SELECT * FROM nhdw_vault_business.v_facility_resolution_asof;

    (`SET LOCAL` inside a transaction scopes it to that transaction.) A dbt var
    of the same name sets the compile-time default; unset, both default to
    far-future, which makes the view identical to the live one, so this model
    is additive and cannot change current numbers.

    A session setting is used instead of a build-time dbt var: a
    release is reproduced by whoever is asking, when they ask, and rebuilding
    the warehouse to read history would make reproduction a deployment.

    The other half of a release: decisions are only one of the two inputs
    that determine a master; the source records are the other. A source
    row that arrived after T still appears here, because this view reads the
    current sats. Reproducing a release fully therefore means pairing this
    as-of cut with the release's source_watermark (ops_governance.mdm_release),
    and that is why a release pins both. This model does not
    filter source rows by load_ts: satellites are insert-only but the
    *_current views collapse to the latest record per key, so a naive load_ts
    filter here would silently drop entities instead of reconstructing them. A
    faithful source-side reconstruction reads the sats as of T, which belongs
    in the *_current layer instead of being approximated here.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

WITH _asof AS (
    -- the pinned instant: the session setting when present, else the
    -- compile-time default (far future = "as things stand now")
    SELECT COALESCE(NULLIF(current_setting('nhdw.release_as_of', true), ''),
                    '{{ var("release_as_of", "2099-12-31T00:00:00Z") }}')::timestamptz AS t
),
decisions_asof AS (
    SELECT source_system_code, source_record_key, master_bk
    FROM {{ source('vault_governance', 'mdm_match_decision') }}, _asof
    WHERE decision IN ('accept', 'merge')
      AND master_bk IS NOT NULL
      AND deployment_context = '{{ var("deployment_context", "ET") }}'
      AND entity_class_code = 'facility'
      -- knowledge time: what the platform knew at T
      AND recorded_from <= _asof.t
      AND (recorded_to IS NULL OR recorded_to > _asof.t)
      -- valid time: the mastering statement is true then too
      AND _asof.t::date >= valid_from
      AND _asof.t::date <  valid_to
),
base AS (
    -- the source-side derivation is owned by the live view; re-deriving it
    -- here would be a second copy that drifts. Read its pre-decision columns
    -- and re-apply the as-of decisions on top.
    SELECT
        hk_source_facility, source_system, source_name, source_code,
        facility_id, norm_name, load_ts
    FROM {{ ref('v_facility_resolution') }}
),
resolved AS (
    SELECT
        b.hk_source_facility,
        b.source_system,
        b.source_name,
        b.source_code,
        b.facility_id,
        b.norm_name,
        COALESCE(d.master_bk, b.facility_id, b.norm_name) AS conformed_bk,
        CASE WHEN d.master_bk IS NOT NULL   THEN 'steward_decision'
             WHEN b.facility_id IS NOT NULL THEN 'facility_id'
             ELSE 'norm_name' END AS match_strategy,
        b.load_ts
    FROM base b
    LEFT JOIN decisions_asof d
      ON  d.source_system_code = b.source_system
      AND d.source_record_key  = encode(b.hk_source_facility, 'hex')
    WHERE COALESCE(d.master_bk, b.facility_id, b.norm_name) <> ''
)
SELECT
    resolved.*,
    {{ automate_dv.hash(columns=['conformed_bk'], alias='hk_facility_conformed') }},
    'BV.facility_resolution_asof' AS record_source
FROM resolved
