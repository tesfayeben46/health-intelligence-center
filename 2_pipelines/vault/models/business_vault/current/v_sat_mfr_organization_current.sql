/*
    v_sat_mfr_organization_current: the latest sat_mfr_organization record per hk_mfr_organization.
    Satellites are insert-only: a changed record lands as a new row for the
    same hash key, so any consumer joining the base sat double-counts once
    history accumulates. Marts read this view for "the" attribute row.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_mfr_organization) *
FROM {{ ref('sat_mfr_organization') }}
ORDER BY hk_mfr_organization, load_ts DESC
