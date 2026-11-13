/*
    sat_date_eth_calendar: Ethiopian-calendar overlay for the conformed
    date hub. Pulls EC fields straight from HCMIS dim_date (the only source
    that already carries them). Dates absent from HCMIS dim_date carry no EC
    fields.
*/
{{ config(
    materialized='incremental',
    on_schema_change='append_new_columns',
    schema='nhdw_vault_business'
) }}

WITH src AS (
    SELECT
        h.hk_date_conformed,
        sat.full_date,
        sat.calendar_year,
        sat.calendar_quarter,
        sat.month_of_year      AS gregorian_month,
        sat.month_name         AS gregorian_month_name,
        sat.day_of_month       AS gregorian_day,
        sat.day_name_of_week   AS gregorian_day_name,
        sat.week_of_year       AS gregorian_iso_week,
        sat.fiscal_year,
        sat.fiscal_quarter,
        sat.fiscal_month_of_year,
        sat.eth_year,
        sat.eth_month,
        sat.eth_day_of_month,
        sat.eth_day_name_of_week,
        sat.eth_month_name,
        sat.eth_full_date,
        sat.is_holiday,
        sat.is_last_day_of_month,
        sat.load_ts,
        'BV.date_eth_calendar' AS record_source
    FROM {{ ref('hub_date_conformed') }} h
    JOIN {{ ref('sat_hcmis_date') }} sat
      ON TO_CHAR(sat.full_date::date, 'YYYY-MM-DD') = h.date_iso
)
SELECT
    hk_date_conformed,
    full_date,
    calendar_year, calendar_quarter,
    gregorian_month, gregorian_month_name, gregorian_day, gregorian_day_name, gregorian_iso_week,
    fiscal_year, fiscal_quarter, fiscal_month_of_year,
    eth_year, eth_month, eth_day_of_month, eth_day_name_of_week, eth_month_name, eth_full_date,
    is_holiday, is_last_day_of_month,
    md5(
        COALESCE(full_date::text,'') || '|' ||
        COALESCE(eth_year::text,'')  || '|' ||
        COALESCE(eth_month::text,'') || '|' ||
        COALESCE(eth_day_of_month::text,'')
    ) AS hashdiff,
    load_ts,
    record_source
FROM src
{% if is_incremental() %}
WHERE hk_date_conformed NOT IN (SELECT hk_date_conformed FROM {{ this }})
{% endif %}
