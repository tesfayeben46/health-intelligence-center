/*
    lnk_mbrana_stock_event: mBrana USSD-reported SOH fact link.
    FK mapping (soh_log by ussd_facility by commodity).

    `soh_log_id` is a dependent-child key: it is part of the link grain
    (hashed into hk_mbrana_stock_event by the stage view) so it must be
    stored on the link; otherwise the PK could not be re-derived from the
    link's own columns (DV2.0 auditability). It is a key, not a descriptive
    attribute, so it does not appear in sat_mbrana_stock_event's payload.
*/
{{ config(materialized='incremental', on_schema_change='append_new_columns') }}

{{ automate_dv.link(
    src_pk='hk_mbrana_stock_event',
    src_fk=['hk_mbrana_ussd_facility','hk_mbrana_commodity'],
    src_extra_columns=['soh_log_id'],
    src_ldts='load_ts', src_source='record_source',
    source_model='v_stg_mbrana__mflowussd_sohlogs'
) }}
