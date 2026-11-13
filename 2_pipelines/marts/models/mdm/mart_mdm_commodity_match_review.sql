/*
    mart_mdm_commodity_match_review: the steward queue: every discovered eLMIS <-> commodity
    candidate with what a reviewer needs to accept or reject it. Accepting is a governance
    act recorded outside dbt; this mart is rebuilt from the candidates on every run.
*/
{{ config(materialized='view', schema='bi_mdm') }}

SELECT candidate_rank, score, match_rule, review_status,
       elmis_product_code, elmis_product_name, generic_name, strength, tracer,
       commodity_id, commodity_name, programme_id, discovered_at
FROM {{ ref('mdm_commodity_match_candidates') }}
ORDER BY score DESC, elmis_product_code, candidate_rank
