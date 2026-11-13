/*
    v_sat_hcmis_dispatch_current: latest sat row per hk_hcmis_dispatch
    (movement). Marts read this current-record view.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_hcmis_dispatch) *
FROM {{ ref('sat_hcmis_dispatch') }}
ORDER BY hk_hcmis_dispatch, load_ts DESC
