/*
    mart_public_indicator_kpis: public-dashboard indicator KPI projection.

    Region by indicator by month rollup of the achievement rate (mean_value) for
    the official public headline indicators, with k-anonymity suppression: any
    region cell with < 5 reporting facilities is dropped (k≥5, the public
    release rule that exceeds the national Data-Sharing minimum).

    Unlike mart_public_summary (which carries programme-level reporting
    *completeness*), this mart carries the indicator *achievement* rate the
    public KPI tiles show, e.g. Penta3 coverage %, ANC4 %, SBA %, mCPR %,
    TB treatment success %, ART viral suppression %, U5 stunting %.

    Nothing here is hardcoded: which indicators are public, their labels, the
    HSDIP targets, definitions and units all come from the committed reference
    seed public_indicator_reference (WHERE is_public_kpi). Region names come
    from public_region_reference. Every value traces mean_value -> DHIS2 via
    mart_hmis_indicator_coverage -> business vault -> raw vault -> staging ->
    DHIS2 dataValues.

    National figures are not precomputed here (single-grain mart); the public
    fetcher aggregates regions with a facility-reporting weighting.

    Consumed by: Public dashboard: 8 KPI tiles, "Coverage by region" bar,
    24-month indicator trend.
*/
{{ config(materialized='table', schema='bi_public') }}

WITH ref AS (
    SELECT
        indicator_id, programme_id, kpi_label, kpi_order, kpi_kind, unit,
        definition, hsdip_target, target_direction, target_source
    FROM {{ ref('public_indicator_reference') }}
    WHERE is_public_kpi
),
region_month AS (
    -- Roll the woreda-grain achievement rate up to region by indicator by month.
    -- The region rate is the facility-reporting-weighted mean of the woreda
    -- mean_value (a defensible national/region aggregate when only the mean
    -- rate is carried, not raw numerators/denominators).
    SELECT
        c.region_id,
        c.indicator_id,
        c.month_id,
        SUM(c.facilities_reporting)::bigint AS facilities_reporting,
        SUM(c.facilities_expected)::bigint  AS facilities_expected,
        SUM(c.mean_value * c.facilities_reporting)
            / NULLIF(SUM(c.facilities_reporting), 0)  AS rate_pct_raw
    FROM {{ ref('mart_hmis_indicator_coverage') }} c
    JOIN ref USING (indicator_id)
    WHERE c.mean_value IS NOT NULL
    GROUP BY 1, 2, 3
    HAVING SUM(c.facilities_reporting) >= 5   -- k≥5 suppression (drop sub-k cells)
)
SELECT
    rm.region_id,
    reg.region_name,
    rm.indicator_id,
    ref.programme_id,
    ref.kpi_label,
    ref.kpi_order,
    ref.kpi_kind,
    ref.unit,
    ref.definition,
    ref.hsdip_target,
    ref.target_direction,
    ref.target_source,
    rm.month_id,
    rm.facilities_reporting,
    rm.facilities_expected,
    ROUND(rm.rate_pct_raw::numeric(20, 4), 1)::numeric(6, 1) AS rate_pct
FROM region_month rm
JOIN ref  USING (indicator_id)
LEFT JOIN {{ ref('public_region_reference') }} reg ON reg.region_id = rm.region_id
