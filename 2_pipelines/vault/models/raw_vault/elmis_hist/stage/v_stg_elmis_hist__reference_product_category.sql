{{ config(materialized='view') }}
SELECT *,
       'eLMIS-v2-history.reference_product_category' AS record_source,
       {{ automate_dv.hash(columns=['code'], alias='hk_elmis_hist_product_category') }},
       {{ automate_dv.hash(columns=sat_hashdiff('elmis_hist_product_category'), alias='hd_elmis_hist_product_category', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_elmis_hist__reference_product_category') }}
