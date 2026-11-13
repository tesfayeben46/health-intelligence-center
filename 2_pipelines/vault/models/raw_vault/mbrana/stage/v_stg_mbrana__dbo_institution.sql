{{ config(materialized='view') }}
SELECT *,
       'mBrana.dbo_institution' AS record_source,
       {{ automate_dv.hash(columns=['id'], alias='hk_mbrana_facility') }},
       {{ automate_dv.hash(columns=sat_hashdiff('mbrana_facility'), alias='hd_mbrana_facility', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_mbrana__dbo_institution') }}
