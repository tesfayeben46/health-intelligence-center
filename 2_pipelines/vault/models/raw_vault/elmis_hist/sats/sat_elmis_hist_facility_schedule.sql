{{ config(materialized='incremental', on_schema_change='append_new_columns') }}
{{ automate_dv.sat(
    src_pk='hk_elmis_hist_facility_schedule',
    src_hashdiff={'source_column': 'hd_elmis_hist_facility_schedule', 'alias': 'hashdiff'},
    src_payload=sat_payload('elmis_hist_facility_schedule'),
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_elmis_hist__reference_facility_programme_schedule'
) }}
