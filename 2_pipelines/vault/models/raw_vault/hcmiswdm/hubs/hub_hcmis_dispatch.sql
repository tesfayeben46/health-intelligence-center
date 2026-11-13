/*
    hub_hcmis_dispatch: BK = movement_id. One row per dispatch movement line
    (the unit of work). Its descriptive payload rides sat_hcmis_dispatch; its
    hub/facility/period edges ride lnk_hcmis_dispatch.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.hub(
    src_pk='hk_hcmis_dispatch', src_nk='movement_id',
    src_ldts='load_ts', src_source='record_source',
    source_model=['v_stg_hcmiswdm__fact_dispatch']
) }}
