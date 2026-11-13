{{ config(materialized='view') }}
SELECT *,
       'eLMIS-v2-history.reference_geographic_zone' AS record_source,
       {{ automate_dv.hash(columns=['code'], alias='hk_elmis_hist_zone') }},
       {{ automate_dv.hash(columns=sat_hashdiff('elmis_hist_zone'), alias='hd_elmis_hist_zone', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_elmis_hist__reference_geographic_zone') }}
