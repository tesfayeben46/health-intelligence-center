/*
    lnk_elmis_hist_requisition: historical requisition context link.
    FK mapping (requisition x facility x programme x reporting period).
    Source identity is elmis_hist throughout: a Zambian facility-code hub row
    is a different entity from any OpenLMIS v3 facility (no conflation).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_elmis_hist_requisition_ctx',
    src_fk=['hk_elmis_hist_requisition','hk_elmis_hist_facility','hk_elmis_hist_programme','hk_elmis_hist_period'],
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_elmis_hist__history_requisition'
) }}
