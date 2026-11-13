/*
    v_stg_dhis2__category_option_combos_from_facts: category-option-combo
    BKs observed in the data_value_sets fact, aliased to
    hub_dhis2_category_option_combo's NK column (`id`). The fact references
    the COC hub in two roles (category_option_combo and
    attribute_option_combo; see lnk_dhis2_data_value); both roles are
    unioned here so either can introduce a new hub row.
*/
{{ config(materialized='view') }}
SELECT
    hk_dhis2_category_option_combo,
    category_option_combo AS id,
    load_ts,
    record_source
FROM {{ ref('v_stg_dhis2__data_value_sets') }}
UNION ALL
SELECT
    hk_dhis2_attribute_option_combo AS hk_dhis2_category_option_combo,
    attribute_option_combo          AS id,
    load_ts,
    record_source
FROM {{ ref('v_stg_dhis2__data_value_sets') }}
