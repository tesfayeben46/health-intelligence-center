/*
    lnk_observation_patient: clinical result -> patient (FHIR Observation.subject).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_observation_patient',
    src_fk=['hk_ethioemr_observation', 'hk_ethioemr_patient_fk'],
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_ethioemr__observation'
) }}
