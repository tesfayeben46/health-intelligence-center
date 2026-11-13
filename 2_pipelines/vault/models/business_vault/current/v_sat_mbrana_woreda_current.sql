/*
    v_sat_mbrana_woreda_current: the latest sat_mbrana_woreda record per hk_mbrana_woreda.
    Satellites are insert-only: a changed record lands as a new row for the
    same hash key, so any consumer joining the base sat double-counts once
    history accumulates. Marts read this view for "the" attribute row.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_mbrana_woreda) *
FROM {{ ref('sat_mbrana_woreda') }}
ORDER BY hk_mbrana_woreda, load_ts DESC
