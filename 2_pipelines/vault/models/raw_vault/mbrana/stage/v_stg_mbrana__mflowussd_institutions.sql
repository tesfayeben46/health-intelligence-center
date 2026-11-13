{{ config(materialized='view') }}
SELECT *,
       'mBrana.mflowussd_institutions' AS record_source,
       {{ automate_dv.hash(columns=['institution_id'], alias='hk_mbrana_ussd_facility') }},
       {{ automate_dv.hash(columns=sat_hashdiff('mbrana_ussd_facility'), alias='hd_mbrana_ussd_facility', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_mbrana__mflowussd_institutions') }}
