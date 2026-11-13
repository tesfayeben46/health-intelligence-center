/*
    mart_public_workforce: public Open-Data projection of
    mart_health_workforce at region grain.

    Health-worker counts by cadre (physicians, nurses, health extension
    workers) with WHO-comparable density per 10,000 population, per region,
    from the Master Facility Registry joined to population. Already a region
    aggregate of thousands of workers, so no individual is re-identifiable; the
    HAVING guard only drops regions with a degenerate (< 5) total.

    Materialised as a TABLE so Trino's PG connector introspects the aggregate
    columns reliably. Lives under facility_distribution/public/ so it picks up
    +schema: bi_public from dbt_project.yml.

    Consumed by 6_access-points/dashboards/public/ (Open Data catalogue).
*/
{{ config(materialized='table') }}

SELECT
    region_id,
    phys::bigint          AS physicians,
    nurse::bigint         AS nurses,
    hew::bigint           AS health_extension_workers,
    hcw_total::bigint     AS health_workers_total,
    population::bigint    AS population,
    density_per_10k       AS hcw_density_per_10k
FROM {{ ref('mart_health_workforce') }}
WHERE hcw_total >= 5
ORDER BY region_id
