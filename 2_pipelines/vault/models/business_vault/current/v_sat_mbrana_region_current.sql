/*
    v_sat_mbrana_region_current: the latest sat_mbrana_region record per hk_mbrana_region.
    Satellites are insert-only: a changed record lands as a new row for the
    same hash key, so any consumer joining the base sat double-counts once
    history accumulates. Marts read this view for "the" attribute row.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_mbrana_region) *
FROM {{ ref('sat_mbrana_region') }}
ORDER BY hk_mbrana_region, load_ts DESC
