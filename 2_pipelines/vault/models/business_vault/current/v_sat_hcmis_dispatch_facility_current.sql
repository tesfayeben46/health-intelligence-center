/*
    v_sat_hcmis_dispatch_facility_current: latest sat row per
    hk_hcmis_dispatch_facility. Marts read this current-record view.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_hcmis_dispatch_facility) *
FROM {{ ref('sat_hcmis_dispatch_facility') }}
ORDER BY hk_hcmis_dispatch_facility, load_ts DESC
