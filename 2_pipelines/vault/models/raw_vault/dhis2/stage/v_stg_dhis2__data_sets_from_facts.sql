/*
    v_stg_dhis2__data_sets_from_facts: data-set BKs observed in the
    data_value_sets fact, aliased to hub_dhis2_data_set's NK column (`id`)
    so the hub can union this feed with the dim stage. Closes the orphan-BK
    hole for data sets that appear in facts before the dim.
*/
{{ config(materialized='view') }}
SELECT
    hk_dhis2_data_set,
    data_set AS id,
    load_ts,
    record_source
FROM {{ ref('v_stg_dhis2__data_value_sets') }}
