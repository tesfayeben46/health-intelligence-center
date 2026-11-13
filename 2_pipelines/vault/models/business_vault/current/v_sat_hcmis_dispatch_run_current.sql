/*
    v_sat_hcmis_dispatch_run_current: latest sat row per hk_hcmis_dispatch_run.
    Marts read this current-record view.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_hcmis_dispatch_run) *
FROM {{ ref('sat_hcmis_dispatch_run') }}
ORDER BY hk_hcmis_dispatch_run, load_ts DESC
