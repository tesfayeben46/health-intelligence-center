{{ config(materialized='view') }}
SELECT *,
       'DHIS2.data_sets' AS record_source,
       {{ automate_dv.hash(columns=['id'], alias='hk_dhis2_data_set') }},
       {{ automate_dv.hash(columns=sat_hashdiff('dhis2_data_set'), alias='hd_dhis2_data_set', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_dhis2__data_sets') }}
