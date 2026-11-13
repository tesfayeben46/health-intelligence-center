{{ config(materialized='view') }}
SELECT *,
       'mBrana.dbo_zone' AS record_source,
       {{ automate_dv.hash(columns=['id'],        alias='hk_mbrana_zone') }},
       {{ automate_dv.hash(columns=['region_id'], alias='hk_mbrana_region_fk') }},
       {{ automate_dv.hash(columns=['id','region_id'], alias='hk_zone_region') }},
       {{ automate_dv.hash(columns=sat_hashdiff('mbrana_zone'), alias='hd_mbrana_zone', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_mbrana__dbo_zone') }}
