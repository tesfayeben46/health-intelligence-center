-- Densification sanity: mart_ml_disease_input must be the full grid:
-- row_count == (surveillance woredas) * (priority diseases) * (ISO weeks of
-- the observed span). A shortfall means zero weeks are being dropped
-- (the trap this mart exists to fix); an excess means grid fan-out
-- (e.g. duplicate org-unit satellite versions).
--
-- Returns rows when the test FAILS (dbt convention).

WITH actual AS (
    SELECT COUNT(*) AS n FROM {{ ref('mart_ml_disease_input') }}
),
woredas AS (
    SELECT COUNT(DISTINCT woreda_uid) AS n FROM {{ ref('mart_ml_disease_input') }}
),
diseases AS (
    SELECT COUNT(DISTINCT disease_code) AS n FROM {{ ref('mart_ml_disease_input') }}
),
weeks AS (
    SELECT COUNT(DISTINCT week_start) AS n FROM {{ ref('mart_ml_disease_input') }}
)
SELECT a.n AS row_count,
       w.n AS woredas, d.n AS diseases, k.n AS weeks,
       w.n * d.n * k.n AS expected
FROM actual a, woredas w, diseases d, weeks k
WHERE a.n <> w.n * d.n * k.n
