-- Integration test: vault load-loss guard.
--
-- Asserts that the vault link table has at least 95% of the distinct
-- fact-grain rows from landing. If the vault has fewer, a load-loss bug
-- has silently dropped data (most commonly a hash collision or a bad
-- incremental WHERE clause).
--
-- Uses raw schema references (not source()) because the vault tables
-- aren't declared as marts sources; they are upstream artifacts.
--
-- Returns rows when the test FAILS (dbt convention).

WITH landing_n AS (
    -- The composite natural key for a DHIS2 data value is:
    --   (data_element, period, org_unit, category_option_combo,
    --    attribute_option_combo, data_set)
    -- Multiple staging rows can share that key (re-reported values);
    -- the vault link dedupes on the composite hash.
    SELECT COUNT(DISTINCT (
        data_element, period, org_unit,
        category_option_combo, attribute_option_combo, data_set
    )) AS n
    FROM nhdw_landing.stg_dhis2__data_value_sets
),
vault_n AS (
    SELECT COUNT(*) AS n FROM nhdw_vault_raw.lnk_dhis2_data_value
)
SELECT 'lnk_dhis2_data_value' AS link, l.n AS landing_n, v.n AS vault_n
FROM landing_n l, vault_n v
WHERE v.n < l.n * 0.95  -- allow 5% slack for natural dedupe
