{{ config(materialized='view') }}
SELECT *,
       'HCMIS.vaccine_dailyrecord' AS record_source,
       {{ automate_dv.hash(columns=['daily_record_id'], alias='hk_hcmis_vaccine_record') }},
       {{ automate_dv.hash(columns=['item_id'],         alias='hk_hcmis_item') }},
       {{ automate_dv.hash(columns=['unit_id'],         alias='hk_hcmis_unit') }},
       {{ automate_dv.hash(columns=['environment_id'],  alias='hk_hcmis_environment') }},
       {{ automate_dv.hash(
            columns=['daily_record_id','item_id','unit_id','environment_id','date'],
            alias='hk_hcmis_vaccine_event') }},
       {{ automate_dv.hash(columns=sat_hashdiff('hcmis_vaccine_event'), alias='hd_hcmis_vaccine_event', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_hcmiswdm__vaccine_dailyrecord') }}
