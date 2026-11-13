/*
    EthioEMR Observation (FHIR R4): the clinical-result facts. Dedupes on
    FHIR id. The `value_*` columns surface the polymorphic FHIR Observation.value[x]
    in flat form; downstream marts pick whichever is non-null.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT
        id,
        meta__version_id     AS meta_version_id,
        meta__last_updated   AS meta_last_updated,
        meta__source         AS meta_source,
        status,
        code__text           AS code_text,
        subject__reference   AS subject_reference,
        -- FHIR "Patient/<id>" -> "<id>". FK to hub_ethioemr_patient.
        nullIf(extract(toString(subject__reference), '^Patient/(.+)$'), '') AS subject_patient_fhir_id,
        subject__display     AS subject_display,
        encounter__reference AS encounter_reference,
        -- FHIR "Encounter/<id>" -> "<id>". FK to hub_ethioemr_encounter.
        nullIf(extract(toString(encounter__reference), '^Encounter/(.+)$'), '') AS encounter_fhir_id,
        effective_date_time  AS effective_dt,
        effective_period__start AS effective_period_start,
        effective_period__end   AS effective_period_end,
        issued,
        value_quantity__value         AS value_quantity_value,
        value_quantity__unit          AS value_quantity_unit,
        value_quantity__system        AS value_quantity_system,
        value_quantity__code          AS value_quantity_code,
        value_codeable_concept__text  AS value_codeable_concept_text,
        value_string,
        value_date_time              AS value_date_time,
        method__text                  AS method_text
    FROM {{ source('raw', 'ethioemr__observation') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['id','meta_version_id','meta_last_updated','meta_source','status','code_text','subject_reference','subject_patient_fhir_id','subject_display','encounter_reference','encounter_fhir_id','effective_dt','effective_period_start','effective_period_end','issued','value_quantity_value','value_quantity_unit','value_quantity_system','value_quantity_code','value_codeable_concept_text','value_string','value_date_time','method_text']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY id ORDER BY meta_last_updated DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT
    id, meta_version_id, meta_last_updated, meta_source,
    status, code_text,
    subject_reference, subject_patient_fhir_id, subject_display,
    encounter_reference, encounter_fhir_id,
    effective_dt, effective_period_start, effective_period_end, issued,
    value_quantity_value, value_quantity_unit, value_quantity_system, value_quantity_code,
    value_codeable_concept_text, value_string, value_date_time, method_text,
    now64() AS load_ts,
    source_record_hash
FROM deduped WHERE rn = 1
