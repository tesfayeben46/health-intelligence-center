/*
    mart_malaria_kpi: the malaria KPI set (region by month).

    KPIs: suspected cases; tested by microscopy; tested by RDT;
    test-positivity rate; confirmed cases; confirmed per 1,000 person-years;
    inpatient malaria deaths; ITN coverage; IRS coverage. All from DHIS2
    HMIS elements seeded (MAL-SUSP/MICRO/RDT/CONF/DEATH counts +
    MAL-ITN-COV/MAL-IRS-COV coverage), joined to POP-TOTAL for the
    population-denominated rate.

      · counts (suspected/micro/rdt/confirmed/deaths): region-month sums;
      · test_positivity_pct = confirmed / (microscopy + RDT tests), clamped
        0..100 (split by test modality);
      - confirmed_per_1000 = confirmed / region population * 1,000 (per
        1,000 person-years at monthly grain * 12);
      · itn/irs coverage: mean of the reported coverage element.

    Coverage elements are reported at aggregate grain, so their monthly
    regional mean is the appropriate read. Backs dz-malaria-kpi (a multi-line
    monthly chart).
*/
WITH counts AS (
    SELECT
        region_id,
        month_id,
        SUM(total_value) FILTER (WHERE indicator_id = 'MAL-SUSP')  AS suspected,
        SUM(total_value) FILTER (WHERE indicator_id = 'MAL-MICRO') AS micro_tests,
        SUM(total_value) FILTER (WHERE indicator_id = 'MAL-RDT')   AS rdt_tests,
        SUM(total_value) FILTER (WHERE indicator_id = 'MAL-CONF')  AS confirmed,
        SUM(total_value) FILTER (WHERE indicator_id = 'MAL-TX-AL') AS treated,
        SUM(total_value) FILTER (WHERE indicator_id = 'MAL-DEATH') AS inpatient_deaths,
        AVG(mean_value)  FILTER (WHERE indicator_id = 'MAL-ITN-COV') AS itn_coverage_pct,
        AVG(mean_value)  FILTER (WHERE indicator_id = 'MAL-IRS-COV') AS irs_coverage_pct
    FROM {{ ref('mart_hmis_indicator_coverage') }}
    WHERE indicator_id LIKE 'MAL-%'
    GROUP BY 1, 2
),
pop AS (
    -- region population from the ANNUAL_POPULATION dataset (latest year)
    SELECT
        SUBSTRING(ou.code FROM '^WOR-([A-Z]{2})-') AS region_id,
        SUM(dv.value::numeric) AS population
    FROM {{ source('nhdw_vault_raw', 'lnk_dhis2_data_value') }} l
    JOIN {{ source('nhdw_vault_business', 'v_sat_dhis2_data_value_current') }} dv USING (hk_dhis2_data_value)
    JOIN {{ source('nhdw_vault_business', 'v_sat_dhis2_data_element_current') }} de
         ON de.hk_dhis2_data_element = l.hk_dhis2_data_element
    JOIN {{ source('nhdw_vault_business', 'v_sat_dhis2_org_unit_current') }} ou
         ON ou.hk_dhis2_org_unit = l.hk_dhis2_org_unit
    JOIN {{ source('nhdw_vault_business', 'sat_dhis2_period_resolved') }} per USING (hk_dhis2_period)
    WHERE de.code = 'POP-TOTAL' AND per.period_type = 'yearly' AND ou.code LIKE 'WOR-%'
      AND per.start_date = (SELECT MAX(p2.start_date)
                            FROM {{ source('nhdw_vault_business', 'sat_dhis2_period_resolved') }} p2
                            WHERE p2.period_type = 'yearly')
    GROUP BY 1
)
SELECT
    c.region_id,
    c.month_id,
    ROUND(c.suspected)        AS suspected,
    ROUND(c.micro_tests)      AS micro_tests,
    ROUND(c.rdt_tests)        AS rdt_tests,
    ROUND(c.confirmed)        AS confirmed,
    ROUND(c.treated)          AS treated,
    ROUND(c.inpatient_deaths) AS inpatient_deaths,
    CASE WHEN COALESCE(c.micro_tests, 0) + COALESCE(c.rdt_tests, 0) > 0
         THEN LEAST(100.0, ROUND(100.0 * c.confirmed
              / (COALESCE(c.micro_tests, 0) + COALESCE(c.rdt_tests, 0)), 1))
         ELSE NULL END AS test_positivity_pct,
    CASE WHEN pop.population > 0
         THEN ROUND(1000.0 * c.confirmed / pop.population, 3)
         ELSE NULL END AS confirmed_per_1000,
    ROUND(c.itn_coverage_pct, 1) AS itn_coverage_pct,
    ROUND(c.irs_coverage_pct, 1) AS irs_coverage_pct
FROM counts c
LEFT JOIN pop USING (region_id)
