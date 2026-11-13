{{ config(materialized='view') }}
SELECT *,
       'HCMIS.dim_dispatchhub' AS record_source,
       {{ automate_dv.hash(columns=['hub_id'], alias='hk_hcmis_dispatch_hub') }},
       {{ automate_dv.hash(columns=sat_hashdiff('hcmis_dispatch_hub'), alias='hd_hcmis_dispatch_hub', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_hcmiswdm__dim_dispatchhub') }}
