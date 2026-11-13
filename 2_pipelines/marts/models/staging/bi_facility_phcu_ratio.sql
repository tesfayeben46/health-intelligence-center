/*
    Sample staging mart: facility-to-PHCU ratio per region.
    Lands in bi_staging.bi_facility_phcu_ratio. Promoted to bi_facility
    via the gateway graduation flow after a governance click.

    n_facilities = care-delivery facilities (bi_facility_distribution's
    n_health_facilities: every FAC-% tier except DS drug stores);
    n_phcus = PHCU anchors (health centers; Ethiopia's PHCU is 1 HC +
    its satellite health posts). Regions with no HC (e.g. a city admin
    with a single facility) are excluded by the n_phcus > 0 filter.

    Approval criteria (R-STEW must confirm):
      - region_name is from the conformed list (no typo regions)
      - n_facilities + n_phcus are sourced exclusively from DHIS2
        org_unit data (no clinical data mixed in)
      - The ratio column is a derived metric, not a raw datum.
*/

SELECT
    region_name,
    n_health_facilities                                  AS n_facilities,
    n_phcus,
    ROUND(
        n_health_facilities::numeric / NULLIF(n_phcus, 0),
        2
    )                                                    AS facilities_per_phcu
FROM (
    SELECT region_name,
           SUM(n_health_facilities) AS n_health_facilities,
           SUM(n_phcus)             AS n_phcus
    FROM {{ ref('bi_facility_distribution') }}
    GROUP BY region_name
) agg
WHERE n_phcus > 0
ORDER BY facilities_per_phcu DESC
