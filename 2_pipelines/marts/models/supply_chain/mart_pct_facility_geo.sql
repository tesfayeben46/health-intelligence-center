{{ config(materialized='table') }}
{#
    PCT facility geography: one row per eLMIS facility (Zambia), located and
    labelled by how trustworthy the location is (bi_supply.mart_pct_facility_geo).

    region_id is the province zone code, identical to the active profile's region
    codes, so the same geography filter narrows this mart and mart_pct_kpi.

    coord_basis: every row says where its point came from:
      source                  reported, inside Zambia's bounding box
      repaired_lat_sign       reported latitude was positive (northern hemisphere);
                              Zambia lies entirely south of the equator, so the
                              dropped minus sign is restored
      repaired_lng_sign       reported longitude was negative; restored the same way
      district_facility_mean  no usable point: mean of the usable points of the
                              facilities in its district (inferred: eLMIS districts
                              carry no coordinates of their own)
      district_mean_point_rejected  a reported point more than 200 km from the median
                              of its district's other points (the widest real district
                              spread is 172 km): junk or misplaced records, placed at
                              the district mean instead (inferred)
      province_facility_mean  district has no usable point: mean of the province's
      none                    nothing usable
    Points outside Zambia that a sign fix does not recover are rejected, never
    guessed; their facilities fall to the derived bases.

    lat / lng are facility GPS, classified restricted / generalize in
    column_classification.yaml like mart_ct_nodes.
#}

WITH bbox AS (
    SELECT -18.1::numeric AS lat_min, -8.2::numeric AS lat_max,
            21.9::numeric AS lng_min, 33.8::numeric AS lng_max
),

zone AS (
    SELECT h.code, s.name, s.parent_zone_code, s.geographic_level_code
    FROM {{ source('nhdw_vault_raw', 'hub_elmis_hist_zone') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_zone_current') }} s USING (hk_elmis_hist_zone)
),

facility AS (
    SELECT h.code AS facility_code, s.name AS facility_name, s.facility_type_code,
           d.code AS district_code, d.name AS district_name,
           p.code AS region_id, p.name AS region_name,
           s.latitude::numeric AS lat_reported, s.longitude::numeric AS lng_reported
    FROM {{ source('nhdw_vault_raw', 'hub_elmis_hist_facility') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_elmis_hist_facility_current') }} s USING (hk_elmis_hist_facility)
    LEFT JOIN zone d ON d.code = s.geographic_zone_code AND d.geographic_level_code = 'District'
    LEFT JOIN zone p ON p.code = d.parent_zone_code     AND p.geographic_level_code = 'Province'
),

located AS (
    SELECT f.*,
           CASE
             WHEN f.lat_reported BETWEEN b.lat_min AND b.lat_max
              AND f.lng_reported BETWEEN b.lng_min AND b.lng_max        THEN 'source'
             WHEN -f.lat_reported BETWEEN b.lat_min AND b.lat_max
              AND f.lng_reported BETWEEN b.lng_min AND b.lng_max        THEN 'repaired_lat_sign'
             WHEN f.lat_reported BETWEEN b.lat_min AND b.lat_max
              AND -f.lng_reported BETWEEN b.lng_min AND b.lng_max       THEN 'repaired_lng_sign'
           END AS point_basis
    FROM facility f CROSS JOIN bbox b
),

usable AS (
    SELECT l.*,
           CASE point_basis WHEN 'repaired_lat_sign' THEN -lat_reported ELSE lat_reported END AS lat_ok,
           CASE point_basis WHEN 'repaired_lng_sign' THEN -lng_reported ELSE lng_reported END AS lng_ok
    FROM located l
),

district_centre AS (
    -- robust centre (median) of each district's reported points
    SELECT district_code, percentile_cont(0.5) WITHIN GROUP (ORDER BY lat_ok) AS lat,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY lng_ok) AS lng
    FROM usable WHERE point_basis IS NOT NULL GROUP BY 1
),

checked AS (
    SELECT u.*,
           (u.point_basis IS NOT NULL AND 2 * 6371 * asin(sqrt(
                power(sin(radians(u.lat_ok - c.lat) / 2), 2)
              + cos(radians(c.lat)) * cos(radians(u.lat_ok)) * power(sin(radians(u.lng_ok - c.lng) / 2), 2))) > 200) AS far
    FROM usable u LEFT JOIN district_centre c USING (district_code)
),

district_mean AS (
    SELECT district_code, avg(lat_ok) AS lat, avg(lng_ok) AS lng
    FROM checked WHERE point_basis IS NOT NULL AND NOT far GROUP BY 1
),

province_mean AS (
    SELECT region_id, avg(lat_ok) AS lat, avg(lng_ok) AS lng
    FROM checked WHERE point_basis IS NOT NULL AND NOT far GROUP BY 1
),

reporting AS (
    SELECT DISTINCT facility_code
    FROM {{ ref('mart_elmis_hist_facility_product_month') }}
)

SELECT u.facility_code, u.facility_name, u.facility_type_code,
       u.district_code, u.district_name, u.region_id, u.region_name,
       round(CASE WHEN u.point_basis IS NOT NULL AND NOT u.far THEN u.lat_ok
                  ELSE coalesce(dm.lat, pm.lat) END, 6) AS lat,
       round(CASE WHEN u.point_basis IS NOT NULL AND NOT u.far THEN u.lng_ok
                  ELSE coalesce(dm.lng, pm.lng) END, 6) AS lng,
       CASE WHEN u.point_basis IS NOT NULL AND NOT u.far THEN u.point_basis
            WHEN u.far AND dm.lat IS NOT NULL THEN 'district_mean_point_rejected'
            WHEN dm.lat IS NOT NULL THEN 'district_facility_mean'
            WHEN pm.lat IS NOT NULL THEN 'province_facility_mean'
            ELSE 'none' END AS coord_basis,
       (r.facility_code IS NOT NULL) AS reports_in_window
FROM checked u
LEFT JOIN district_mean dm ON dm.district_code = u.district_code
LEFT JOIN province_mean pm ON pm.region_id = u.region_id
LEFT JOIN reporting r      ON r.facility_code = u.facility_code
