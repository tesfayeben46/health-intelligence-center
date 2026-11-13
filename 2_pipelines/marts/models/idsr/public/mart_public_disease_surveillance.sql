/*
    mart_public_disease_surveillance: public Open-Data projection of
    mart_idsr_disease_weekly at region by disease by epi-week grain.

    Ethiopia's IDSR (Integrated Disease Surveillance & Response) weekly
    confirmed cases + deaths for priority notifiable diseases, rolled up from
    the woreda grain to region to enlarge groups. k-anonymity: a region by 
    disease by week cell is only published when it carries >= 5 combined events
    (cases + deaths); tiny cells are suppressed; woredas_reporting is carried
    as informative metadata. Aggregate weekly regional counts are the standard public epidemiological
    open-data unit (cf. WHO/Africa CDC weekly bulletins).

    Materialised as a TABLE (not a view) so Trino's PG connector introspects
    the aggregate columns reliably. Lives under idsr/public/ so it picks up
    +schema: bi_public from dbt_project.yml.

    Consumed by 6_access-points/dashboards/public/ (Open Data catalogue).
*/
{{ config(materialized='table') }}

SELECT
    iso_week,
    week_start,
    region_id,
    programme_id,
    disease_code,
    disease_label,
    SUM(new_cases)::bigint              AS new_cases,
    SUM(deaths)::bigint                 AS deaths,
    COUNT(DISTINCT woreda_uid)::bigint  AS woredas_reporting
FROM {{ ref('mart_idsr_disease_weekly') }}
GROUP BY 1, 2, 3, 4, 5, 6
HAVING (SUM(new_cases) + SUM(deaths)) >= 5
ORDER BY week_start, region_id, disease_code
