/*
    dim_period: thin dimension view over the conformed period hub.
    One row per (period_id, period_grain). Calendar attributes come from
    sat_dhis2_period_resolved (the 6-format DHIS2 period decoder) where a
    DHIS2 period code exists; periods contributed only by other sources
    (HCMIS dim_date) fall back to bounds derived from the period_id
    itself: same values, different provenance.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

WITH dhis2_monthly AS (
    SELECT
        SUBSTRING(p.period::text FROM 1 FOR 4) || '-'
            || SUBSTRING(p.period::text FROM 5 FOR 2) AS period_id,
        sat.start_date,
        sat.end_date,
        sat.days_in_period,
        sat.is_complete
    FROM {{ ref('sat_dhis2_period_resolved') }} sat
    JOIN {{ ref('hub_dhis2_period') }} p USING (hk_dhis2_period)
    WHERE sat.period_type = 'monthly'
),
resolved AS (
    SELECT DISTINCT ON (period_id) *
    FROM dhis2_monthly
    ORDER BY period_id
),
base AS (
    SELECT
        h.hk_period_conformed,
        h.period_id,
        h.period_grain,
        h.load_ts,
        CASE h.period_grain
            WHEN 'daily' THEN h.period_id::date
            ELSE TO_DATE(h.period_id || '-01', 'YYYY-MM-DD')
        END AS fb_start,
        CASE h.period_grain
            WHEN 'daily' THEN h.period_id::date
            ELSE (TO_DATE(h.period_id || '-01', 'YYYY-MM-DD')
                  + INTERVAL '1 month - 1 day')::date
        END AS fb_end
    FROM {{ ref('hub_period_conformed') }} h
)
SELECT
    b.hk_period_conformed,
    b.period_id,
    b.period_grain,
    COALESCE(r.start_date,     b.fb_start)                  AS start_date,
    COALESCE(r.end_date,       b.fb_end)                    AS end_date,
    COALESCE(r.days_in_period, b.fb_end - b.fb_start + 1)   AS days_in_period,
    COALESCE(r.is_complete,    b.fb_end < CURRENT_DATE)     AS is_complete,
    b.load_ts
FROM base b
LEFT JOIN resolved r
       ON b.period_grain = 'monthly' AND r.period_id = b.period_id
