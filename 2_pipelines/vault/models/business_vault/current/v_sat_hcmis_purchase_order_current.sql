/*
    v_sat_hcmis_purchase_order_current: latest sat row per hk_hcmis_purchase_order.
    Satellites are insert-only, so the mart reads THIS current-record view.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_hcmis_purchase_order) *
FROM {{ ref('sat_hcmis_purchase_order') }}
ORDER BY hk_hcmis_purchase_order, load_ts DESC
