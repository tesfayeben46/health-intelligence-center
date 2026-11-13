/*
    mdm_commodity_match_candidates: the MDM link table of discovered
    relationships, i.e. candidate matches between eLMIS v2 products (Zambian
    archive, kept standalone) and the Ethiopian entity-model commodities
    (the golden records = hub_commodity_conformed / commodity_reference).

    Nothing here is a same-as link. A candidate becomes a link only after a steward
    accepts it (review_status), and even then as a labelled mapping at the mart layer
    (as a view); the vault keeps native truth. Facilities and geography are
    never matched: a drug name is shareable across countries, a facility is not.

    Matching: normalised names (lower, alphanumerics), pg_trgm similarity on the full
    name plus token overlap on the generic name. Top 3 candidates per eLMIS product
    with score >= 0.35; the steward mart shows them.
*/
{{ config(materialized='table', schema='bi_mdm') }}

WITH golden AS (
    SELECT commodity_id, commodity_name, programme_id,
           lower(regexp_replace(commodity_name, '[^a-zA-Z0-9 ]', '', 'g')) AS norm_name
    FROM {{ ref('commodity_reference') }}
),
elmis AS (
    SELECT h.code AS elmis_product_code,
           ps.primary_name AS elmis_product_name, ps.generic_name, ps.strength, ps.tracer,
           lower(regexp_replace(COALESCE(ps.primary_name, ''), '[^a-zA-Z0-9 ]', '', 'g')) AS norm_name,
           lower(regexp_replace(COALESCE(ps.generic_name, ps.primary_name, ''), '[^a-zA-Z0-9 ]', '', 'g')) AS norm_generic
    FROM {{ source('nhdw_vault_raw', 'hub_elmis_hist_product') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_product_current') }} ps USING (hk_elmis_hist_product)
),
scored AS (
    SELECT e.elmis_product_code, e.elmis_product_name, e.generic_name, e.strength, e.tracer,
           g.commodity_id, g.commodity_name, g.programme_id,
           greatest(similarity(e.norm_name, g.norm_name), similarity(e.norm_generic, g.norm_name)) AS score,
           CASE WHEN similarity(e.norm_generic, g.norm_name) >= similarity(e.norm_name, g.norm_name)
                THEN 'trgm_generic_name' ELSE 'trgm_primary_name' END AS match_rule
    FROM elmis e CROSS JOIN golden g
),
ranked AS (
    SELECT *, row_number() OVER (PARTITION BY elmis_product_code ORDER BY score DESC, commodity_id) AS rnk
    FROM scored WHERE score >= 0.35
)
SELECT elmis_product_code, elmis_product_name, generic_name, strength, tracer,
       commodity_id, commodity_name, programme_id,
       round(score::numeric, 3) AS score, match_rule, rnk AS candidate_rank,
       'pending'::text AS review_status,          -- steward decision lives outside dbt (ops DB); default pending
       now() AS discovered_at
FROM ranked WHERE rnk <= 3
