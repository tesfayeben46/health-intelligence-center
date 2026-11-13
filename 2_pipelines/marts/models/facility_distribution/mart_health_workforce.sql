/*
    mart_health_workforce: one row per region: health-worker counts by cadre
    (physician / nurse / HEW) + WHO-comparable density per 10k population.

    Staffing rides the MFR facility Organization satellite (staff_phys/nurse/hew),
    joined to the conformed facility_id via v_facility_resolution (the single
    owner of cross-source facility identity), then rolled up to region.
    region_id is reconciled to the warehouse's 2-letter form on both legs:
      - staff:      DHIS2 FAC-<region>- code -> SPLIT_PART(code,'-',2) = 'OR'
      - population: DHIS2 POP-TOTAL (woreda grain) -> region org 'ET-XX' -> 'XX'
    so the two joins line up. Population LEFT JOIN: tolerate NULL (density NULL).

    Backs rm-workforce.
*/
WITH facility_region AS (
    SELECT sat.code AS facility_id, SPLIT_PART(sat.code, '-', 2) AS region_id
    FROM {{ source('nhdw_vault_business', 'v_sat_dhis2_org_unit_current') }} sat
    WHERE sat.code LIKE 'FAC-%'
),
staff AS (
    SELECT
        r.facility_id,
        COALESCE(sat.staff_phys::numeric, 0)  AS phys,
        COALESCE(sat.staff_nurse::numeric, 0) AS nurse,
        COALESCE(sat.staff_hew::numeric, 0)   AS hew
    FROM {{ source('nhdw_vault_business', 'v_sat_mfr_organization_current') }} sat
    JOIN {{ source('nhdw_vault_business', 'v_facility_resolution') }} r
        ON  r.hk_source_facility = sat.hk_mfr_organization
        AND r.source_system      = 'MFR.organization'
),
region_staff AS (
    SELECT
        fr.region_id,
        SUM(s.phys)  AS phys,
        SUM(s.nurse) AS nurse,
        SUM(s.hew)   AS hew,
        SUM(s.phys + s.nurse + s.hew) AS hcw_total
    FROM facility_region fr
    JOIN staff s USING (facility_id)
    GROUP BY 1
),
ou AS (
    SELECT sat.hk_dhis2_org_unit, SPLIT_PART(sat.path, '/', 3) AS region_ou_uid
    FROM {{ source('nhdw_vault_business', 'v_sat_dhis2_org_unit_current') }} sat
    WHERE sat.code LIKE 'WOR-%'
),
region_map AS (
    SELECT h.id AS region_ou_uid, REGEXP_REPLACE(sat.code, '^ET-', '') AS region_id
    FROM {{ source('nhdw_vault_raw', 'hub_dhis2_org_unit') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_dhis2_org_unit_current') }} sat USING (hk_dhis2_org_unit)
    WHERE sat.code LIKE 'ET-%'
),
pop_de AS (
    SELECT hk_dhis2_data_element
    FROM {{ source('nhdw_vault_business', 'v_sat_dhis2_data_element_current') }}
    WHERE code = 'POP-TOTAL'
),
pop_latest AS (
    SELECT
        dv_lnk.hk_dhis2_org_unit,
        dv_sat.value::numeric AS pop,
        ROW_NUMBER() OVER (PARTITION BY dv_lnk.hk_dhis2_org_unit ORDER BY per.start_date DESC) AS rn
    FROM {{ source('nhdw_vault_raw', 'lnk_dhis2_data_value') }} dv_lnk
    JOIN pop_de USING (hk_dhis2_data_element)
    JOIN {{ source('nhdw_vault_business', 'v_sat_dhis2_data_value_current') }} dv_sat USING (hk_dhis2_data_value)
    JOIN {{ source('nhdw_vault_business', 'sat_dhis2_period_resolved') }} per USING (hk_dhis2_period)
    WHERE dv_sat.value IS NOT NULL
),
region_pop AS (
    SELECT rm.region_id, SUM(pl.pop) AS population
    FROM pop_latest pl
    JOIN ou USING (hk_dhis2_org_unit)
    JOIN region_map rm USING (region_ou_uid)
    WHERE pl.rn = 1
    GROUP BY rm.region_id
)
SELECT
    rs.region_id,
    rs.phys::bigint      AS phys,
    rs.nurse::bigint     AS nurse,
    rs.hew::bigint       AS hew,
    rs.hcw_total::bigint AS hcw_total,
    rp.population::bigint AS population,
    CASE WHEN rp.population > 0
         THEN ROUND(rs.hcw_total / (rp.population / 10000.0), 2)
         ELSE NULL END AS density_per_10k
FROM region_staff rs
LEFT JOIN region_pop rp USING (region_id)
