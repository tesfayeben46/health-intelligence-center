{{ config(materialized='view') }}
SELECT *,
       'eLMIS-v2-history.reference_programme' AS record_source,
       {{ automate_dv.hash(columns=['code'], alias='hk_elmis_hist_programme') }},
       {{ automate_dv.hash(columns=sat_hashdiff('elmis_hist_programme'), alias='hd_elmis_hist_programme', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_elmis_hist__reference_programme') }}
