{{ config(materialized='view') }}
SELECT *,
       'HCMIS.dim_environment' AS record_source,
       {{ automate_dv.hash(columns=['environment_id'], alias='hk_hcmis_environment') }},
       {{ automate_dv.hash(columns=sat_hashdiff('hcmis_environment'), alias='hd_hcmis_environment', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_hcmiswdm__dim_environment') }}
