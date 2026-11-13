-- Cascade funnels must be (near-)monotone: each step's value may not exceed
-- the previous step's within a (region by programme by year) funnel. Guards
-- against a percentage element summed as a count. 5% headroom tolerates
-- seeded reporting noise at
-- small region-year grains; a unit-mixing bug overshoots by whole multiples.
SELECT *
FROM (
    SELECT
        region_id, programme_id, year_id, step_order, step_indicator, value_total,
        LAG(value_total) OVER (
            PARTITION BY region_id, programme_id, year_id
            ORDER BY step_order) AS prev_value
    FROM {{ ref('mart_programme_cascade') }}
) steps
WHERE prev_value IS NOT NULL
  AND value_total > prev_value * 1.05
