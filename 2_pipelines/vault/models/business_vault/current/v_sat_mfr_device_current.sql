/*
    v_sat_mfr_device_current: the latest sat_mfr_device record per hk_mfr_device.
    Satellites are insert-only: a changed record lands as a new row for the
    same hash key, so any consumer joining the base sat double-counts once
    history accumulates. Marts read this view for "the" attribute row.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_mfr_device) *
FROM {{ ref('sat_mfr_device') }}
ORDER BY hk_mfr_device, load_ts DESC
