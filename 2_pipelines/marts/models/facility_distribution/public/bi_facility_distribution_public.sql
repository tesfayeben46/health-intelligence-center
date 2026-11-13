/*
    bi_public.bi_facility_distribution_public: k-anonymised projection
    for the Public Dashboard. Counts collapsed across facility level so
    groups are bigger; any region with fewer than 5 facilities is
    suppressed.

    Materialised as a TABLE (not a view) so Trino's PG connector
    introspects the aggregate column `n_facilities` correctly. The PG
    connector reliably exposes physical-table columns; on views it
    sometimes drops derived/aggregate projections.

    The model lives under facility_distribution/public/ so it picks up
    +schema: bi_public from dbt_project.yml.

    Consumed by 6_access-points/dashboards/public/.
*/
{{ config(materialized='table') }}

SELECT
    region_id,
    region_name,
    SUM(n_facilities)::bigint                                       AS n_facilities
FROM {{ ref('bi_facility_distribution') }}
GROUP BY region_id, region_name
HAVING SUM(n_facilities) >= 5
ORDER BY region_name
