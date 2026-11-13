/*
    mart_stockout_service_impact: what stockouts cost in services, per
    (region by commodity).

    The missed-service estimate needs a commodity -> service linkage and that
    service's regional volume, from two different source systems (HCMIS supply,
    DHIS2 HMIS), joined here through the commodity_reference seed's `enables_indicator`:
    here through the commodity_reference seed's `enables_indicator`:

        est_missed_services ≈ (regional monthly service volume / 30)
                              * stockout days_out (12-month window)

    Commodities with no clean service linkage (empty enables: ORS,
    paracetamol, LLIN campaigns, insulin…) keep est_missed_services NULL:
    the estimate is derived or absent, never guessed. It is an estimate, and
    consumers must label it so. facilities_affected stays out entirely
    (supply has no facility grain).
*/
WITH events AS (
    -- stockout days per (region, commodity), episodes starting in the last
    -- 12 months (matches the fetcher's period framing "ytd-ish window")
    SELECT
        region_id,
        commodity_name,
        SUM(days_out) AS days_out_12m
    FROM {{ ref('mart_supply_stockout_events') }}
    WHERE episode_start_month >= TO_CHAR(CURRENT_DATE - INTERVAL '12 months', 'YYYY-MM')
    GROUP BY 1, 2
),
volume AS (
    -- avg monthly regional service volume per enabled indicator, last 12 months
    SELECT
        region_id,
        indicator_id,
        AVG(month_total) AS monthly_volume
    FROM (
        SELECT region_id, indicator_id, month_id, SUM(total_value) AS month_total
        FROM {{ ref('mart_hmis_indicator_coverage') }}
        WHERE month_id >= TO_CHAR(CURRENT_DATE - INTERVAL '12 months', 'YYYY-MM')
        GROUP BY 1, 2, 3
    ) m
    GROUP BY 1, 2
)
SELECT
    e.region_id,
    e.commodity_name,
    cr.commodity_id,
    cr.programme_id,
    cr.enables_indicator,
    e.days_out_12m,
    ROUND(v.monthly_volume) AS monthly_service_volume,
    CASE WHEN v.monthly_volume IS NOT NULL
         THEN ROUND(v.monthly_volume / 30.0 * e.days_out_12m)
         ELSE NULL END AS est_missed_services,
    CASE WHEN v.monthly_volume IS NOT NULL AND v.monthly_volume > 0
         THEN ROUND(100.0 * (v.monthly_volume / 30.0 * e.days_out_12m)
                    / (v.monthly_volume * 12), 1)
         ELSE NULL END AS service_disruption_pct
FROM events e
LEFT JOIN {{ ref('commodity_reference') }} cr
       ON cr.commodity_name = e.commodity_name
LEFT JOIN volume v
       ON v.region_id = e.region_id
      AND v.indicator_id = cr.enables_indicator
