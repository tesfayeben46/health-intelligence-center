/*
    mart_supply_stockout_public: national-level commodity stock
    availability for the Public dashboard.

    A facility is counted as "stocked" if at least one of its tracked
    commodities has projected_days_remaining >= 30 at the latest snapshot.
    Stocked-pct = stocked facilities / total facilities with any
    commodity record.

    Aggregated to the national level only: the HCMIS environment hub has no
    satellite carrying region/woreda metadata in the vault, so there is no
    sound way to break the result down by region. A
    `lnk_hcmis_environment_facility` link with a `facility -> region` bridge
    would allow per-region rows.

    k-anonymity: aggregate is suppressed if fewer than 5 facilities
    contribute, matching the rule used by mart_public_summary.

    Consumed by: 6_access-points/dashboards/public/  (stock-availability
    series, public/app/marts.py::stock_availability).
*/
{{ config(materialized='table') }}

WITH per_env AS (
    SELECT
        hk_hcmis_environment,
        BOOL_OR(projected_days_remaining >= 30) AS is_stocked
    FROM {{ ref('mart_supply_stockout_forecast') }}
    WHERE projected_days_remaining IS NOT NULL
    GROUP BY hk_hcmis_environment
),
rollup AS (
    SELECT
        COUNT(*)                                                AS n_facilities,
        SUM(CASE WHEN is_stocked THEN 1 ELSE 0 END)             AS n_stocked
    FROM per_env
)
SELECT
    'National'::text                                            AS region_name,
    n_facilities,
    n_stocked,
    CASE WHEN n_facilities >= 5
         THEN ROUND(100.0 * n_stocked::numeric / n_facilities, 1)::numeric(6,1)
         ELSE NULL::numeric(6,1) END                            AS stocked_pct,
    NOW()::timestamp                                            AS computed_at
FROM rollup
WHERE n_facilities >= 5
