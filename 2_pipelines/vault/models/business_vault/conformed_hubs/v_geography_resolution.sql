/*
    v_geography_resolution: admin-hierarchy node resolution, and the
    AutomateDV stage for hub_geography_conformed.
    Sources: DHIS2 org units (region/zone/woreda level filter), mBrana
    dim hubs (region/zone/woreda), MFR Organizations at admin tiers,
    all read via the v_sat_*_current views (one identity row per record).

    Levels:
        L2 = region   (ET-XX)         e.g. ET-OR
        L3 = zone     (ZON-XX-N)
        L4 = woreda   (WOR-XX-N)
        L5 = facility (FAC-…)   (facilities go through v_facility_resolution
                                  not this view)
    Conformed BK = the admin code itself (already standardised at the
    shared-entity-model level). hk_geography_conformed = hash(geography_id)
    is pre-computed here per the AutomateDV stage contract.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

WITH dhis2 AS (
    SELECT
        sat.hk_dhis2_org_unit AS source_hk,
        'DHIS2' AS source_system,
        sat.name AS source_name,
        sat.code AS source_code,
        CASE
            WHEN sat.code LIKE 'ET-%'  THEN sat.code
            WHEN sat.code LIKE 'ZON-%' THEN sat.code
            WHEN sat.code LIKE 'WOR-%' THEN sat.code
            ELSE NULL END AS geography_id,
        CASE
            WHEN sat.code LIKE 'ET-%'  THEN 'region'
            WHEN sat.code LIKE 'ZON-%' THEN 'zone'
            WHEN sat.code LIKE 'WOR-%' THEN 'woreda'
            ELSE NULL END AS geography_level,
        sat.load_ts
    FROM {{ ref('v_sat_dhis2_org_unit_current') }} sat
    WHERE sat.code LIKE 'ET-%' OR sat.code LIKE 'ZON-%' OR sat.code LIKE 'WOR-%'
),
mbrana_region AS (
    SELECT sat.hk_mbrana_region AS source_hk, 'mBrana.region' AS source_system,
           sat.region_name AS source_name, sat.p_code AS source_code,
           sat.p_code AS geography_id, 'region' AS geography_level, sat.load_ts
    FROM {{ ref('v_sat_mbrana_region_current') }} sat
),
mbrana_zone AS (
    SELECT sat.hk_mbrana_zone AS source_hk, 'mBrana.zone' AS source_system,
           sat.zone_name AS source_name, sat.p_code AS source_code,
           sat.p_code AS geography_id, 'zone' AS geography_level, sat.load_ts
    FROM {{ ref('v_sat_mbrana_zone_current') }} sat
),
mbrana_woreda AS (
    SELECT sat.hk_mbrana_woreda AS source_hk, 'mBrana.woreda' AS source_system,
           sat.woreda_name AS source_name, sat.p_code AS source_code,
           sat.p_code AS geography_id, 'woreda' AS geography_level, sat.load_ts
    FROM {{ ref('v_sat_mbrana_woreda_current') }} sat
),
mfr_admin AS (
    -- MFR Organizations with identifier system in {SYS_REGION, SYS_ZONE, SYS_WOREDA, SYS_COUNTRY}.
    SELECT
        sat.hk_mfr_organization AS source_hk,
        'MFR.organization' AS source_system,
        sat.name AS source_name,
        NULL::text AS source_code,
        -- '\y' = word boundary in Postgres AREs ('\b' is backspace and would
        -- match nothing, so no MFR admin row would resolve).
        CASE
            WHEN sat.identifier::text ~ '\yET-[A-Z]{2}\y' THEN
                (REGEXP_MATCH(sat.identifier::text, '\y(ET-[A-Z]{2})\y'))[1]
            WHEN sat.identifier::text ~ '\yZON-[A-Z]{2}-[a-f0-9]+\y' THEN
                (REGEXP_MATCH(sat.identifier::text, '\y(ZON-[A-Z]{2}-[a-f0-9]+)\y'))[1]
            WHEN sat.identifier::text ~ '\yWOR-[A-Z]{2}-[a-f0-9]+\y' THEN
                (REGEXP_MATCH(sat.identifier::text, '\y(WOR-[A-Z]{2}-[a-f0-9]+)\y'))[1]
            ELSE NULL END AS geography_id,
        CASE
            WHEN sat.identifier::text ~ '\yET-[A-Z]{2}\y'              THEN 'region'
            WHEN sat.identifier::text ~ '\yZON-[A-Z]{2}-[a-f0-9]+\y'   THEN 'zone'
            WHEN sat.identifier::text ~ '\yWOR-[A-Z]{2}-[a-f0-9]+\y'   THEN 'woreda'
            ELSE NULL END AS geography_level,
        sat.load_ts
    FROM {{ ref('v_sat_mfr_organization_current') }} sat
),
unioned AS (
    SELECT * FROM dhis2 WHERE geography_id IS NOT NULL
    UNION ALL SELECT * FROM mbrana_region WHERE geography_id IS NOT NULL
    UNION ALL SELECT * FROM mbrana_zone   WHERE geography_id IS NOT NULL
    UNION ALL SELECT * FROM mbrana_woreda WHERE geography_id IS NOT NULL
    UNION ALL SELECT * FROM mfr_admin     WHERE geography_id IS NOT NULL
)
SELECT
    unioned.*,
    {{ automate_dv.hash(columns=['geography_id'], alias='hk_geography_conformed') }},
    'BV.geography_resolution' AS record_source
FROM unioned
