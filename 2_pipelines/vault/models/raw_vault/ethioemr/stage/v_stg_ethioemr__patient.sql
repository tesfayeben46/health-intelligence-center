{{ config(materialized='view') }}
SELECT *,
       'EthioEMR.Patient' AS record_source,
       {{ automate_dv.hash(columns=['id'], alias='hk_ethioemr_patient') }},
       {{ automate_dv.hash(columns=sat_hashdiff('ethioemr_patient'), alias='hd_ethioemr_patient', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_ethioemr__patient') }}
