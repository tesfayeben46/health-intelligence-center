{{ config(materialized='view') }}
-- Stage view: pre-computes the hash key (region by ISO-week) + hash-diff over the
-- weekly climate measures, before the hub/sat consume it.
SELECT *,
       'Climate.weekly' AS record_source,
       {{ automate_dv.hash(columns=['region_id', 'iso_week'],
            alias='hk_climate_week') }},
       {{ automate_dv.hash(columns=sat_hashdiff('climate_week'), alias='hd_climate_week', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_climate__weekly') }}
