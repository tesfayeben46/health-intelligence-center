/*
    mart_reported_vs_recorded: DHIS2-reported vs EMR-recorded agreement,
    month by region by indicator.

    Backs sv-agreement-donut + sv-data-trust-table ("Reported vs Recorded").
    EMR side: the consent-gated demo EMR feed (bi_demo_patient_observations,
    patient-level observations; region decoded from the facility code
    FAC-<region>-…). DHIS2 side: the monthly dataValue totals already
    aggregated in mart_hmis_indicator_coverage.

    Grain is region (not facility): the demo EMR facilities are a separate
    fixture and do not exist in the DHIS2 org tree, so a facility-level join
    would be fabrication. Region by indicator by month compares two independent feeds.

    Observation -> indicator mapping (closest clinical counterpart):
      ANC visit      -> ANC1        Penta3 dose -> PENTA3
      HIV test       -> HIV-TEST    Malaria RDT -> MAL-RDT
      TB screening   -> TB-NOTIF    Blood pressure -> HTN-SCRN
*/
/*  Normalisation: the demo EMR feed covers ~30 fixture facilities while DHIS2
    covers every reporting facility in the region, so raw totals are not
    comparable. Both sides are therefore normalised to a per-reporting-facility
    monthly rate before comparison: a comparison of intensity, not volume. */
WITH obs_map (observation, indicator_id) AS (
    -- like-for-like event pairs only. 'TB screening' has no DHIS2 counterpart
    -- (TB-NOTIF is notified cases, a much smaller funnel stage than screening;
    -- comparing them would show false disagreement), so TB is left unmapped.
    VALUES ('ANC visit', 'ANC1'), ('Penta3 dose', 'PENTA3'),
           ('HIV test', 'HIV-TEST'), ('Malaria RDT', 'MAL-RDT'),
           ('Blood pressure', 'HTN-SCRN')
),
emr AS (
    SELECT
        SPLIT_PART(o.facility_code, '-', 2)        AS region_id,
        m.indicator_id,
        m.observation,
        TO_CHAR(o.encounter_date, 'YYYY-MM')       AS month_id,
        COUNT(*)                                   AS emr_recorded,
        COUNT(DISTINCT o.facility_code)            AS emr_facilities
    FROM {{ ref('bi_demo_patient_observations') }} o
    JOIN obs_map m USING (observation)
    GROUP BY 1, 2, 3, 4
),
dhis2 AS (
    SELECT region_id, indicator_id, month_id,
           SUM(total_value)::numeric              AS dhis2_reported,
           SUM(facilities_reporting)              AS dhis2_facilities
    FROM {{ ref('mart_hmis_indicator_coverage') }}
    GROUP BY 1, 2, 3
),
rates AS (
    SELECT
        e.month_id, e.region_id, e.indicator_id, e.observation,
        e.emr_recorded, d.dhis2_reported,
        e.emr_recorded::numeric  / NULLIF(e.emr_facilities, 0)   AS emr_rate,
        d.dhis2_reported::numeric / NULLIF(d.dhis2_facilities, 0) AS dhis2_rate
    FROM emr e
    JOIN dhis2 d USING (region_id, indicator_id, month_id)
)
SELECT
    month_id,
    region_id,
    indicator_id,
    observation,
    emr_recorded,
    dhis2_reported,
    ROUND(emr_rate, 1)   AS emr_rate_per_facility,
    ROUND(dhis2_rate, 1) AS dhis2_rate_per_facility,
    CASE WHEN GREATEST(emr_rate, dhis2_rate) > 0
         THEN ROUND(100.0 * LEAST(emr_rate, dhis2_rate)
                    / GREATEST(emr_rate, dhis2_rate), 1)
         ELSE NULL END AS agreement_pct
FROM rates
