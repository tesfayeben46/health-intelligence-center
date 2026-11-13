{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.sat(
    src_pk='hk_dhis2_org_unit',
    src_hashdiff={'source_column': 'hd_dhis2_org_unit', 'alias': 'hashdiff'},
    src_payload=sat_payload('dhis2_org_unit'),
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_dhis2__organisation_units'
) }}
