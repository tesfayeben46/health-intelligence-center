/*
    lnk_hcmis_vaccine_event: HCMIS vaccine-module daily fact link.
    FK mapping (vaccine_record by item by unit by environment).

    `date` is a dependent-child key: it is part of the link grain (hashed
    into hk_hcmis_vaccine_event by the stage view) so it must be stored on
    the link; otherwise the PK could not be re-derived from the link's own
    columns (DV2.0 auditability).
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_hcmis_vaccine_event',
    src_fk=['hk_hcmis_vaccine_record','hk_hcmis_item','hk_hcmis_unit','hk_hcmis_environment'],
    src_extra_columns=['date'],
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_hcmiswdm__vaccine_dailyrecord'
) }}
