/*
    v_stg_dhis2__data_elements_from_facts: data-element BKs observed in
    the data_value_sets fact, aliased to hub_dhis2_data_element's NK column
    (`id`) so the hub can union this feed with the dim stage. Closes the
    orphan-FK hole for elements that appear in facts before the dim.
*/
{{ config(materialized='view') }}
SELECT
    hk_dhis2_data_element,
    data_element AS id,
    load_ts,
    record_source
FROM {{ ref('v_stg_dhis2__data_value_sets') }}
