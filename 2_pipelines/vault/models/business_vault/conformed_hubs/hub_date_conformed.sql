/*
    hub_date_conformed: Business Vault conformed hub for calendar dates.
    BK = date_iso (yyyy-mm-dd), staged in v_date_resolution (currently
    HCMIS dim_date; extend the stage view when other sources contribute
    new dates). sat_date_eth_calendar hangs off this hub.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.hub(
    src_pk='hk_date_conformed', src_nk='date_iso', src_ldts='load_ts', src_source='record_source',
    source_model='v_date_resolution'
) }}
