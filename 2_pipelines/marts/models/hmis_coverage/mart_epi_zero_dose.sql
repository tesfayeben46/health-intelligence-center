/*
    mart_epi_zero_dose: where are the unreached children, and can the
    vaccine supply chain reach them? (region by year)

    Three feeds that only a warehouse can hold together:

      · DHIS2 immunization: PENTA1 doses given (the zero-dose standard
        proxy is children who never received DTP-containing dose 1);
      - DHIS2 population: POP-TOTAL per woreda by year, expected infants =
        population * 3.2% (Ethiopia CBR, a labelled estimate);
      · mBrana cold-chain: share of USSD stock logs at zero in the last 90
        days per region (last-mile vaccine availability stress).

    Comparative index, not absolute coverage: the seeded HMIS service volumes
    are at demonstration scale while POP-TOTAL is population-scale, so an
    absolute zero-dose count would read close to 100% everywhere. reach_index
    (annualized Penta-1 doses per 1,000 expected infants) compares regions
    fairly: the zero-dose question is "where are the unreached children", and
    the lowest-reach regions answer it. hotspot = bottom-tercile reach and
    mBrana zero-stock share ≥ 15%. Partial years are annualized by observed
    months. Backs sv-zero-dose.
*/
WITH pop AS (
    SELECT
        SUBSTRING(ou.code FROM '^WOR-([A-Z]{2})-') AS region_id,
        EXTRACT(YEAR FROM per.start_date)::int     AS year_id,
        SUM(dv.value::numeric)                     AS population
    FROM {{ source('nhdw_vault_raw', 'lnk_dhis2_data_value') }} l
    JOIN {{ source('nhdw_vault_business', 'v_sat_dhis2_data_value_current') }} dv USING (hk_dhis2_data_value)
    JOIN {{ source('nhdw_vault_business', 'v_sat_dhis2_data_element_current') }} de
         ON de.hk_dhis2_data_element = l.hk_dhis2_data_element
    JOIN {{ source('nhdw_vault_business', 'v_sat_dhis2_org_unit_current') }} ou
         ON ou.hk_dhis2_org_unit = l.hk_dhis2_org_unit
    JOIN {{ source('nhdw_vault_business', 'sat_dhis2_period_resolved') }} per USING (hk_dhis2_period)
    WHERE de.code = 'POP-TOTAL' AND per.period_type = 'yearly'
      AND ou.code LIKE 'WOR-%'
    GROUP BY 1, 2
),
penta1 AS (
    -- annualized: a half-observed year must not read as half the reach
    SELECT
        region_id,
        SUBSTRING(month_id FROM 1 FOR 4)::int AS year_id,
        SUM(total_value) AS penta1_doses_raw,
        ROUND(SUM(total_value) * 12.0 / COUNT(DISTINCT month_id)) AS penta1_doses_annualized
    FROM {{ ref('mart_hmis_indicator_coverage') }}
    WHERE indicator_id = 'PENTA1'
    GROUP BY 1, 2
),
mbrana AS (
    -- last-mile vaccine availability stress: share of mBrana USSD stock logs
    -- at zero in the trailing 90 days of the feed, per region (region rides
    -- the canonical facility code FAC-<REGION>-…).
    SELECT
        SUBSTRING(f.institution_name FROM '^FAC-([A-Z]{2})-') AS region_id,
        ROUND(100.0 * COUNT(*) FILTER (WHERE s.quantity::numeric <= 0)
              / NULLIF(COUNT(*), 0), 1) AS zero_stock_share_pct,
        MAX(s.date::date) AS last_log_date
    FROM {{ source('nhdw_vault_raw', 'lnk_mbrana_stock_event') }} l
    JOIN {{ source('nhdw_vault_business', 'v_sat_mbrana_stock_event_current') }} s USING (hk_mbrana_stock_event)
    JOIN {{ source('nhdw_vault_business', 'v_sat_mbrana_ussd_facility_current') }} f USING (hk_mbrana_ussd_facility)
    WHERE s.date::date >= (SELECT MAX(s2.date::date) FROM {{ source('nhdw_vault_business', 'v_sat_mbrana_stock_event_current') }} s2) - INTERVAL '90 days'
    GROUP BY 1
)
SELECT
    pop.region_id,
    pop.year_id,
    ROUND(pop.population)              AS population,
    ROUND(pop.population * 0.032)      AS expected_infants,
    ROUND(COALESCE(p.penta1_doses_raw, 0))        AS penta1_doses,
    ROUND(COALESCE(p.penta1_doses_annualized, 0)) AS penta1_annualized,
    CASE WHEN pop.population > 0
         THEN ROUND(COALESCE(p.penta1_doses_annualized, 0)
                    / (pop.population * 0.032) * 1000, 1)
         ELSE NULL END AS reach_index,
    mb.zero_stock_share_pct AS mbrana_zero_stock_pct,
    mb.last_log_date        AS mbrana_last_log,
    (CASE WHEN pop.population > 0
          AND (COALESCE(p.penta1_doses_annualized, 0) / (pop.population * 0.032) * 1000)
              <= (SELECT PERCENTILE_CONT(0.33) WITHIN GROUP (ORDER BY
                    COALESCE(p2.penta1_doses_annualized, 0) / NULLIF(pop2.population * 0.032, 0) * 1000)
                  FROM pop pop2
                  LEFT JOIN penta1 p2 USING (region_id, year_id)
                  WHERE pop2.year_id = pop.year_id)
          AND COALESCE(mb.zero_stock_share_pct, 0) >= 15
     THEN true ELSE false END) AS hotspot
FROM pop
LEFT JOIN penta1 p USING (region_id, year_id)
LEFT JOIN mbrana mb USING (region_id)
