{{ config(materialized='table') }}
{#
    Supply Control Tower runs (bi_supply.mart_ct_vehicle_runs), Zambia eLMIS.
    One run per district store and period, visiting every facility with a planned
    order in the sweep order of mart_ct_movements. Distances are straight-line
    (no road network); the baseline is one out-and-back trip per facility.
    No vehicle, capacity, cost or schedule data exists: those columns are NULL.
    path_json is NULL: a path of facility coordinates would bypass the
    GPS masking applied to mart_ct_nodes; the map draws routes from the (masked)
    node coordinates instead.
#}

WITH mv AS (
    SELECT * FROM {{ ref('mart_ct_movements') }} WHERE planned
),

nodes AS (
    SELECT node_id, lat, lng FROM {{ ref('mart_ct_nodes') }}
),

stop AS (
    SELECT m.vehicle_run_id, m.period_id, m.from_hub_id, m.to_node_id, min(m.sequence_index) AS seq,
           sum(m.units) AS drop_units, count(*) AS line_count, max(m.baseline_km) AS lane_km,
           json_agg(json_build_object('drugShort', m.drug_short, 'units', m.units) ORDER BY m.units DESC) AS lines
    FROM mv m
    GROUP BY m.vehicle_run_id, m.period_id, m.from_hub_id, m.to_node_id
),

placed AS (
    SELECT s.*, n.lat, n.lng, h.lat AS hub_lat, h.lng AS hub_lng,
           lag(n.lat) OVER w AS prev_lat, lag(n.lng) OVER w AS prev_lng,
           row_number() OVER (PARTITION BY s.vehicle_run_id ORDER BY s.seq DESC) = 1 AS is_last
    FROM stop s
    JOIN nodes n ON n.node_id = s.to_node_id
    JOIN nodes h ON h.node_id = s.from_hub_id
    WINDOW w AS (PARTITION BY s.vehicle_run_id ORDER BY s.seq)
),

legs AS (
    SELECT p.*,
           2 * 6371 * asin(sqrt(power(sin(radians(p.lat - coalesce(p.prev_lat, p.hub_lat)) / 2), 2)
               + cos(radians(coalesce(p.prev_lat, p.hub_lat))) * cos(radians(p.lat))
               * power(sin(radians(p.lng - coalesce(p.prev_lng, p.hub_lng)) / 2), 2))) AS leg_km,
           CASE WHEN p.is_last THEN 2 * 6371 * asin(sqrt(power(sin(radians(p.hub_lat - p.lat) / 2), 2)
               + cos(radians(p.lat)) * cos(radians(p.hub_lat)) * power(sin(radians(p.hub_lng - p.lng) / 2), 2)))
               ELSE 0 END AS return_km
    FROM placed p
),

cum AS (
    SELECT l.*, sum(l.leg_km) OVER (PARTITION BY l.vehicle_run_id ORDER BY l.seq) AS cum_km FROM legs l
)

SELECT
    vehicle_run_id                                      AS run_id,
    period_id,
    NULL::text                                          AS vehicle_type,
    NULL::int                                           AS capacity_units,
    from_hub_id                                         AS origin_hub_id,
    round(sum(leg_km) + sum(return_km))::int            AS total_road_km,
    sum(drop_units)::int                                AS total_units,
    NULL::numeric                                       AS fill_rate,
    NULL::int                                           AS cost_total,
    NULL::numeric                                       AS cost_per_unit,
    round(sum(2 * lane_km) - (sum(leg_km) + sum(return_km)))::int AS km_saved_vs_baseline,
    NULL::text                                          AS depart,
    NULL::text                                          AS eta,
    'Planned'::text                                     AS status,
    json_agg(json_build_object('nodeId', to_node_id, 'seq', seq, 'legKm', round(leg_km),
             'cumKm', round(cum_km), 'dropUnits', drop_units, 'lines', lines, 'lineCount', line_count,
             'etaMin', NULL, 'serviceMin', NULL) ORDER BY seq)::text AS stops_json,
    NULL::text                                          AS path_json
FROM cum
GROUP BY vehicle_run_id, period_id, from_hub_id
