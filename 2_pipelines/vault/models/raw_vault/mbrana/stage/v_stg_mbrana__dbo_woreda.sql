{{ config(materialized='view') }}
SELECT *,
       'mBrana.dbo_woreda' AS record_source,
       {{ automate_dv.hash(columns=['id'],      alias='hk_mbrana_woreda') }},
       {{ automate_dv.hash(columns=['zone_id'], alias='hk_mbrana_zone_fk') }},
       {{ automate_dv.hash(columns=['id','zone_id'], alias='hk_woreda_zone') }},
       {{ automate_dv.hash(columns=sat_hashdiff('mbrana_woreda'), alias='hd_mbrana_woreda', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_mbrana__dbo_woreda') }}
