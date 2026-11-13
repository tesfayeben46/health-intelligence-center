/*
    sat_facility_canonical: the facility golden record, versioned.

    One row per (conformed facility by canonical revision): the best value for
    each attribute chosen across the master's source records by a fixed
    per-attribute source priority, with field-level provenance (every golden
    value carries the source system it came from). Insert-only: when the
    golden attributes change (a rename lands, a steward decision re-keys a
    record into the cluster, a better source starts carrying a field), a new
    revision row is inserted (hashdiff gate); history is never rewritten.

    Attribute authorities (first source that has the value wins):
      name       MFR.location > MFR.organization > DHIS2 > HCMIS > mBrana
                 (the registry names facilities; supply systems abbreviate)
      status     MFR.location (registry operational status) > mBrana
      telecom    MFR.organization (FHIR telecom) > mBrana phone
      lat/lng    MFR.location (position) > mBrana GPS
                 (MFR Location carries no position in the demonstration
                 data, so mBrana supplies the coordinates.)
      admin area mBrana woreda/zone, held there as bigint FKs and resolved
                 to names via the mBrana admin hubs/sats

    Downstream: dims/marts can join the current revision (max load_ts per
    hk) for golden attributes instead of re-deriving per-source precedence
    locally; the *_source columns surface provenance to the UI.
*/
{{ config(
    materialized='incremental',
    on_schema_change='append_new_columns',
    schema='nhdw_vault_business'
) }}

WITH members AS (
    -- every source record in each master's cluster, tagged with the fixed
    -- source rank (lower = more authoritative for name)
    SELECT
        r.hk_facility_conformed,
        r.hk_source_facility,
        r.source_system,
        r.source_name,
        CASE r.source_system
            WHEN 'MFR.location'     THEN 1
            WHEN 'MFR.organization' THEN 2
            WHEN 'DHIS2'            THEN 3
            WHEN 'HCMIS'            THEN 4
            WHEN 'mBrana.dbo'       THEN 5
            ELSE 6 END AS source_rank
    FROM {{ ref('v_facility_resolution') }} r
),
golden_name AS (
    SELECT DISTINCT ON (hk_facility_conformed)
        hk_facility_conformed,
        source_name   AS canonical_name,
        source_system AS name_source
    FROM members
    WHERE COALESCE(source_name, '') <> ''
    ORDER BY hk_facility_conformed, source_rank
),
-- one attribute row per master from each providing source (DISTINCT ON with a
-- deterministic order: a cluster holding several records of one source must
-- not fan the golden join out into duplicate revisions)
mfr_loc_attrs AS (
    SELECT DISTINCT ON (m.hk_facility_conformed)
           m.hk_facility_conformed,
           s.status, s.latitude, s.longitude
    FROM members m
    JOIN {{ ref('v_sat_mfr_facility_current') }} s
      ON s.hk_mfr_facility = m.hk_source_facility
    WHERE m.source_system = 'MFR.location'
    ORDER BY m.hk_facility_conformed, m.hk_source_facility
),
mfr_org_attrs AS (
    SELECT DISTINCT ON (m.hk_facility_conformed)
           m.hk_facility_conformed, s.telecom
    FROM members m
    JOIN {{ ref('v_sat_mfr_organization_current') }} s
      ON s.hk_mfr_organization = m.hk_source_facility
    WHERE m.source_system = 'MFR.organization'
    ORDER BY m.hk_facility_conformed, m.hk_source_facility
),
mbrana_attrs AS (
    -- mBrana holds woreda/zone as foreign keys (bigint) into its own admin
    -- tables, not as names; resolve them to names here so the golden column
    -- carries something a human can read.
    SELECT DISTINCT ON (m.hk_facility_conformed)
           m.hk_facility_conformed,
           -- Cast the type-sensitive columns to their canonical types. dlt
           -- preserves the source types when mBrana is ingested, but if the
           -- source is skipped (a non-default profile has no mBrana data) the
           -- resilient empty raw view types every column Nullable(String), and
           -- the golden-record build below uses these as boolean / numeric. The
           -- cast is an identity when the real types are present and yields
           -- correctly-typed NULLs when the source is empty.
           s.operational_status::boolean AS operational_status, s.phone,
           s.latitude::numeric  AS latitude,
           s.longitude::numeric AS longitude,
           w.woreda_name, z.zone_name
    FROM members m
    JOIN {{ ref('v_sat_mbrana_facility_current') }} s
      ON s.hk_mbrana_facility = m.hk_source_facility
    LEFT JOIN {{ ref('hub_mbrana_woreda') }} hw ON hw.id = s.woreda
    LEFT JOIN {{ ref('v_sat_mbrana_woreda_current') }} w
           ON w.hk_mbrana_woreda = hw.hk_mbrana_woreda
    LEFT JOIN {{ ref('hub_mbrana_zone') }} hz   ON hz.id = s.zone
    LEFT JOIN {{ ref('v_sat_mbrana_zone_current') }} z
           ON z.hk_mbrana_zone = hz.hk_mbrana_zone
    WHERE m.source_system = 'mBrana.dbo'
    ORDER BY m.hk_facility_conformed, m.hk_source_facility
),
golden AS (
    SELECT
        gn.hk_facility_conformed,
        gn.canonical_name,
        gn.name_source,
        -- mBrana carries operational status as a boolean, MFR as FHIR text;
        -- normalise mBrana onto the FHIR vocabulary so the golden column has
        -- one meaning whichever source supplied it.
        COALESCE(ml.status,
                 CASE WHEN mb.operational_status THEN 'active'
                      WHEN mb.operational_status IS NOT NULL THEN 'inactive'
                 END)                                            AS operational_status,
        CASE WHEN ml.status IS NOT NULL              THEN 'MFR.location'
             WHEN mb.operational_status IS NOT NULL  THEN 'mBrana.dbo' END AS status_source,
        COALESCE(mo.telecom,               mb.phone)               AS telecom,
        CASE WHEN mo.telecom IS NOT NULL             THEN 'MFR.organization'
             WHEN mb.phone IS NOT NULL               THEN 'mBrana.dbo' END AS telecom_source,
        COALESCE(ml.latitude::numeric,     mb.latitude)            AS latitude,
        COALESCE(ml.longitude::numeric,    mb.longitude)           AS longitude,
        CASE WHEN ml.latitude IS NOT NULL            THEN 'MFR.location'
             WHEN mb.latitude IS NOT NULL            THEN 'mBrana.dbo' END AS coord_source,
        mb.woreda_name,
        mb.zone_name,
        CASE WHEN mb.woreda_name IS NOT NULL         THEN 'mBrana.dbo' END AS admin_source
    FROM golden_name gn
    LEFT JOIN mfr_loc_attrs  ml USING (hk_facility_conformed)
    LEFT JOIN mfr_org_attrs  mo USING (hk_facility_conformed)
    LEFT JOIN mbrana_attrs   mb USING (hk_facility_conformed)
),
revisioned AS (
    SELECT
        golden.*,
        md5(
            COALESCE(canonical_name, '')            || '|' ||
            COALESCE(operational_status, '')        || '|' ||
            COALESCE(telecom, '')                   || '|' ||
            COALESCE(latitude::text, '')            || '|' ||
            COALESCE(longitude::text, '')           || '|' ||
            COALESCE(woreda_name, '')               || '|' ||
            COALESCE(zone_name, '')
        ) AS hashdiff,
        CURRENT_TIMESTAMP AS load_ts,
        'BV.facility_canonical' AS record_source
    FROM golden
)
SELECT * FROM revisioned
{% if is_incremental() %}
-- insert-only revision gate against the latest revision only (a golden
-- record that changes back to an earlier state gets a fresh revision;
-- comparing against all history would silently swallow the change-back)
WHERE NOT EXISTS (
    SELECT 1
    FROM (
        SELECT DISTINCT ON (hk_facility_conformed) hk_facility_conformed, hashdiff
        FROM {{ this }}
        ORDER BY hk_facility_conformed, load_ts DESC
    ) t
    WHERE t.hk_facility_conformed = revisioned.hk_facility_conformed
      AND t.hashdiff = revisioned.hashdiff
)
{% endif %}
