/*
    v_sat_elmis_hist_product_group_current: latest sat_elmis_hist_product_group per hk_elmis_hist_product_group.
    Satellites are insert-only, so marts read this current-record view.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_elmis_hist_product_group) *
FROM {{ ref('sat_elmis_hist_product_group') }}
ORDER BY hk_elmis_hist_product_group, load_ts DESC
