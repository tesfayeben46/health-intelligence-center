{{ config(materialized='view') }}
SELECT *,
       'EthioEMR.Encounter' AS record_source,
       {{ automate_dv.hash(columns=['id'], alias='hk_ethioemr_encounter') }},
       {{ automate_dv.hash(columns=['subject_patient_fhir_id'], alias='hk_ethioemr_patient_fk') }},
       {{ automate_dv.hash(
            columns=['id','subject_patient_fhir_id'],
            alias='hk_encounter_patient') }},
       {{ automate_dv.hash(columns=sat_hashdiff('ethioemr_encounter'), alias='hd_ethioemr_encounter', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_ethioemr__encounter') }}
