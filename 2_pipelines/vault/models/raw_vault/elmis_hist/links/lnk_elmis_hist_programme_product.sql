/*
    lnk_elmis_hist_programme_product: which product belongs to which programme
    (price, category and doses per month live on sat_elmis_hist_programme_product).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_elmis_hist_programme_product',
    src_fk=['hk_elmis_hist_programme','hk_elmis_hist_product'],
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_elmis_hist__reference_programme_product'
) }}
