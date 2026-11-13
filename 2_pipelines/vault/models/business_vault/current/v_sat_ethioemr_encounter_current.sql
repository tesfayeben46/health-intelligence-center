/*
    v_sat_ethioemr_encounter_current: the latest sat_ethioemr_encounter record per hk_ethioemr_encounter.
    Satellites are insert-only: a changed record lands as a new row for the
    same hash key, so any consumer joining the base sat double-counts once
    history accumulates. Marts read this view for "the" attribute row.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_ethioemr_encounter) *
FROM {{ ref('sat_ethioemr_encounter') }}
ORDER BY hk_ethioemr_encounter, load_ts DESC
