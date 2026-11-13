/*
    lnk_elmis_hist_requisition_line: historical R&R line fact link.
    FK mapping (requisition x product). `source_line_item_id` is a
    dependent-child key (part of the link grain, stored via
    src_extra_columns, never in the sat payload).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_elmis_hist_requisition_line',
    src_fk=['hk_elmis_hist_requisition','hk_elmis_hist_product'],
    src_extra_columns=['source_line_item_id'],
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_elmis_hist__history_requisition_line'
) }}
