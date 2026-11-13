/*
    v_sat_dhis2_data_value_current: the latest sat_dhis2_data_value record per hk_dhis2_data_value.
    Satellites are insert-only: a changed record lands as a new row for the
    same hash key, so any consumer joining the base sat double-counts once
    history accumulates. Marts read this view for "the" attribute row.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_dhis2_data_value) *
FROM {{ ref('sat_dhis2_data_value') }}
ORDER BY hk_dhis2_data_value, load_ts DESC
