/*
    v_sat_hcmis_item_current: the latest sat_hcmis_item record per hk_hcmis_item.
    Satellites are insert-only: a changed record lands as a new row for the
    same hash key, so any consumer joining the base sat double-counts once
    history accumulates. Marts read this view for "the" attribute row.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_hcmis_item) *
FROM {{ ref('sat_hcmis_item') }}
ORDER BY hk_hcmis_item, load_ts DESC
