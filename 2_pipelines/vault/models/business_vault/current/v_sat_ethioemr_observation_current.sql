/*
    v_sat_ethioemr_observation_current: the latest sat_ethioemr_observation record per hk_ethioemr_observation.
    Satellites are insert-only: a changed record lands as a new row for the
    same hash key, so any consumer joining the base sat double-counts once
    history accumulates. Marts read this view for "the" attribute row.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_ethioemr_observation) *
FROM {{ ref('sat_ethioemr_observation') }}
ORDER BY hk_ethioemr_observation, load_ts DESC
