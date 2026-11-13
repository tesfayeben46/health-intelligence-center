{{ config(materialized='view') }}
SELECT *,
       'HCMIS.fact_dailyrecord' AS record_source,
       -- BK alias so this fact stage can co-load hub_hcmis_date (whose NK
       -- column is dim_date's `date_key`) alongside the dim stage.
       date_id AS date_key,
       {{ automate_dv.hash(columns=['item_id'],        alias='hk_hcmis_item') }},
       {{ automate_dv.hash(columns=['environment_id'], alias='hk_hcmis_environment') }},
       {{ automate_dv.hash(columns=['mode_id'],        alias='hk_hcmis_mode') }},
       {{ automate_dv.hash(columns=['date_id'],        alias='hk_hcmis_date') }},
       {{ automate_dv.hash(
            columns=['item_id','environment_id','mode_id','date_id'],
            alias='hk_hcmis_stock_event') }},
       {{ automate_dv.hash(columns=sat_hashdiff('hcmis_stock_event'), alias='hd_hcmis_stock_event', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_hcmiswdm__fact_dailyrecord') }}
