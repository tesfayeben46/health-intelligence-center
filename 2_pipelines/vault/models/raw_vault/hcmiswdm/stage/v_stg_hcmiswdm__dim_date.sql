{{ config(materialized='view') }}
SELECT *,
       'HCMIS.dim_date' AS record_source,
       {{ automate_dv.hash(columns=['date_key'], alias='hk_hcmis_date') }},
       {{ automate_dv.hash(columns=sat_hashdiff('hcmis_date'), alias='hd_hcmis_date', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_hcmiswdm__dim_date') }}
