/*
    v_sat_hcmis_facility_current: the latest sat_hcmis_facility record per hk_hcmis_facility.
    Satellites are insert-only: a changed record lands as a new row for the
    same hash key, so any consumer joining the base sat double-counts once
    history accumulates. Marts read this view for "the" attribute row.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_hcmis_facility) *
FROM {{ ref('sat_hcmis_facility') }}
ORDER BY hk_hcmis_facility, load_ts DESC
