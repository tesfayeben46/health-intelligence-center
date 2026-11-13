/*
    bi_facility_distribution: internal view. Count facilities per region
    per tier (the FAC-% code token, not a hardcoded org-unit level; the
    seeded tree has no level 6; all facilities are FAC-% leaves).

    Column semantics (SUM across a region's rows to aggregate):
      n_facilities        every FAC-% org unit in the tier row
      n_health_facilities care-delivery facilities (everything except
                          DS drug stores, which are supply points, not care sites)
      n_phcus             PHCU anchors. Ethiopia's Primary Health Care
                          Unit = 1 health center (HC) + its satellite
                          health posts, so PHCUs are counted as HCs.

    Consumed by Internal BI (R-DEC, R-CONS-ANA), the public variant at
    bi_public.bi_facility_distribution_public, and
    bi_staging.bi_facility_phcu_ratio. No k-anonymity filter here:
    internal users see exact counts.
*/

SELECT
    region_id,
    region_name,
    facility_tier,
    COUNT(*)                                              AS n_facilities,
    COUNT(*) FILTER (WHERE facility_tier <> 'DS')         AS n_health_facilities,
    COUNT(*) FILTER (WHERE facility_tier = 'HC')          AS n_phcus
FROM {{ ref('bi_dhis2_facility_with_region') }}
WHERE region_id IS NOT NULL
GROUP BY region_id, region_name, facility_tier
ORDER BY region_name, facility_tier
