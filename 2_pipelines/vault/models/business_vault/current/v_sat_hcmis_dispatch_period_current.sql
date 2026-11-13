/*
    v_sat_hcmis_dispatch_period_current: latest sat row per
    hk_hcmis_dispatch_period. Marts read this current-record view.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_hcmis_dispatch_period) *
FROM {{ ref('sat_hcmis_dispatch_period') }}
ORDER BY hk_hcmis_dispatch_period, load_ts DESC
