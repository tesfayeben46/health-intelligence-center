/*
    DHIS2 dataValueSets: typed, deduped staging.

    Source: nhdw_staging.raw_dhis2__data_value_sets (s3() view over Parquet)

    What this model does:
      1. Casts each column to its target type. The parquet from dlt arrives
         with all strings as Nullable(String) and timestamps as DateTime64.
      2. Dedupes on the natural key. If a re-ingest brings the same
         (data_element, period, org_unit, category_option_combo,
          attribute_option_combo) twice, the most recent `last_updated`
         wins.
      3. Adds `load_ts` (when this view was last refreshed) and
         `source_record_hash` (cheap change-detection signal for the
         vault loader).

    Note: `comment` and `follow_up` fields are part of the DHIS2 API but
    are absent from the ingested data (all NULL, so dlt drops the columns).
    Add them back when an ingest carries non-null values.

    Materialisation: view: zero rows persisted in nhdw_staging.
*/

{{ config(materialized='view') }}

WITH src AS (
    SELECT
        data_element,
        period,
        org_unit,
        category_option_combo,
        attribute_option_combo,
        value,
        stored_by,
        created,
        last_updated,
        data_set
    FROM {{ source('raw', 'dhis2__data_value_sets') }}
),
hashed AS (
    -- Fingerprint over every projected source column, computed before the
    -- dedupe so it can serve as the deterministic final tie-breaker below.
    SELECT *, {{ source_record_hash(['data_element','period','org_unit','category_option_combo','attribute_option_combo','value','stored_by','created','last_updated','data_set']) }} AS source_record_hash
    FROM src
),
deduped AS (
    SELECT
        *,
        row_number() OVER (
            PARTITION BY data_element, period, org_unit, category_option_combo, attribute_option_combo
            ORDER BY last_updated DESC NULLS LAST, source_record_hash
        ) AS rn
    FROM hashed
)
SELECT
    data_element,
    period,
    org_unit,
    category_option_combo,
    attribute_option_combo,
    value,
    stored_by,
    created,
    last_updated,
    data_set,
    now64() AS load_ts,
    source_record_hash
FROM deduped
WHERE rn = 1
