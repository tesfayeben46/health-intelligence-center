/*
    v_stg_dhis2__org_units_from_facts: org-unit BKs observed in the
    data_value_sets fact, aliased to hub_dhis2_org_unit's NK column (`id`)
    so the hub can union this feed with the dim stage. Covers org units
    that appear in facts before (or without ever) appearing in the dim
    extract; otherwise lnk_dhis2_data_value would carry orphan FKs.
*/
{{ config(materialized='view') }}
SELECT
    hk_dhis2_org_unit,
    org_unit AS id,
    load_ts,
    record_source
FROM {{ ref('v_stg_dhis2__data_value_sets') }}
