{{ config(materialized='view') }}
SELECT *,
       'DHIS2.organisation_units' AS record_source,
       {{ automate_dv.hash(columns=['id'], alias='hk_dhis2_org_unit') }},
       {{ automate_dv.hash(columns=sat_hashdiff('dhis2_org_unit'), alias='hd_dhis2_org_unit', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_dhis2__organisation_units') }}
