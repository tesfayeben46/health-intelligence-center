/*
    v_sat_ethioemr_patient_current: the latest sat_ethioemr_patient record per hk_ethioemr_patient.
    Satellites are insert-only: a changed record lands as a new row for the
    same hash key, so any consumer joining the base sat double-counts once
    history accumulates. Marts read this view for "the" attribute row.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_ethioemr_patient) *
FROM {{ ref('sat_ethioemr_patient') }}
ORDER BY hk_ethioemr_patient, load_ts DESC
