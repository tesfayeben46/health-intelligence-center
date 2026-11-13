/*
    HCMIS date dimension: Gregorian + Ethiopian calendar both surfaced.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT date_key, full_date, day_of_week, day_name_of_week, day_of_month, day_of_year,
           weekday_weekend, week_of_year, month_name, month_of_year, calendar_quarter,
           calendar_year, fiscal_year, fiscal_quarter, fiscal_month_of_year,
           eth_year, eth_month, eth_day_of_month, eth_day_name_of_week, eth_month_name,
           eth_full_date, is_holiday, is_last_day_of_month
    FROM {{ source('raw', 'hcmiswdm__dim_date') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['date_key','full_date','day_of_week','day_name_of_week','day_of_month','day_of_year','weekday_weekend','week_of_year','month_name','month_of_year','calendar_quarter','calendar_year','fiscal_year','fiscal_quarter','fiscal_month_of_year','eth_year','eth_month','eth_day_of_month','eth_day_name_of_week','eth_month_name','eth_full_date','is_holiday','is_last_day_of_month']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY date_key ORDER BY full_date DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT date_key, full_date, day_of_week, day_name_of_week, day_of_month, day_of_year,
       weekday_weekend, week_of_year, month_name, month_of_year, calendar_quarter,
       calendar_year, fiscal_year, fiscal_quarter, fiscal_month_of_year,
       eth_year, eth_month, eth_day_of_month, eth_day_name_of_week, eth_month_name,
       eth_full_date, is_holiday, is_last_day_of_month,
       now64() AS load_ts,
       source_record_hash
FROM deduped WHERE rn = 1
