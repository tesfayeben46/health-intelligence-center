{{ config(materialized='table') }}
{#
    Supply Control Tower nodes (bi_supply.mart_ct_nodes), Zambia eLMIS.
    Hubs are district stores: the District Health Office of each district (the
    district level of the MSL -> district -> facility chain). The archive records
    no supply links, so the facility -> district-store link is inferred
    (hub_basis says how each hub was placed). Facilities are those with a planned
    order next month (bi_supply.mart_ct_movements). GPS columns lat/lng keep the
    restricted/generalize classification (masked by trust level at the gateway).
#}

WITH geo AS (
    SELECT * FROM {{ ref('mart_pct_facility_geo') }}
),

ftype AS (
    SELECT h.code AS facility_type_code, s.name AS type_name
    FROM {{ source('nhdw_vault_raw', 'hub_elmis_hist_facility_type') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_facility_type_current') }} s USING (hk_elmis_hist_facility_type)
),

centre AS (
    -- where the district's facilities actually are (GPS-sourced points only)
    SELECT district_code, avg(lat) AS lat, avg(lng) AS lng
    FROM geo WHERE coord_basis = 'source' GROUP BY district_code
),

dho AS (
    -- one store per district: a DHO with real GPS, nearest the district's facilities
    -- (the archive holds stray DHO-typed records far outside their district)
    SELECT DISTINCT ON (g.district_code) g.district_code, g.facility_code, g.facility_name, g.lat, g.lng, g.coord_basis
    FROM geo g LEFT JOIN centre c USING (district_code)
    WHERE g.facility_type_code = 'DHO'
    ORDER BY g.district_code, (g.coord_basis = 'source') DESC,
             power(g.lat - coalesce(c.lat, g.lat), 2) + power(g.lng - coalesce(c.lng, g.lng), 2), g.facility_code
),

district AS (
    SELECT district_code, max(district_name) AS district_name, max(region_name) AS region_name,
           max(region_id) AS region_id, avg(lat) AS lat, avg(lng) AS lng
    FROM geo GROUP BY district_code
),

ordering AS (
    SELECT to_node_id AS facility_code, count(*) AS lines, max(from_hub_id) AS hub_id
    FROM {{ ref('mart_ct_movements') }} GROUP BY to_node_id
),

hub AS (
    SELECT 'H-' || coalesce(dho.facility_code, 'D' || d.district_code)       AS hub_id,
           coalesce(dho.facility_name, d.district_name || ' district store') AS name,
           d.district_name                                                   AS short_name,
           d.region_name                                                     AS zone,
           d.district_code,
           coalesce(dho.lat, d.lat)                                          AS lat,
           coalesce(dho.lng, d.lng)                                          AS lng,
           CASE WHEN dho.facility_code IS NULL THEN 'district_centre'
                WHEN dho.coord_basis = 'source' THEN 'district_health_office'
                ELSE 'district_health_office_inferred_location' END           AS hub_basis
    FROM district d
    LEFT JOIN dho USING (district_code)
    WHERE 'H-' || coalesce(dho.facility_code, 'D' || d.district_code) IN (SELECT hub_id FROM ordering)
)

SELECT
    'hub'::text                        AS node_kind,
    hub.hub_id                         AS node_id,
    hub.name,
    NULL::text                         AS region,
    NULL::text                         AS region_id,
    NULL::text                         AS node_type,
    round(hub.lng::numeric, 6)         AS lng,
    round(hub.lat::numeric, 6)         AS lat,
    NULL::int                          AS line_count,
    NULL::int                          AS store_cap,
    hub.short_name,
    hub.zone,
    true                               AS is_hub,
    NULL::int                          AS vehicles,
    hub.district_code,
    hub.hub_basis                      AS location_basis
FROM hub
UNION ALL
SELECT
    'facility'::text                   AS node_kind,
    g.facility_code                    AS node_id,
    g.facility_name                    AS name,
    g.region_name                      AS region,
    g.region_id,
    coalesce(t.type_name, g.facility_type_code) AS node_type,
    g.lng::numeric                     AS lng,
    g.lat::numeric                     AS lat,
    o.lines::int                       AS line_count,
    NULL::int                          AS store_cap,
    NULL::text                         AS short_name,
    NULL::text                         AS zone,
    NULL::boolean                      AS is_hub,
    NULL::int                          AS vehicles,
    g.district_code,
    g.coord_basis                      AS location_basis
FROM geo g
JOIN ordering o USING (facility_code)
LEFT JOIN ftype t USING (facility_type_code)
