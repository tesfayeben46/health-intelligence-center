/*
    mart_supply_outbreak_response ("Act this week"): live outbreak pressure
    crossed with response-commodity stock, per (region, disease, commodity).

    The cross-source join: IDSR surveillance (DHIS2 weekly) and EPSS
    hub stock (HCMIS, catchment-blended) exist in different source systems;
    only a national warehouse can put "measles is surging in Gambela" next to
    "the MR vaccine covering Gambela has 0.4 months of stock" in one row.

      · outbreak signal: last 4 ISO weeks of new cases per (region, disease)
        vs the prior 4 weeks (trend_ratio > 1 = growing);
      · response mapping: disease_response_map seed (generated from
        entity-model.yaml; diseases with no tracked countermeasure appear
        with commodity NULL, never faked);
      · stock: latest-month catchment-blended mos per (region, commodity)
        from mart_supply_region_stock;
      · act_score: cases_4w weighted by supply severity, the widget's
        ranking key (a big outbreak with a stocked-out treatment outranks a
        bigger one with healthy stock).

    Backs rm-act-week (Resource hero). Consumers must treat commodity_name
    NULL as "no tracked countermeasure", not as missing data.
*/
WITH latest AS (
    SELECT MAX(week_start) AS wk FROM {{ ref('mart_idsr_disease_weekly') }}
),
sig AS (
    SELECT
        w.region_id,
        w.disease_code,
        MAX(w.disease_label) AS disease_label,
        SUM(w.new_cases) FILTER (
            WHERE w.week_start >  latest.wk - INTERVAL '28 days') AS cases_4w,
        SUM(w.new_cases) FILTER (
            WHERE w.week_start <= latest.wk - INTERVAL '28 days'
              AND w.week_start >  latest.wk - INTERVAL '56 days') AS cases_prior_4w,
        SUM(w.deaths) FILTER (
            WHERE w.week_start >  latest.wk - INTERVAL '28 days') AS deaths_4w
    FROM {{ ref('mart_idsr_disease_weekly') }} w
    CROSS JOIN latest
    GROUP BY 1, 2
    HAVING SUM(w.new_cases) FILTER (
        WHERE w.week_start > latest.wk - INTERVAL '28 days') > 0
),
stock AS (
    SELECT s.region_id, s.commodity_name, s.mos
    FROM {{ ref('mart_supply_region_stock') }} s
    WHERE s.month_id = (SELECT MAX(month_id) FROM {{ ref('mart_supply_region_stock') }})
),
joined AS (
    SELECT
        sig.region_id,
        sig.disease_code,
        sig.disease_label,
        sig.cases_4w,
        sig.cases_prior_4w,
        sig.deaths_4w,
        CASE WHEN COALESCE(sig.cases_prior_4w, 0) > 0
             THEN ROUND(sig.cases_4w::numeric / sig.cases_prior_4w, 2)
             ELSE NULL END AS trend_ratio,
        m.commodity_id,
        m.commodity_name,
        cr.programme_id,
        m.role AS commodity_role,
        st.mos,
        CASE
            WHEN m.commodity_id IS NULL THEN 'no_countermeasure'
            WHEN st.mos IS NULL          THEN 'no_stock_data'
            WHEN st.mos <= 0             THEN 'stockout'
            WHEN st.mos < 1              THEN 'critical'
            WHEN st.mos < 3              THEN 'tight'
            ELSE 'ok'
        END AS supply_status
    FROM sig
    LEFT JOIN {{ ref('disease_response_map') }} m USING (disease_code)
    LEFT JOIN {{ ref('commodity_reference') }} cr ON cr.commodity_id = m.commodity_id
    LEFT JOIN stock st
        ON st.region_id = sig.region_id
       AND st.commodity_name = m.commodity_name
)
SELECT
    *,
    -- ranking key: outbreak size by supply severity. Unmapped/unknown sit at
    -- 0.5 (worth a look: readiness cannot be verified), healthy stock at 0.25.
    ROUND(cases_4w * CASE supply_status
        WHEN 'stockout'          THEN 3.0
        WHEN 'critical'          THEN 2.0
        WHEN 'tight'             THEN 1.0
        WHEN 'no_countermeasure' THEN 0.5
        WHEN 'no_stock_data'     THEN 0.5
        ELSE 0.25 END, 1) AS act_score
FROM joined
