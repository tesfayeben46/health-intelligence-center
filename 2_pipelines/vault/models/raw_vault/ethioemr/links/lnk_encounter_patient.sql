/*
    lnk_encounter_patient: visit -> patient (FHIR Encounter.subject reference).
    FK mapping:
        hk_ethioemr_encounter      <- v_stg.id                     -> hub_ethioemr_encounter
        hk_ethioemr_patient_fk     <- v_stg.subject_patient_fhir_id -> hub_ethioemr_patient
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_encounter_patient',
    src_fk=['hk_ethioemr_encounter', 'hk_ethioemr_patient_fk'],
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_ethioemr__encounter'
) }}
