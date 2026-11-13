/*
    v_sat_climate_week_current: the latest sat_climate_week record per hk_climate_week.
    Satellites are insert-only: a changed record lands as a new row for the
    same hash key, so any consumer joining the base sat double-counts once
    history accumulates. Marts read this view for "the" attribute row.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_climate_week) *
FROM {{ ref('sat_climate_week') }}
ORDER BY hk_climate_week, load_ts DESC
