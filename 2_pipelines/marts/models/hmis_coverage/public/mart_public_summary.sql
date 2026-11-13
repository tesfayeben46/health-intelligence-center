/*
    mart_public_summary: public-dashboard projection.
    Region by month by programme rollup of mart_hmis_indicator_coverage,
    with k-anonymity suppression: any cell with < 5 reporting facilities
    is NULLed out. The table is rebuilt from the upstream mart on each
    marts dbt run.

    Consumed by: Public dashboard regional coverage map + national trends.
*/
{{ config(materialized='table') }}

-- Materialised as a table (not a view) so Trino's PG connector picks up
-- the numeric column types reliably. Refreshed on each marts dbt run.
SELECT
    region_id,
    programme_id,
    month_id,
    SUM(facilities_reporting)::bigint              AS facilities_reporting,
    SUM(facilities_expected)::bigint               AS facilities_expected,
    CASE WHEN SUM(facilities_reporting) >= 5
         THEN ROUND(100.0 * SUM(facilities_reporting)::numeric(20,4)
                          / NULLIF(SUM(facilities_expected)::numeric(20,4), 0), 1)::numeric(6,1)
         ELSE NULL::numeric(6,1) END               AS coverage_pct,
    CASE WHEN SUM(facilities_reporting) >= 5
         THEN ROUND(SUM(total_value)::numeric(20,4), 1)::numeric(20,1)
         ELSE NULL::numeric(20,1) END              AS total_value
FROM {{ ref('mart_hmis_indicator_coverage') }}
GROUP BY 1,2,3
HAVING SUM(facilities_reporting) >= 5
