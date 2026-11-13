/*
    mart_public_data_quality: public Open-Data projection of mart_data_quality
    at source by period by region grain (woreda dropped to enlarge groups).

    A transparency scorecard: reporting completeness, timeliness and validity
    by source system, period and region across the six national data feeds
    (DHIS2, HCMIS, mBrana, EthioEMR, MFR, IDSR/EWARS). The eLMIS historical
    archive is not published here (see the WHERE clause). A
    dimension stays NULL where the source carries no reliable signal for it,
    as in the internal mart. k-anonymity: a cell is published only when it
    aggregates >= 5 received reports.

    Materialised as a TABLE so Trino's PG connector introspects the aggregate
    columns reliably. Lives under data_quality/public/ so it picks up
    +schema: bi_public from dbt_project.yml.

    Consumed by 6_access-points/dashboards/public/ (Open Data catalogue).
*/
{{ config(materialized='table') }}

SELECT
    source,
    period_id,
    region_id,
    ROUND(AVG(completeness_pct), 1)  AS completeness_pct,
    ROUND(AVG(timeliness_pct), 1)    AS timeliness_pct,
    ROUND(AVG(validity_pct), 1)      AS validity_pct,
    SUM(reports_received)::bigint    AS reports_received
FROM {{ ref('mart_data_quality') }}
WHERE period_id IS NOT NULL
  -- eLMIS-hist is excluded. It is a foreign (Zambian) historical
  -- archive carried for supply-chain feature work; the data dictionary marks
  -- it internal-dashboards-only, and it never joins the entity model.
  -- Publishing it on Ethiopia's national transparency scorecard would present
  -- another country's reporting record as this country's.
  AND source <> 'eLMIS-hist'
GROUP BY 1, 2, 3
HAVING SUM(reports_received) >= 5
ORDER BY source, period_id, region_id
