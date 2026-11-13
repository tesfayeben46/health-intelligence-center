/*
    v_facility_resolution: master facility resolution, and the
    AutomateDV stage for hub_facility_conformed + lnk_same_as_facility.

    Strategy (steward decision > facility_id > normalised name):
        0. A current steward MDM decision (accept/merge in
           ops_governance.mdm_match_decision, read via the
           nhdw_vault_governance FDW seam) overrides everything for its
           source record. An empty log leaves steps 1 to 3 unchanged.
        1. Otherwise extract the shared canonical `facility_id` from each
           source.
           Every source carries it:
             - MFR Organization / Location  -> identifier JSON, system =
                 "http://moh.gov.et/identifier/facility"
             - DHIS2 OrgUnit                -> sat.code (= facility_id)
             - HCMIS Institution            -> description (slug fmt)
             - mBrana Institution           -> description (master) / town (USSD)
        2. conformed_bk = facility_id when present (deterministic);
           else norm_name (legacy / external partner data).
        3. Cluster rows by conformed_bk -> one row in
           hub_facility_conformed; rows bridged via lnk_same_as_facility.

    DHIS2 is filtered to level-5 nodes whose code matches the FAC- prefix
    (facilities, not the admin tree).

    Reads the v_sat_*_current views (one identity row per source record),
    never the raw sats: a renamed facility must contribute its current
    name only, not one candidate row per historical name.

    Stage columns (AutomateDV contract: hashes are pre-computed here):
        hk_facility_conformed   hub PK   = hash(conformed_bk)
        hk_source_facility      link FK  = the source hub's own hash key
        hk_same_as_facility     link PK  = hash(source_system,
                                                hex(hk_source_facility),
                                                hex(hk_facility_conformed))
    source_system must stay a PK-hash input: source hubs hash unqualified
    BKs, so hubs whose integer BK spaces overlap (HCMIS institution_id vs
    mBrana id) can produce identical hk_source_facility values; hashing
    only the participant keys would collide two systems' edges whenever
    the names also conform. Same rationale as lnk_same_as_commodity.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

WITH hcmis AS (
    SELECT
        sat.hk_hcmis_facility AS source_hk,
        'HCMIS' AS source_system,
        sat.name AS source_name,
        NULL::text AS source_code,
        -- First-match slug extraction (NULL when absent). Never use '\b'
        -- here: in Postgres AREs '\b' is backspace, not a word boundary
        -- ('\y' is), so a '\b' pattern silently matches nothing.
        SUBSTRING(sat.description FROM 'FAC-[A-Z]{2}-[A-Z]+-[a-f0-9]+') AS facility_id,
        LOWER(REGEXP_REPLACE(COALESCE(sat.name,''), '[^a-zA-Z0-9 ]', '', 'g')) AS norm_name,
        sat.load_ts
    FROM {{ ref('v_sat_hcmis_facility_current') }} sat
),
mbrana_master AS (
    SELECT
        sat.hk_mbrana_facility AS source_hk,
        'mBrana.dbo' AS source_system,
        sat.name AS source_name,
        NULL::text AS source_code,
        SUBSTRING(sat.description FROM 'FAC-[A-Z]{2}-[A-Z]+-[a-f0-9]+') AS facility_id,
        LOWER(REGEXP_REPLACE(COALESCE(sat.name,''), '[^a-zA-Z0-9 ]', '', 'g')) AS norm_name,
        sat.load_ts
    FROM {{ ref('v_sat_mbrana_facility_current') }} sat
),
mbrana_ussd AS (
    -- mBrana USSD sat doesn't carry a `town` column where the facility_id
    -- slug could live, so this source only contributes a norm_name match.
    SELECT
        sat.hk_mbrana_ussd_facility AS source_hk,
        'mBrana.ussd' AS source_system,
        sat.institution_name AS source_name,
        NULL::text AS source_code,
        NULL::text AS facility_id,
        LOWER(REGEXP_REPLACE(COALESCE(sat.institution_name,''),
                              '[^a-zA-Z0-9 ]', '', 'g')) AS norm_name,
        sat.load_ts
    FROM {{ ref('v_sat_mbrana_ussd_facility_current') }} sat
),
dhis2 AS (
    -- DHIS2 OrgUnit code == facility_id for level-5 nodes. Higher-level
    -- nodes (region/zone/woreda) have admin codes (ET-OR, ZON-OR-x,
    -- WOR-OR-y); those are geography, not facility, so they are filtered out.
    SELECT
        sat.hk_dhis2_org_unit AS source_hk,
        'DHIS2' AS source_system,
        sat.name AS source_name,
        sat.code AS source_code,
        CASE WHEN sat.code LIKE 'FAC-%' THEN sat.code ELSE NULL END AS facility_id,
        LOWER(REGEXP_REPLACE(COALESCE(sat.name,''), '[^a-zA-Z0-9 ]', '', 'g')) AS norm_name,
        sat.load_ts
    FROM {{ ref('v_sat_dhis2_org_unit_current') }} sat
),
mfr_loc AS (
    SELECT
        sat.hk_mfr_facility AS source_hk,
        'MFR.location' AS source_system,
        sat.name AS source_name,
        NULL::text AS source_code,
        SUBSTRING(sat.identifier::text FROM 'FAC-[A-Z]{2}-[A-Z]+-[a-f0-9]+') AS facility_id,
        LOWER(REGEXP_REPLACE(COALESCE(sat.name,''), '[^a-zA-Z0-9 ]', '', 'g')) AS norm_name,
        sat.load_ts
    FROM {{ ref('v_sat_mfr_facility_current') }} sat
),
mfr_org AS (
    SELECT
        sat.hk_mfr_organization AS source_hk,
        'MFR.organization' AS source_system,
        sat.name AS source_name,
        NULL::text AS source_code,
        SUBSTRING(sat.identifier::text FROM 'FAC-[A-Z]{2}-[A-Z]+-[a-f0-9]+') AS facility_id,
        LOWER(REGEXP_REPLACE(COALESCE(sat.name,''), '[^a-zA-Z0-9 ]', '', 'g')) AS norm_name,
        sat.load_ts
    FROM {{ ref('v_sat_mfr_organization_current') }} sat
    WHERE sat.identifier::text LIKE '%FAC-%'
),
unioned AS (
    SELECT * FROM hcmis
    UNION ALL SELECT * FROM mbrana_master
    UNION ALL SELECT * FROM mbrana_ussd
    UNION ALL SELECT * FROM dhis2
    UNION ALL SELECT * FROM mfr_loc
    UNION ALL SELECT * FROM mfr_org
),
decisions AS (
    -- Current, in-validity steward verdicts for this deployment context
    -- (36_mdm_decisions_read.sql: read-only FDW over nhdw_ops; written only
    -- via the steward console). accept/merge map one source record, keyed
    -- (source_system, hex(source hub hash)) like the MDM matcher's output,
    -- onto a master conformed_bk. A steward verdict overrides the
    -- deterministic derivation; an empty log leaves this view identical to
    -- the deterministic derivation (the parity contract).
    SELECT source_system_code, source_record_key, master_bk
    FROM {{ source('vault_governance', 'mdm_match_decision') }}
    WHERE recorded_to IS NULL
      AND decision IN ('accept', 'merge')
      AND master_bk IS NOT NULL
      AND deployment_context = '{{ var("deployment_context", "ET") }}'
      AND entity_class_code = 'facility'
      AND CURRENT_DATE >= valid_from AND CURRENT_DATE < valid_to
),
resolved AS (
    SELECT
        u.source_hk AS hk_source_facility,
        u.source_system,
        u.source_name,
        u.source_code,
        u.facility_id,
        u.norm_name,
        COALESCE(d.master_bk, u.facility_id, u.norm_name) AS conformed_bk,
        CASE WHEN d.master_bk IS NOT NULL   THEN 'steward_decision'
             WHEN u.facility_id IS NOT NULL THEN 'facility_id'
             ELSE 'norm_name' END AS match_strategy,
        u.load_ts
    FROM unioned u
    LEFT JOIN decisions d
      ON  d.source_system_code = u.source_system
      AND d.source_record_key  = encode(u.source_hk, 'hex')
    WHERE COALESCE(d.master_bk, u.facility_id, u.norm_name) <> ''
),
keyed AS (
    SELECT
        resolved.*,
        {{ automate_dv.hash(columns=['conformed_bk'], alias='hk_facility_conformed') }},
        'BV.facility_resolution' AS record_source
    FROM resolved
)
SELECT
    keyed.*,
    {{ automate_dv.hash(columns=['source_system',
                                 "encode(hk_source_facility, 'hex')",
                                 "encode(hk_facility_conformed, 'hex')"],
                        alias='hk_same_as_facility') }}
FROM keyed
