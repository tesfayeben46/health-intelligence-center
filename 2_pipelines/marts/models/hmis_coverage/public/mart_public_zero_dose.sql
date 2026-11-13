/*
    mart_public_zero_dose: public Open-Data projection of mart_epi_zero_dose
    at region by year grain.

    "Where are the unreached children?" Penta-1 immunization reach index
    (annualized doses per 1,000 expected infants), expected-infant estimates
    and last-mile cold-chain stress by region and year, with equity hotspots
    flagged. Already a region by year aggregate of population-scale counts, so
    no individual is re-identifiable; the HAVING guard only drops degenerate
    (< 5 population) rows. reach_index is a comparative index, not absolute
    coverage (see the internal mart's header).

    Materialised as a TABLE so Trino's PG connector introspects the aggregate
    columns reliably. Lives under hmis_coverage/public/ so it picks up
    +schema: bi_public from dbt_project.yml.

    Consumed by 6_access-points/dashboards/public/ (Open Data catalogue).
*/
{{ config(materialized='table') }}

SELECT
    region_id,
    year_id,
    population::bigint          AS population,
    expected_infants::bigint    AS expected_infants,
    penta1_annualized::bigint   AS penta1_doses_annualized,
    reach_index,
    mbrana_zero_stock_pct,
    hotspot
FROM {{ ref('mart_epi_zero_dose') }}
WHERE population >= 5
ORDER BY region_id, year_id
