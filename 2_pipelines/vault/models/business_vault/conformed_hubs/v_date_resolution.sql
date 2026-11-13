/*
    v_date_resolution: AutomateDV stage for hub_date_conformed.

    BK = ISO date string (yyyy-mm-dd). All source-system date references
    (HCMIS dim_date.full_date, mBrana modified_date, DHIS2 period start
    dates, EthioEMR effective_dt) can resolve to this hub once a Same-As
    link is wired up.

    Sourced from HCMIS dim_date (the richest date dimension available), read via
    the current-record view. When other sources contribute new dates,
    union them in here.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

WITH src AS (
    SELECT DISTINCT
        TO_CHAR(sat.full_date::date, 'YYYY-MM-DD') AS date_iso,
        sat.load_ts
    FROM {{ ref('v_sat_hcmis_date_current') }} sat
    WHERE sat.full_date IS NOT NULL
)
SELECT
    src.*,
    {{ automate_dv.hash(columns=['date_iso'], alias='hk_date_conformed') }},
    'BV.date_resolution' AS record_source
FROM src
