/*
    v_sat_dhis2_data_element_current: the latest sat_dhis2_data_element record per hk_dhis2_data_element.
    Satellites are insert-only: a changed record lands as a new row for the
    same hash key, so any consumer joining the base sat double-counts once
    history accumulates. Marts read this view for "the" attribute row.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_dhis2_data_element) *
FROM {{ ref('sat_dhis2_data_element') }}
ORDER BY hk_dhis2_data_element, load_ts DESC
