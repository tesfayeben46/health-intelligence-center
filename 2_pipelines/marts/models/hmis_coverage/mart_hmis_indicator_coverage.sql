/*
    mart_hmis_indicator_coverage: month by woreda by indicator by programme.

    Coverage % = facilities_reporting / facilities_expected.
    Source: DHIS2 dataValue facts (the only source carrying HMIS-shape
    indicator by period by facility together), read through the business
    vault:
      - facilities_reporting rides the BV spine
        (lnk_facility_indicator_period by dim_facility /
        hub_indicator_conformed / dim_period): one edge per conformed
        facility by indicator by monthly period that reported.
      - Measures (total/mean value) stay on the raw dataValue feed (the
        spine carries no measure satellite), but every satellite read goes
        through the v_*_current views (sats are insert-only; a bare sat
        join double-counts once history accumulates).
      - Month decoding comes from sat_dhis2_period_resolved (the 6-format
        period decoder), programme membership from v_programme_resolution
        (the indicator-code -> programme single source of truth).
      - region_id / woreda_uid are decoded from the OU code/path:
        the BV carries no facility -> geography membership, so the
        OU path is the only holder of that hierarchy.

    Consumed by:
      - Public Dashboard regional coverage map (rolled up to region)
      - Internal BI Service Performance (joined to programme targets)
      - Programme cascade base values
      - Data Quality completeness denominator
*/
WITH ou AS (
    -- FAC-% level-5 org units. Identity from the current-record view;
    -- geo legs decoded from the path: /<root>/<region>/<zone>/<woreda>/<facility>
    SELECT
        sat.hk_dhis2_org_unit,
        sat.code AS ou_code,
        SPLIT_PART(sat.path, '/', 5) AS woreda_uid,
        SUBSTRING(sat.code FROM '^FAC-([A-Z]{2})-') AS region_id
    FROM {{ source('nhdw_vault_business', 'v_sat_dhis2_org_unit_current') }} sat
    WHERE sat.code LIKE 'FAC-%'
),
wname AS (
    -- Woreda display names: a level-4 org unit's path ends with its own uid
    -- (/<root>/<region>/<zone>/<woreda_uid>), the same segment facilities carry
    -- at position 5, so uid -> name resolves without touching the raw hub.
    -- Downstream (sv-risk-woredas et al.) shows names, not surrogate uids.
    SELECT
        SPLIT_PART(sat.path, '/', 5) AS woreda_uid,
        MAX(sat.name)                AS woreda_name
    FROM {{ source('nhdw_vault_business', 'v_sat_dhis2_org_unit_current') }} sat
    WHERE sat.level = 4
    GROUP BY 1
),
de AS (
    -- IDSR-* are weekly disease-surveillance elements (woreda grain, own mart);
    -- exclude them so they don't inflate HMIS monthly coverage via the CROSS JOIN.
    SELECT
        sat.hk_dhis2_data_element,
        sat.code AS indicator_id
    FROM {{ source('nhdw_vault_business', 'v_sat_dhis2_data_element_current') }} sat
    WHERE sat.code NOT LIKE 'IDSR-%'
),
spine AS (
    -- The BV spine, decoded via the thin dims: one edge per conformed
    -- facility by indicator by monthly period that actually reported.
    SELECT
        f.facility_id,
        i.indicator_id,
        p.period_id AS month_id
    FROM {{ source('nhdw_vault_business', 'lnk_facility_indicator_period') }} s
    JOIN {{ source('nhdw_vault_business', 'dim_facility') }} f USING (hk_facility_conformed)
    JOIN {{ source('nhdw_vault_business', 'hub_indicator_conformed') }} i USING (hk_indicator_conformed)
    JOIN {{ source('nhdw_vault_business', 'dim_period') }} p USING (hk_period_conformed)
),
expected AS (
    -- "Expected" facilities for (woreda by indicator) = facilities that have
    -- ever reported that indicator: the facility's service profile inferred
    -- from its full reporting history (standard DHIS2 reporting-rate
    -- practice: expected reports come from dataset assignment, not from the
    -- universe of facilities). Counting every facility as expected for every
    -- indicator would let a health post that never runs an NCD clinic drag
    -- HTN completeness down permanently: structural, not behavioural,
    -- incompleteness.
    SELECT
        ou.region_id,
        ou.woreda_uid,
        s.indicator_id,
        COUNT(DISTINCT s.facility_id) AS facilities_expected
    FROM spine s
    JOIN ou ON ou.ou_code = s.facility_id
    JOIN de ON de.indicator_id = s.indicator_id
    GROUP BY 1,2,3
),
reporting AS (
    -- Coverage counts FROM the spine (this is what the spine exists for).
    -- ou supplies the geo legs; the de join re-applies the IDSR exclusion.
    SELECT
        ou.region_id,
        ou.woreda_uid,
        s.indicator_id,
        s.month_id,
        COUNT(DISTINCT s.facility_id) AS facilities_reporting
    FROM spine s
    JOIN ou ON ou.ou_code = s.facility_id
    JOIN de ON de.indicator_id = s.indicator_id
    GROUP BY 1,2,3,4
),
vals AS (
    -- Measures from the raw dataValue feed (the spine carries no measure sat),
    -- satellite reads via the current-record view, month via the resolved sat.
    SELECT
        ou.region_id,
        ou.woreda_uid,
        de.indicator_id,
        TO_CHAR(per.start_date, 'YYYY-MM') AS month_id,
        SUM(dv_sat.value::numeric) AS total_value,
        AVG(dv_sat.value::numeric) AS mean_value
    FROM {{ source('nhdw_vault_raw', 'lnk_dhis2_data_value') }} dv_lnk
    JOIN {{ source('nhdw_vault_business', 'v_sat_dhis2_data_value_current') }} dv_sat USING (hk_dhis2_data_value)
    JOIN {{ source('nhdw_vault_business', 'sat_dhis2_period_resolved') }} per USING (hk_dhis2_period)
    JOIN ou USING (hk_dhis2_org_unit)
    JOIN de USING (hk_dhis2_data_element)
    WHERE per.period_type = 'monthly'
      AND dv_sat.value IS NOT NULL
    GROUP BY 1,2,3,4
),
joined AS (
    SELECT
        v.region_id,
        v.woreda_uid,
        pr.programme_id,
        v.indicator_id,
        v.month_id,
        r.facilities_reporting,
        e.facilities_expected,
        v.total_value,
        v.mean_value
    FROM vals v
    JOIN reporting r USING (region_id, woreda_uid, indicator_id, month_id)
    JOIN expected  e USING (region_id, woreda_uid, indicator_id)
    JOIN (
        -- Programme membership from the BV single source of truth
        -- (v_programme_resolution).
        SELECT DISTINCT source_code AS indicator_id, programme_id
        FROM {{ source('nhdw_vault_business', 'v_programme_resolution') }}
    ) pr USING (indicator_id)
)
SELECT
    joined.region_id,
    joined.woreda_uid,
    wname.woreda_name,
    joined.programme_id,
    joined.indicator_id,
    joined.month_id,
    joined.facilities_reporting,
    joined.facilities_expected,
    CASE WHEN joined.facilities_expected > 0
         THEN ROUND(100.0 * joined.facilities_reporting / joined.facilities_expected, 2)
         ELSE NULL END                      AS coverage_pct,
    joined.total_value,
    joined.mean_value
FROM joined
LEFT JOIN wname USING (woreda_uid)
