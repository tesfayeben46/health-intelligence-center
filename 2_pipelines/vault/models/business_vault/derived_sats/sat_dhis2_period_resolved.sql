/*
    sat_dhis2_period_resolved: Business Vault derived sat.

    Decomposes the raw DHIS2 period_code stored in hub_dhis2_period.period
    into structured calendar attributes:
        period_type   (yearly / quarterly / monthly / weekly / daily / unknown)
        start_date    inclusive period start (Gregorian)
        end_date      inclusive period end
        days_in_period
        is_complete   (TRUE once end_date < CURRENT_DATE)

    DHIS2 period code formats recognised:
        yyyy           yearly        e.g. 2024
        yyyyQq         quarterly     e.g. 2024Q1
        yyyySs         six-monthly   e.g. 2024S1
        yyyymm         monthly       e.g. 202401
        yyyyWnn        weekly        e.g. 2024W12 (ISO week)
        yyyymmdd       daily         e.g. 20240115

    Materialised as table (not incremental) because the input space (distinct
    period_codes in the warehouse) is small (tens to hundreds), and the
    decomposition logic is deterministic so a full rebuild on each run is
    free. AutomateDV's incremental sat semantics aren't useful here.
*/
{{ config(materialized='table', schema='nhdw_vault_business') }}

WITH src AS (
    -- period arrives as bigint when the seed CSV values are all-numeric
    -- (CSV->parquet->ClickHouse->PG infers int); cast to text so the regex /
    -- substr period-format parsing below works. The ::int casts further down
    -- still work on the text value.
    SELECT hk_dhis2_period, period::text AS period, load_ts, record_source
    FROM {{ ref('hub_dhis2_period') }}
),
parsed AS (
    SELECT
        hk_dhis2_period,
        period,
        load_ts,
        record_source,
        CASE
            WHEN period ~ '^[0-9]{4}$'            THEN 'yearly'
            WHEN period ~ '^[0-9]{4}Q[1-4]$'      THEN 'quarterly'
            WHEN period ~ '^[0-9]{4}S[1-2]$'      THEN 'six_monthly'
            WHEN period ~ '^[0-9]{4}W[0-9]{1,2}$' THEN 'weekly'
            WHEN period ~ '^[0-9]{6}$'            THEN 'monthly'
            WHEN period ~ '^[0-9]{8}$'            THEN 'daily'
            ELSE 'unknown'
        END AS period_type,
        CASE
            WHEN period ~ '^[0-9]{4}$' THEN
                make_date(period::int, 1, 1)
            WHEN period ~ '^[0-9]{4}Q[1-4]$' THEN
                make_date(substr(period,1,4)::int, ((substr(period,6,1)::int - 1) * 3) + 1, 1)
            WHEN period ~ '^[0-9]{4}S[1-2]$' THEN
                make_date(substr(period,1,4)::int,
                          CASE substr(period,6,1) WHEN '1' THEN 1 ELSE 7 END, 1)
            WHEN period ~ '^[0-9]{4}W[0-9]{1,2}$' THEN
                to_date(substr(period,1,4) || '-' || lpad(substring(period from 6),2,'0') || '-1',
                        'IYYY-IW-ID')
            WHEN period ~ '^[0-9]{6}$' THEN
                make_date(substr(period,1,4)::int, substr(period,5,2)::int, 1)
            WHEN period ~ '^[0-9]{8}$' THEN
                make_date(substr(period,1,4)::int, substr(period,5,2)::int, substr(period,7,2)::int)
        END AS start_date
    FROM src
)
SELECT
    hk_dhis2_period,
    period_type,
    start_date,
    CASE period_type
        WHEN 'yearly'      THEN (start_date + INTERVAL '1 year - 1 day')::date
        WHEN 'quarterly'   THEN (start_date + INTERVAL '3 months - 1 day')::date
        WHEN 'six_monthly' THEN (start_date + INTERVAL '6 months - 1 day')::date
        WHEN 'weekly'      THEN (start_date + INTERVAL '6 days')::date
        WHEN 'monthly'     THEN (start_date + INTERVAL '1 month - 1 day')::date
        WHEN 'daily'       THEN start_date
    END AS end_date,
    CASE period_type
        WHEN 'yearly'      THEN ((start_date + INTERVAL '1 year - 1 day')::date - start_date + 1)
        WHEN 'quarterly'   THEN ((start_date + INTERVAL '3 months - 1 day')::date - start_date + 1)
        WHEN 'six_monthly' THEN ((start_date + INTERVAL '6 months - 1 day')::date - start_date + 1)
        WHEN 'weekly'      THEN 7
        WHEN 'monthly'     THEN ((start_date + INTERVAL '1 month - 1 day')::date - start_date + 1)
        WHEN 'daily'       THEN 1
    END AS days_in_period,
    (
        CASE period_type
            WHEN 'yearly'      THEN (start_date + INTERVAL '1 year - 1 day')::date
            WHEN 'quarterly'   THEN (start_date + INTERVAL '3 months - 1 day')::date
            WHEN 'six_monthly' THEN (start_date + INTERVAL '6 months - 1 day')::date
            WHEN 'weekly'      THEN (start_date + INTERVAL '6 days')::date
            WHEN 'monthly'     THEN (start_date + INTERVAL '1 month - 1 day')::date
            WHEN 'daily'       THEN start_date
        END < CURRENT_DATE
    ) AS is_complete,
    md5(period_type || '|' || COALESCE(start_date::text,'')) AS hashdiff,
    load_ts,
    record_source
FROM parsed
