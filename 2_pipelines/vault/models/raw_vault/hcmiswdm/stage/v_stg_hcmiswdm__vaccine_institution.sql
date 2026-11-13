{{ config(materialized='view') }}
SELECT *,
       'HCMIS.vaccine_institution' AS record_source,
       {{ automate_dv.hash(columns=['institution_id'], alias='hk_hcmis_facility') }},
       {{ automate_dv.hash(columns=sat_hashdiff('hcmis_facility'), alias='hd_hcmis_facility', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_hcmiswdm__vaccine_institution') }}
