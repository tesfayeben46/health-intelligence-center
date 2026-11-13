/*
    v_sat_elmis_hist_product_category_current: latest sat_elmis_hist_product_category per hk_elmis_hist_product_category.
    Satellites are insert-only, so marts read this current-record view.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_elmis_hist_product_category) *
FROM {{ ref('sat_elmis_hist_product_category') }}
ORDER BY hk_elmis_hist_product_category, load_ts DESC
