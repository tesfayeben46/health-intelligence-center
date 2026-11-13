{{ config(materialized='view') }}
SELECT *,
       'eLMIS-v2-history.reference_facility_type' AS record_source,
       {{ automate_dv.hash(columns=['code'], alias='hk_elmis_hist_facility_type') }},
       {{ automate_dv.hash(columns=sat_hashdiff('elmis_hist_facility_type'), alias='hd_elmis_hist_facility_type', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_elmis_hist__reference_facility_type') }}
