/*
    v_sat_dhis2_category_combo_current: the latest sat_dhis2_category_combo record per hk_dhis2_category_combo.
    Satellites are insert-only: a changed record lands as a new row for the
    same hash key, so any consumer joining the base sat double-counts once
    history accumulates. Marts read this view for "the" attribute row.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_dhis2_category_combo) *
FROM {{ ref('sat_dhis2_category_combo') }}
ORDER BY hk_dhis2_category_combo, load_ts DESC
