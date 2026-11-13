/*
    lnk_dhis2_data_value: the canonical fact-grain link, joining the five
    business entities that uniquely identify a DHIS2 data value:

        data_element + period + org_unit + cat_opt_combo + attr_opt_combo

    This matches the staging dedupe grain exactly (one row per 5-tuple).
    data_set is not part of the value's identity; see the stage view.

    FK columns (exact mapping; the order here is the documented contract):
        hk_dhis2_data_element              <- v_stg.data_element  -> hub_dhis2_data_element
        hk_dhis2_period                    <- v_stg.period         -> hub_dhis2_period
        hk_dhis2_org_unit                  <- v_stg.org_unit       -> hub_dhis2_org_unit
        hk_dhis2_category_option_combo     <- v_stg.category_option_combo
                                                                  -> hub_dhis2_category_option_combo
        hk_dhis2_attribute_option_combo    <- v_stg.attribute_option_combo
                                                                  -> hub_dhis2_category_option_combo
                                                                    (same hub, two roles)

    Insert-on-change: the AutomateDV `link` macro inserts only on rows whose
    composite hash_key (hk_dhis2_data_value) is not already present.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_dhis2_data_value',
    src_fk=[
        'hk_dhis2_data_element',
        'hk_dhis2_period',
        'hk_dhis2_org_unit',
        'hk_dhis2_category_option_combo',
        'hk_dhis2_attribute_option_combo'
    ],
    src_ldts='load_ts',
    src_source='record_source',
    source_model='v_stg_dhis2__data_value_sets'
) }}
