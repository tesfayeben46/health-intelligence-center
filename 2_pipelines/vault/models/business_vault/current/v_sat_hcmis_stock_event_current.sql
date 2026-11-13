/*
    v_sat_hcmis_stock_event_current: the latest sat_hcmis_stock_event record per hk_hcmis_stock_event.
    Satellites are insert-only: a changed record lands as a new row for the
    same hash key, so any consumer joining the base sat double-counts once
    history accumulates. Marts read this view for "the" attribute row.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_hcmis_stock_event) *
FROM {{ ref('sat_hcmis_stock_event') }}
ORDER BY hk_hcmis_stock_event, load_ts DESC
