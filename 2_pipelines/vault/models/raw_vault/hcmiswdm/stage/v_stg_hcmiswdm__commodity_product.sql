{{ config(materialized='view') }}
SELECT *,
       'HCMIS.commodity_product' AS record_source,
       {{ automate_dv.hash(columns=['product_id'], alias='hk_hcmis_commodity') }},
       {{ automate_dv.hash(columns=sat_hashdiff('hcmis_commodity'), alias='hd_hcmis_commodity', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_hcmiswdm__commodity_product') }}
