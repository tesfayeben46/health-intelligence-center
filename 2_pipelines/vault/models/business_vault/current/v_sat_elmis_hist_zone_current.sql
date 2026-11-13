/*
    v_sat_elmis_hist_zone_current: latest sat_elmis_hist_zone per hk_elmis_hist_zone.
    Satellites are insert-only, so marts read this current-record view.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_elmis_hist_zone) *
FROM {{ ref('sat_elmis_hist_zone') }}
ORDER BY hk_elmis_hist_zone, load_ts DESC
