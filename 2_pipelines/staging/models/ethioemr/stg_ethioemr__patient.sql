/*
    EthioEMR Patient (FHIR R4 resource). Dedupes on FHIR id, keeps the latest
    meta_last_updated.

    FHIR `meta.last_updated` is the canonical write timestamp; `meta.version_id`
    is the FHIR-resource version counter on the source server.
*/
{{ config(materialized='view') }}

WITH src AS (
    SELECT
        id,
        meta__version_id   AS meta_version_id,
        meta__last_updated AS meta_last_updated,
        meta__source       AS meta_source,
        gender,
        birth_date,
        text__status       AS text_status,
        text__div          AS text_div
    FROM {{ source('raw', 'ethioemr__patient') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['id','meta_version_id','meta_last_updated','meta_source','gender','birth_date','text_status','text_div']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT *, row_number() OVER (PARTITION BY id ORDER BY meta_last_updated DESC NULLS LAST, source_record_hash) AS rn
    FROM hashed
)
SELECT
    id, meta_version_id, meta_last_updated, meta_source,
    gender, birth_date, text_status, text_div,
    now64() AS load_ts,
    source_record_hash
FROM deduped WHERE rn = 1
