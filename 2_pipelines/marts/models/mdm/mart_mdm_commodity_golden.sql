/*
    mart_mdm_commodity_golden: the golden-record view of the commodity master:
    one row per entity-model commodity with, per source system, how it is known there
    (HCMIS/mBrana via the vault's same-as resolution; eLMIS via the discovered candidates,
    which are labelled as candidates, never as facts). The steward dashboard reads this.
*/
{{ config(materialized='table', schema='bi_mdm') }}

WITH golden AS (
    -- the vault's conformed commodity key is the normalised name (v_commodity_resolution.conformed_bk)
    SELECT commodity_id, commodity_name, programme_id,
           lower(regexp_replace(commodity_name, '[^a-zA-Z0-9 ]', '', 'g')) AS conformed_bk
    FROM {{ ref('commodity_reference') }}
),
resolved AS (
    SELECT conformed_bk, source_system, count(*) AS source_records
    FROM {{ source('nhdw_vault_business', 'v_commodity_resolution') }}
    GROUP BY 1, 2
),
elmis AS (
    SELECT commodity_id,
           count(*)                                   AS elmis_candidates,
           max(score)                                 AS elmis_best_score,
           (array_agg(elmis_product_name ORDER BY score DESC))[1] AS elmis_best_candidate
    FROM {{ ref('mdm_commodity_match_candidates') }}
    GROUP BY 1
)
SELECT g.commodity_id, g.commodity_name, g.programme_id, g.conformed_bk,
       COALESCE((SELECT source_records FROM resolved r WHERE r.conformed_bk = g.conformed_bk AND r.source_system = 'HCMIS'), 0)  AS hcmis_records,
       COALESCE((SELECT source_records FROM resolved r WHERE r.conformed_bk = g.conformed_bk AND r.source_system = 'mBrana'), 0) AS mbrana_records,
       COALESCE(e.elmis_candidates, 0) AS elmis_candidates,
       e.elmis_best_score, e.elmis_best_candidate,
       'entity-model'::text AS golden_source
FROM golden g LEFT JOIN elmis e USING (commodity_id)
