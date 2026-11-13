/*
    v_sat_elmis_hist_facility_type_current: latest sat_elmis_hist_facility_type per hk_elmis_hist_facility_type.
    Satellites are insert-only, so marts read this current-record view.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_elmis_hist_facility_type) *
FROM {{ ref('sat_elmis_hist_facility_type') }}
ORDER BY hk_elmis_hist_facility_type, load_ts DESC
