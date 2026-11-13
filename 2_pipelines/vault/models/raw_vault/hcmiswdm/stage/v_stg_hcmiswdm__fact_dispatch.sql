{{ config(materialized='view') }}
-- Dispatch movement stage. Feeds three structures off one landed table:
--   hub_hcmis_dispatch          (BK movement_id  -> hk_hcmis_dispatch)
--   sat_hcmis_dispatch          (movement payload, on hk_hcmis_dispatch)
--   lnk_hcmis_dispatch          (movement by from-hub by facility by period)
-- from_hub_id / to_node_id / period_id ride along as conformed FK hash keys so
-- the link joins the hubs/facility/period dimensions (and the movement stays
-- facility-grained, finer than the rest of the supply vault).
SELECT *,
       'HCMIS.fact_dispatch' AS record_source,
       {{ automate_dv.hash(columns=['movement_id'],   alias='hk_hcmis_dispatch') }},
       {{ automate_dv.hash(columns=['from_hub_id'],   alias='hk_hcmis_dispatch_hub') }},
       {{ automate_dv.hash(columns=['to_node_id'],    alias='hk_hcmis_dispatch_facility') }},
       {{ automate_dv.hash(columns=['period_id'],     alias='hk_hcmis_dispatch_period') }},
       {{ automate_dv.hash(
            columns=['movement_id','from_hub_id','to_node_id','period_id'],
            alias='hk_hcmis_dispatch_route') }},
       {{ automate_dv.hash(columns=sat_hashdiff('hcmis_dispatch'), alias='hd_hcmis_dispatch', is_hashdiff=true) }}
FROM {{ source('landing', 'stg_hcmiswdm__fact_dispatch') }}
