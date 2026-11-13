{{ config(materialized='view') }}
SELECT *,
       'DHIS2.category_option_combos' AS record_source,
       {{ automate_dv.hash(columns=['id'], alias='hk_dhis2_category_option_combo') }},
       {{ automate_dv.hash(columns=['category_combo_id'], alias='hk_dhis2_category_combo_parent') }},
       {{ automate_dv.hash(columns=sat_hashdiff('dhis2_category_option_combo'), alias='hd_dhis2_category_option_combo', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_dhis2__category_option_combos') }}
