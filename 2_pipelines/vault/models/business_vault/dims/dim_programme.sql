/*
    dim_programme: thin dimension view over the conformed programme hub.
    One row per programme (HIV | TB | EPI | MNCH | NCD | MALARIA | FP |
    NUTRITION | OTHER). n_indicators = live indicator codes mapped to the
    programme by v_programme_resolution (the indicator-code -> programme
    single source of truth); marts filter/aggregate by programme here
    instead of re-deriving the mapping locally.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

WITH members AS (
    SELECT programme_id, COUNT(DISTINCT source_code) AS n_indicators
    FROM {{ ref('v_programme_resolution') }}
    GROUP BY programme_id
)
SELECT
    h.hk_programme_conformed,
    h.programme_id,
    COALESCE(m.n_indicators, 0) AS n_indicators,
    h.load_ts
FROM {{ ref('hub_programme_conformed') }} h
LEFT JOIN members m USING (programme_id)
