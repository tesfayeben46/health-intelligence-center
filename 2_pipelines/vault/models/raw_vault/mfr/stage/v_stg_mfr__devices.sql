/*
    v_stg_mfr__devices: staging view for MFR FHIR Device resources (facility
    equipment inventory). BK = device_id; owner_facility_id is the canonical
    FAC-… facility so the mart can roll up by owner if needed.
*/
{{ config(materialized='view') }}
SELECT *,
       'MFR.devices' AS record_source,
       {{ automate_dv.hash(columns=['device_id'], alias='hk_mfr_device') }},
       {{ automate_dv.hash(columns=sat_hashdiff('mfr_device'), alias='hd_mfr_device', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_mfr__devices') }}
