/*
    v_sat_elmis_hist_programme_current: latest sat_elmis_hist_programme per hk_elmis_hist_programme.
    Satellites are insert-only, so marts read this current-record view.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_elmis_hist_programme) *
FROM {{ ref('sat_elmis_hist_programme') }}
ORDER BY hk_elmis_hist_programme, load_ts DESC
