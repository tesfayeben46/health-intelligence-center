{{ config(materialized='view') }}
SELECT *,
       'EthioEMR.Observation' AS record_source,
       {{ automate_dv.hash(columns=['id'], alias='hk_ethioemr_observation') }},
       {{ automate_dv.hash(columns=['subject_patient_fhir_id'], alias='hk_ethioemr_patient_fk') }},
       {{ automate_dv.hash(columns=['encounter_fhir_id'], alias='hk_ethioemr_encounter_fk') }},
       {{ automate_dv.hash(columns=['id','subject_patient_fhir_id'], alias='hk_observation_patient') }},
       {{ automate_dv.hash(columns=['id','encounter_fhir_id'], alias='hk_observation_encounter') }},
       {{ automate_dv.hash(columns=sat_hashdiff('ethioemr_observation'), alias='hd_ethioemr_observation', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_ethioemr__observation') }}
