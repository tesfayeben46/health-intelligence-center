{{ config(materialized='view') }}
SELECT *,
       'mBrana.dbo_region' AS record_source,
       {{ automate_dv.hash(columns=['id'], alias='hk_mbrana_region') }},
       {{ automate_dv.hash(columns=sat_hashdiff('mbrana_region'), alias='hd_mbrana_region', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_mbrana__dbo_region') }}
