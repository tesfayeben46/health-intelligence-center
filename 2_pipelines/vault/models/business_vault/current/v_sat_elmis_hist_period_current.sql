/*
    v_sat_elmis_hist_period_current: latest sat_elmis_hist_period per hk_elmis_hist_period.
    Satellites are insert-only, so marts read this current-record view.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_elmis_hist_period) *
FROM {{ ref('sat_elmis_hist_period') }}
ORDER BY hk_elmis_hist_period, load_ts DESC
