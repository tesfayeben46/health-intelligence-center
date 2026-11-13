/*
    mart_epi_cold_chain_safety: month by national EPI safety surveillance:
    cold-chain temperature excursions + AEFI (adverse events following
    immunization), summed from the DHIS2 HMIS monthly dataValues for the three
    EPI indicators CC-EXCURSION / AEFI-MILD / AEFI-SERIOUS.

    Backs rm-cold-chain as a 2-KPI safety strip (excursions | AEFI), not a
    causal excursion->batch->recipient->AEFI flow, because no source carries
    batch-to-recipient lineage. National grain.
*/
WITH de AS (
    SELECT sat.hk_dhis2_data_element, sat.code AS indicator_id
    FROM {{ source('nhdw_vault_business', 'v_sat_dhis2_data_element_current') }} sat
    WHERE sat.code IN ('CC-EXCURSION', 'AEFI-MILD', 'AEFI-SERIOUS')
),
vals AS (
    SELECT
        de.indicator_id,
        TO_CHAR(per.start_date, 'YYYY-MM') AS month_id,
        SUM(dv_sat.value::numeric) AS total_value
    FROM {{ source('nhdw_vault_raw', 'lnk_dhis2_data_value') }} dv_lnk
    JOIN {{ source('nhdw_vault_business', 'v_sat_dhis2_data_value_current') }} dv_sat
        USING (hk_dhis2_data_value)
    JOIN {{ source('nhdw_vault_business', 'sat_dhis2_period_resolved') }} per
        USING (hk_dhis2_period)
    JOIN de USING (hk_dhis2_data_element)
    WHERE per.period_type = 'monthly' AND dv_sat.value IS NOT NULL
    GROUP BY 1, 2
)
SELECT
    month_id,
    CAST(COALESCE(SUM(total_value) FILTER (WHERE indicator_id = 'CC-EXCURSION'), 0) AS bigint) AS excursions,
    CAST(COALESCE(SUM(total_value) FILTER (WHERE indicator_id = 'AEFI-MILD'), 0) AS bigint)    AS aefi_mild,
    CAST(COALESCE(SUM(total_value) FILTER (WHERE indicator_id = 'AEFI-SERIOUS'), 0) AS bigint) AS aefi_serious,
    CAST(COALESCE(SUM(total_value) FILTER (WHERE indicator_id IN ('AEFI-MILD', 'AEFI-SERIOUS')), 0) AS bigint) AS aefi_total
FROM vals
GROUP BY month_id
