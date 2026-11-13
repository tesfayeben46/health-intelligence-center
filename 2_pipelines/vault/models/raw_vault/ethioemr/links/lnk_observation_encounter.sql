/*
    lnk_observation_encounter: observation -> encounter (FHIR Observation.encounter).
    Skips rows where encounter_fhir_id is NULL (observation not tied to a visit).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_observation_encounter',
    src_fk=['hk_ethioemr_observation', 'hk_ethioemr_encounter_fk'],
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_ethioemr__observation'
) }}
