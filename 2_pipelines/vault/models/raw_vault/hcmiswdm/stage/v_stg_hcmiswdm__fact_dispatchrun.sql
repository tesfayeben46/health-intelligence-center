{{ config(materialized='view') }}
SELECT *,
       'HCMIS.fact_dispatchrun' AS record_source,
       {{ automate_dv.hash(columns=['run_id'], alias='hk_hcmis_dispatch_run') }},
       {{ automate_dv.hash(columns=sat_hashdiff('hcmis_dispatch_run'), alias='hd_hcmis_dispatch_run', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_hcmiswdm__fact_dispatchrun') }}
