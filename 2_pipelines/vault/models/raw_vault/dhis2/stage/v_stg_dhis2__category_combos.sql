{{ config(materialized='view') }}
SELECT *,
       'DHIS2.category_combos' AS record_source,
       {{ automate_dv.hash(columns=['id'], alias='hk_dhis2_category_combo') }},
       {{ automate_dv.hash(columns=sat_hashdiff('dhis2_category_combo'), alias='hd_dhis2_category_combo', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_dhis2__category_combos') }}
