-- K-anonymity contract: every group in a `bi_public.*` mart must
-- represent at least 5 facilities. Public marts enforce this in their
-- SQL via `HAVING ... >= 5`; this test catches regressions where a
-- future model forgets the gate.
--
-- Returns rows when the test FAILS (any group with n < 5 published).

SELECT 'bi_facility_distribution_public' AS mart, region_name AS group_key, n_facilities
FROM {{ ref('bi_facility_distribution_public') }}
WHERE n_facilities < 5

UNION ALL

SELECT 'mart_supply_stockout_public' AS mart, region_name AS group_key, n_facilities
FROM {{ ref('mart_supply_stockout_public') }}
WHERE n_facilities < 5

UNION ALL

SELECT 'mart_public_summary' AS mart, region_id AS group_key, facilities_reporting AS n_facilities
FROM {{ ref('mart_public_summary') }}
WHERE facilities_reporting < 5

UNION ALL

SELECT 'mart_public_indicator_kpis' AS mart, region_id AS group_key, facilities_reporting AS n_facilities
FROM {{ ref('mart_public_indicator_kpis') }}
WHERE facilities_reporting < 5
