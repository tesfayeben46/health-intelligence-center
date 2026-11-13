{{ config(materialized='view') }}

-- eLMIS v2 history (Zambia, pattern-preserving extract; see
-- docs/data-provenance.md). Source-aware: elmis_hist entities are never
-- conflated with the OpenLMIS v3 lane's openlmis ones.

WITH src AS (
    SELECT
        programme_code,
        product_code,
        source_id,
        product_category_code,
        doses_per_month,
        active,
        current_price,
        display_order,
        full_supply
    FROM {{ source('raw', 'elmis_hist__reference_programme_product') }}
),

hashed AS (
    SELECT *,
        {{ source_record_hash(['programme_code', 'product_code', 'source_id', 'product_category_code', 'doses_per_month', 'active', 'current_price', 'display_order', 'full_supply']) }} AS source_record_hash
    FROM src
),

deduped AS (
    SELECT *,
        row_number() OVER (PARTITION BY programme_code, product_code ORDER BY source_record_hash) AS rn
    FROM hashed
)

SELECT
    programme_code, product_code, source_id, product_category_code, doses_per_month, active, current_price, display_order, full_supply,
    now64() AS load_ts,
    source_record_hash
FROM deduped
WHERE rn = 1
