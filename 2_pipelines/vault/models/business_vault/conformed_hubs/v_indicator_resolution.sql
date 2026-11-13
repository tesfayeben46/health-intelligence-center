/*
    v_indicator_resolution: HMIS indicators conformed, and the AutomateDV
    stage for hub_indicator_conformed.
    Sources:
      - DHIS2 dataElement (the canonical source, read via the current-record
        view). sat.code holds the shared indicator_id (e.g. ANC1,
        TB_NOTIFIED, DPT3).
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

WITH src AS (
    SELECT
        sat.hk_dhis2_data_element AS source_hk,
        'DHIS2.dataElement' AS source_system,
        sat.name AS source_name,
        sat.code AS source_code,
        sat.code AS indicator_id,
        sat.value_type AS value_type,
        sat.aggregation_type AS aggregation_type,
        sat.load_ts
    FROM {{ ref('v_sat_dhis2_data_element_current') }} sat
    WHERE sat.code IS NOT NULL
)
SELECT
    src.*,
    {{ automate_dv.hash(columns=['indicator_id'], alias='hk_indicator_conformed') }},
    'BV.indicator_resolution' AS record_source
FROM src
