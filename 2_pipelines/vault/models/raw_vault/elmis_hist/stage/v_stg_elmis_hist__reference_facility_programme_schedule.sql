{{ config(materialized='view') }}
SELECT *,
       'eLMIS-v2-history.reference_facility_programme_schedule' AS record_source,
       {{ automate_dv.hash(columns=['facility_code'], alias='hk_elmis_hist_facility') }},
       {{ automate_dv.hash(columns=['programme_code'], alias='hk_elmis_hist_programme') }},
       {{ automate_dv.hash(columns=['facility_code','programme_code','processing_schedule_code'], alias='hk_elmis_hist_facility_schedule') }},
       {{ automate_dv.hash(columns=sat_hashdiff('elmis_hist_facility_schedule'), alias='hd_elmis_hist_facility_schedule', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_elmis_hist__reference_facility_programme_schedule') }}
