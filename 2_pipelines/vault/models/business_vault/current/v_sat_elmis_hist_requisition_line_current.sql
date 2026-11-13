/*
    v_sat_elmis_hist_requisition_line_current: latest sat_elmis_hist_requisition_line per hk_elmis_hist_requisition_line.
    Satellites are insert-only, so marts read this current-record view.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_elmis_hist_requisition_line) *
FROM {{ ref('sat_elmis_hist_requisition_line') }}
ORDER BY hk_elmis_hist_requisition_line, load_ts DESC
