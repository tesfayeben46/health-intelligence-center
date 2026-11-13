{{ config(materialized='view') }}
SELECT *,
       'HCMIS.dim_dispatchfacility' AS record_source,
       {{ automate_dv.hash(columns=['node_id'], alias='hk_hcmis_dispatch_facility') }},
       {{ automate_dv.hash(columns=sat_hashdiff('hcmis_dispatch_facility'), alias='hd_hcmis_dispatch_facility', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_hcmiswdm__dim_dispatchfacility') }}
