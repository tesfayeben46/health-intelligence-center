{{ config(materialized='view') }}
SELECT *,
       'HCMIS.dim_item' AS record_source,
       {{ automate_dv.hash(columns=['item_id'], alias='hk_hcmis_item') }},
       {{ automate_dv.hash(columns=sat_hashdiff('hcmis_item'), alias='hd_hcmis_item', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_hcmiswdm__dim_item') }}
