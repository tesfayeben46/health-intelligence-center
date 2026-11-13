/*
    EthioEMR Encounter (FHIR R4). Dedupes on FHIR id.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT
        id,
        meta__version_id        AS meta_version_id,
        meta__last_updated      AS meta_last_updated,
        meta__source            AS meta_source,
        status,
        class__system           AS class_system,
        class__code             AS class_code,
        class__display          AS class_display,
        subject__reference      AS subject_reference,
        -- Parse FHIR reference "Patient/<id>" -> "<id>". Becomes the FK to
        -- hub_ethioemr_patient in the vault's lnk_encounter_patient link.
        -- extract() returns '' on no-match and tolerates Nullable input
        -- (splitByChar can't index into Nullable arrays).
        nullIf(extract(toString(subject__reference), '^Patient/(.+)$'), '') AS subject_patient_fhir_id,
        subject__display        AS subject_display,
        subject__type           AS subject_type,
        subject__identifier__system AS subject_identifier_system,
        subject__identifier__value  AS subject_identifier_value,
        period__start           AS period_start,
        period__end             AS period_end,
        part_of__reference      AS part_of_reference,
        service_provider__reference        AS service_provider_reference,
        service_provider__display          AS service_provider_display,
        service_provider__identifier__system AS service_provider_identifier_system,
        service_provider__identifier__value  AS service_provider_identifier_value
    FROM {{ source('raw', 'ethioemr__encounter') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['id','meta_version_id','meta_last_updated','meta_source','status','class_system','class_code','class_display','subject_reference','subject_patient_fhir_id','subject_display','subject_type','subject_identifier_system','subject_identifier_value','period_start','period_end','part_of_reference','service_provider_reference','service_provider_display','service_provider_identifier_system','service_provider_identifier_value']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY id ORDER BY meta_last_updated DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT
    id, meta_version_id, meta_last_updated, meta_source,
    status, class_system, class_code, class_display,
    subject_reference, subject_patient_fhir_id, subject_display, subject_type,
    subject_identifier_system, subject_identifier_value,
    period_start, period_end, part_of_reference,
    service_provider_reference, service_provider_display,
    service_provider_identifier_system, service_provider_identifier_value,
    now64() AS load_ts,
    source_record_hash
FROM deduped WHERE rn = 1
