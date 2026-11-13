{{ config(materialized='view') }}
SELECT *,
       'eLMIS-v2-history.history_requisition_line' AS record_source,
       {{ automate_dv.hash(columns=['source_line_item_id','source_requisition_id','product_code'], alias='hk_elmis_hist_requisition_line') }},
       {{ automate_dv.hash(columns=['source_requisition_id'], alias='hk_elmis_hist_requisition') }},
       {{ automate_dv.hash(columns=['product_code'], alias='hk_elmis_hist_product') }},
       {{ automate_dv.hash(columns=sat_hashdiff('elmis_hist_requisition_line'), alias='hd_elmis_hist_requisition_line', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_elmis_hist__history_requisition_line') }}
