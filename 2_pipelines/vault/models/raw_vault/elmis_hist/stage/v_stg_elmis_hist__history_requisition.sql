{{ config(materialized='view') }}
SELECT *,
       'eLMIS-v2-history.history_requisition' AS record_source,
       {{ automate_dv.hash(columns=['source_requisition_id'], alias='hk_elmis_hist_requisition') }},
       {{ automate_dv.hash(columns=['facility_code'], alias='hk_elmis_hist_facility') }},
       {{ automate_dv.hash(columns=['programme_code'], alias='hk_elmis_hist_programme') }},
       {{ automate_dv.hash(columns=['source_period_id'], alias='hk_elmis_hist_period') }},
       {{ automate_dv.hash(columns=['source_requisition_id','facility_code','programme_code','source_period_id'], alias='hk_elmis_hist_requisition_ctx') }},
       {{ automate_dv.hash(columns=sat_hashdiff('elmis_hist_requisition'), alias='hd_elmis_hist_requisition', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_elmis_hist__history_requisition') }}
