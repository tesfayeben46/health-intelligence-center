/*
    lnk_facility_indicator_period: the spine of the business vault.

    A single conformed grain that every cross-source BI mart can join
    through:
        (hk_facility_conformed by hk_indicator_conformed by hk_period_conformed)

    Why a business-vault link (not just SELECT through the same-as
    bridges + raw link at query time): consolidating the three-way
    conformed key into one materialised link gives every downstream
    mart a single foreign-key surface and avoids re-deriving the
    bridge in every mart. The measure (`value`) stays on the raw
    sat_dhis2_data_value; marts join through this link plus the thin
    dims (dim_facility / dim_period / dim_programme).

    Staged by v_facility_indicator_period_resolution; the link macro
    dedupes the per-category-option-combo fan-out to one edge per key.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_facility_indicator_period',
    src_fk=['hk_facility_conformed', 'hk_indicator_conformed', 'hk_period_conformed'],
    src_ldts='load_ts',
    src_source='record_source',
    source_model='v_facility_indicator_period_resolution'
) }}
