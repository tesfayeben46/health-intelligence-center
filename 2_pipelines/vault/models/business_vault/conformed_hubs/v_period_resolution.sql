/*
    v_period_resolution: period (calendar) hub resolution, and the
    AutomateDV stage for hub_period_conformed.
    Sources:
      - DHIS2 periods: hub_dhis2_period.period is a DHIS2 period code
        (e.g. '202607' for monthly; the natural key lives on the hub, no
        sat carries it). We project monthly periods as YYYY-MM.
      - HCMIS dim_date (current-record view): full_date in YYYY-MM-DD
        form; projected as daily plus the monthly roll-up.

    period_grain ∈ {monthly, daily}. BK = (period_id, period_grain);
    hk_period_conformed = hash(period_id, period_grain) is pre-computed
    here per the AutomateDV stage contract. Dedupe to first-seen load_ts
    is the hub macro's job, not this view's.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

WITH dhis2_monthly AS (
    -- DHIS2 monthly period = exactly 6 digits: YYYYMM. Filter to monthly.
    SELECT DISTINCT
        SUBSTRING(h.period::text FROM 1 FOR 4) || '-' || SUBSTRING(h.period::text FROM 5 FOR 2) AS period_id,
        'monthly' AS period_grain,
        h.load_ts
    FROM {{ ref('hub_dhis2_period') }} h
    WHERE h.period::text ~ '^[0-9]{6}$'
),
hcmis_daily AS (
    SELECT DISTINCT
        sat.full_date::date::text AS period_id,
        'daily' AS period_grain,
        sat.load_ts
    FROM {{ ref('v_sat_hcmis_date_current') }} sat
    WHERE sat.full_date IS NOT NULL
),
hcmis_monthly AS (
    SELECT DISTINCT
        TO_CHAR(sat.full_date::date, 'YYYY-MM') AS period_id,
        'monthly' AS period_grain,
        sat.load_ts
    FROM {{ ref('v_sat_hcmis_date_current') }} sat
    WHERE sat.full_date IS NOT NULL
),
unioned AS (
    SELECT * FROM dhis2_monthly
    UNION SELECT * FROM hcmis_monthly
    UNION SELECT * FROM hcmis_daily
)
SELECT
    unioned.*,
    {{ automate_dv.hash(columns=['period_id', 'period_grain'],
                        alias='hk_period_conformed') }},
    'BV.period_resolution' AS record_source
FROM unioned
