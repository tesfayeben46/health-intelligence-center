/*
    v_sat_elmis_hist_requisition_current: latest sat_elmis_hist_requisition per hk_elmis_hist_requisition.
    Satellites are insert-only, so marts read this current-record view.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_elmis_hist_requisition) *
FROM {{ ref('sat_elmis_hist_requisition') }}
ORDER BY hk_elmis_hist_requisition, load_ts DESC
