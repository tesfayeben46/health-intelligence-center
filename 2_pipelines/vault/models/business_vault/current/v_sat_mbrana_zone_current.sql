/*
    v_sat_mbrana_zone_current: the latest sat_mbrana_zone record per hk_mbrana_zone.
    Satellites are insert-only: a changed record lands as a new row for the
    same hash key, so any consumer joining the base sat double-counts once
    history accumulates. Marts read this view for "the" attribute row.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_mbrana_zone) *
FROM {{ ref('sat_mbrana_zone') }}
ORDER BY hk_mbrana_zone, load_ts DESC
