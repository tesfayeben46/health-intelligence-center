{{ config(materialized='view') }}
SELECT *,
       'eLMIS-v2-history.reference_reporting_period' AS record_source,
       {{ automate_dv.hash(columns=['source_id'], alias='hk_elmis_hist_period') }},
       {{ automate_dv.hash(columns=sat_hashdiff('elmis_hist_period'), alias='hd_elmis_hist_period', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_elmis_hist__reference_reporting_period') }}
