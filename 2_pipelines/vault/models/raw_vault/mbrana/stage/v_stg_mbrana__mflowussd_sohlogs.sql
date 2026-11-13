{{ config(materialized='view') }}
SELECT *,
       'mBrana.mflowussd_sohlogs' AS record_source,
       -- Transactional link PK: composite hash of the event id + both FKs.
       -- soh_log_id is the source PK; combining with the FKs keeps the link
       -- key tied to its semantic identity even if a soh_log was relinked.
       -- soh_log_id is therefore a dependent-child key, stored on the link
       -- via src_extra_columns (input order below is the hash contract).
       {{ automate_dv.hash(columns=['soh_log_id','institution_id','commodity_id'],
            alias='hk_mbrana_stock_event') }},
       {{ automate_dv.hash(columns=['institution_id'], alias='hk_mbrana_ussd_facility') }},
       {{ automate_dv.hash(columns=['commodity_id'],   alias='hk_mbrana_commodity') }},
       {{ automate_dv.hash(columns=sat_hashdiff('mbrana_stock_event'), alias='hd_mbrana_stock_event', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_mbrana__mflowussd_sohlogs') }}
