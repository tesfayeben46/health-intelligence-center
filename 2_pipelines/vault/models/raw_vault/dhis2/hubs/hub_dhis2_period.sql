/*
    hub_dhis2_period: DHIS2 period code (e.g. "202401", "2024Q1", "2024").
    No dedicated dim in source; periods are observed via data_value_sets.

    The Business Vault adds sat_dhis2_period_resolved which decomposes the
    code into (period_type, start_date, end_date, eth_period).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.hub(
    src_pk='hk_dhis2_period',
    src_nk='period',
    src_ldts='load_ts',
    src_source='record_source',
    source_model='v_stg_dhis2__data_value_sets'
) }}
