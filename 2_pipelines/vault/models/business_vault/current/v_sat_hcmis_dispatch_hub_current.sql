/*
    v_sat_hcmis_dispatch_hub_current: latest sat row per hk_hcmis_dispatch_hub.
    Satellites are insert-only, so the marts read this current-record view.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_hcmis_dispatch_hub) *
FROM {{ ref('sat_hcmis_dispatch_hub') }}
ORDER BY hk_hcmis_dispatch_hub, load_ts DESC
