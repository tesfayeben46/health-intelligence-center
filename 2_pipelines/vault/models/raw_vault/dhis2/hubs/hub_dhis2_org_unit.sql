/*
    hub_dhis2_org_unit: DHIS2 organisation unit (facility / admin tree node).
    BK = id (11-char DHIS2 UID). Loaded from both the org_units dim view
    and the data_value_sets fact feed (covers org_units that may not yet
    appear in the dim but do appear in facts). The AutomateDV hub macro
    unions the sources, dedupes on the hash key and keeps the first-seen
    load_ts.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.hub(
    src_pk='hk_dhis2_org_unit',
    src_nk='id',
    src_ldts='load_ts',
    src_source='record_source',
    source_model=['v_stg_dhis2__organisation_units', 'v_stg_dhis2__org_units_from_facts']
) }}
