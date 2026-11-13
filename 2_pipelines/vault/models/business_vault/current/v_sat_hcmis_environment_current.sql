/*
    v_sat_hcmis_environment_current: latest sat_hcmis_environment row per
    hk_hcmis_environment (region_id / hub attributes for the EPSS hubs).
    Insert-only sat, so marts read this current-record view, never the base sat.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_hcmis_environment) *
FROM {{ ref('sat_hcmis_environment') }}
ORDER BY hk_hcmis_environment, load_ts DESC
