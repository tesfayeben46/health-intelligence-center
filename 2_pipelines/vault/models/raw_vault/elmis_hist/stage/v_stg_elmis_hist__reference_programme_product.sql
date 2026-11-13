{{ config(materialized='view') }}
SELECT *,
       'eLMIS-v2-history.reference_programme_product' AS record_source,
       {{ automate_dv.hash(columns=['programme_code'], alias='hk_elmis_hist_programme') }},
       {{ automate_dv.hash(columns=['product_code'], alias='hk_elmis_hist_product') }},
       {{ automate_dv.hash(columns=['programme_code','product_code'], alias='hk_elmis_hist_programme_product') }},
       {{ automate_dv.hash(columns=sat_hashdiff('elmis_hist_programme_product'), alias='hd_elmis_hist_programme_product', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_elmis_hist__reference_programme_product') }}
