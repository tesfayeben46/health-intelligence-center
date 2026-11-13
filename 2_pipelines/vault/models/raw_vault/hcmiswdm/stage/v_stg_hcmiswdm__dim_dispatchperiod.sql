{{ config(materialized='view') }}
SELECT *,
       'HCMIS.dim_dispatchperiod' AS record_source,
       {{ automate_dv.hash(columns=['period_id'], alias='hk_hcmis_dispatch_period') }},
       {{ automate_dv.hash(columns=sat_hashdiff('hcmis_dispatch_period'), alias='hd_hcmis_dispatch_period', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_hcmiswdm__dim_dispatchperiod') }}
