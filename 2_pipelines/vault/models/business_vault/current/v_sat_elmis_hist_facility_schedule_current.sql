/*
    v_sat_elmis_hist_facility_schedule_current: latest sat_elmis_hist_facility_schedule per hk_elmis_hist_facility_schedule.
    Satellites are insert-only, so marts read this current-record view.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

SELECT DISTINCT ON (hk_elmis_hist_facility_schedule) *
FROM {{ ref('sat_elmis_hist_facility_schedule') }}
ORDER BY hk_elmis_hist_facility_schedule, load_ts DESC
