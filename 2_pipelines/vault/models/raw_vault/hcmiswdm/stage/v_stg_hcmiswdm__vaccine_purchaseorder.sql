{{ config(materialized='view') }}
SELECT *,
       'HCMIS.vaccine_purchaseorder' AS record_source,
       {{ automate_dv.hash(columns=['purchase_order_id'], alias='hk_hcmis_purchase_order') }},
       {{ automate_dv.hash(columns=['item_id'], alias='hk_hcmis_item') }},
       {{ automate_dv.hash(columns=['environment_id'], alias='hk_hcmis_environment') }},
       {{ automate_dv.hash(columns=sat_hashdiff('hcmis_purchase_order'), alias='hd_hcmis_purchase_order', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_hcmiswdm__vaccine_purchaseorder') }}
