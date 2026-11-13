{{ config(materialized='view') }}
SELECT *,
       'DHIS2.data_elements' AS record_source,
       {{ automate_dv.hash(columns=['id'], alias='hk_dhis2_data_element') }},
       {{ automate_dv.hash(columns=sat_hashdiff('dhis2_data_element'), alias='hd_dhis2_data_element', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_dhis2__data_elements') }}
