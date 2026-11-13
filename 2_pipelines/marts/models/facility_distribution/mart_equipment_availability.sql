/*
    mart_equipment_availability: national equipment inventory by type, from the
    MFR FHIR Device satellite. FHIR Device.status maps to operational condition:
        active           -> working
        inactive         -> broken (present but not in service)
        entered-in-error -> missing (recorded but unaccounted for)
    Backs rm-equipment (stacked bar, national). One row per equipment_type.

    Reads the current-record view (sats are insert-only) so a re-ingested device
    is counted once, not once per historical status change.

    Schema (bi_facility) + materialization inherited from dbt_project.yml, like
    the sibling facility marts; no per-model config override.
*/
SELECT
    equipment_type,
    count(*) FILTER (WHERE status = 'active')           AS working,
    count(*) FILTER (WHERE status = 'inactive')         AS broken,
    count(*) FILTER (WHERE status = 'entered-in-error') AS missing,
    count(*)                                            AS total
FROM {{ source('nhdw_vault_business', 'v_sat_mfr_device_current') }}
WHERE equipment_type IS NOT NULL
GROUP BY equipment_type
ORDER BY total DESC
