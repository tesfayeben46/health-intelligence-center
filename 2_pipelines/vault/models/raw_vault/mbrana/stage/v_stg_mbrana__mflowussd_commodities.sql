{{ config(materialized='view') }}
SELECT *,
       'mBrana.mflowussd_commodities' AS record_source,
       {{ automate_dv.hash(columns=['commodity_id'], alias='hk_mbrana_commodity') }},
       {{ automate_dv.hash(columns=sat_hashdiff('mbrana_commodity'), alias='hd_mbrana_commodity', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_mbrana__mflowussd_commodities') }}
