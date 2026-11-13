/*
    mart_idsr_disease_weekly: epi-week by region by woreda by disease grain.

    Sourced from DHIS2: Ethiopia's IDSR (Integrated Disease Surveillance &
    Response) is reported through DHIS2 as weekly aggregate data values. This
    mart reads the DHIS2 raw vault: the `IDSR-<DZ>-C` (confirmed cases) and
    `IDSR-<DZ>-D` (deaths) data elements, at level-4 woreda org-units, on weekly
    (`yyyyWnn`) periods. Region is derived from the woreda code (`WOR-XX-…`),
    cases/deaths are pivoted from the two elements. Carries both counts so
    downstream can compute incidence, CFR and outbreak signals across the priority
    notifiable diseases.

    Business-vault reads: satellites via the v_*_current views (insert-only
    sats; bare joins double-count once history accumulates); week decoding
    via sat_dhis2_period_resolved. The BV spine does not apply here (it is
    facility by indicator by monthly; IDSR is woreda by disease by weekly), and
    the disease -> programme CASE below is IDSR response-domain taxonomy
    (WASH/VBD are not BV programmes), not the shared indicator -> programme
    map in v_programme_resolution.

    Consumed by: Internal BI · Disease Surveillance; the EWARS/EARS/outbreak
    models (bundle/seed-pack/app/ml packages, via the ML runner); mart_idsr_climate_drivers.
*/
WITH ou AS (                                   -- woreda org-units (level 4)
    -- region_id decoded from the woreda code: the BV geography hub carries
    -- these nodes but has no dim/hierarchy view.
    SELECT sat.hk_dhis2_org_unit,
           sat.code AS woreda_uid,
           sat.name AS woreda_name,
           SUBSTRING(sat.code FROM '^WOR-([A-Z]{2})-') AS region_id
    FROM {{ source('nhdw_vault_business', 'v_sat_dhis2_org_unit_current') }} sat
    WHERE sat.code LIKE 'WOR-%'
),
de AS (                                        -- IDSR disease elements -> disease + metric
    SELECT sat.hk_dhis2_data_element,
           SPLIT_PART(sat.code, '-', 2) AS disease_code,
           SPLIT_PART(sat.code, '-', 3) AS metric          -- 'C' cases | 'D' deaths
    FROM {{ source('nhdw_vault_business', 'v_sat_dhis2_data_element_current') }} sat
    WHERE sat.code LIKE 'IDSR-%'
),
pe AS (                                        -- weekly periods only -> iso_week + week_start
    -- sat_dhis2_period_resolved owns period decoding (weekly = yyyyWnn,
    -- start_date = ISO week Monday), so no inline period re-parsing here.
    SELECT sat.hk_dhis2_period,
           TO_CHAR(sat.start_date, 'IYYY-"W"IW') AS iso_week,
           sat.start_date AS week_start
    FROM {{ source('nhdw_vault_business', 'sat_dhis2_period_resolved') }} sat
    WHERE sat.period_type = 'weekly'
),
vals AS (
    SELECT ou.region_id, ou.woreda_uid, ou.woreda_name,
           de.disease_code, de.metric, pe.iso_week, pe.week_start,
           dv_sat.value::numeric AS value
    FROM {{ source('nhdw_vault_raw', 'lnk_dhis2_data_value') }} dv_lnk
    JOIN {{ source('nhdw_vault_business', 'v_sat_dhis2_data_value_current') }} dv_sat USING (hk_dhis2_data_value)
    JOIN ou USING (hk_dhis2_org_unit)
    JOIN de USING (hk_dhis2_data_element)
    JOIN pe USING (hk_dhis2_period)
),
pivoted AS (
    SELECT region_id, woreda_uid, woreda_name, disease_code, iso_week, week_start,
           SUM(value) FILTER (WHERE metric = 'C') AS new_cases,
           SUM(value) FILTER (WHERE metric = 'D') AS deaths
    FROM vals
    GROUP BY 1, 2, 3, 4, 5, 6
)
SELECT
    iso_week,
    week_start,
    region_id,
    woreda_uid,
    woreda_name,
    CASE disease_code
        WHEN 'MAL'  THEN 'MALARIA'
        WHEN 'TB'   THEN 'TB'
        WHEN 'MEN'  THEN 'EPI'  WHEN 'MEA' THEN 'EPI'
        WHEN 'YF'   THEN 'EPI'  WHEN 'NNT' THEN 'EPI'
        WHEN 'CHOL' THEN 'WASH' WHEN 'TYPH' THEN 'WASH'
        WHEN 'DENG' THEN 'VBD'  WHEN 'RELF' THEN 'VBD'
        ELSE 'OTHER'
    END AS programme_id,
    disease_code,
    CASE disease_code
        WHEN 'MAL'  THEN 'Malaria (confirmed)'
        WHEN 'MEN'  THEN 'Meningitis (meningococcal)'
        WHEN 'MEA'  THEN 'Measles'
        WHEN 'CHOL' THEN 'Cholera (AWD)'
        WHEN 'DENG' THEN 'Dengue fever'
        WHEN 'YF'   THEN 'Yellow fever'
        WHEN 'TB'   THEN 'Tuberculosis (new)'
        WHEN 'TYPH' THEN 'Typhoid fever'
        WHEN 'RELF' THEN 'Relapsing fever'
        WHEN 'NNT'  THEN 'Neonatal tetanus'
        ELSE disease_code
    END AS disease_label,
    COALESCE(new_cases, 0)::bigint AS new_cases,
    COALESCE(deaths, 0)::bigint    AS deaths
FROM pivoted
WHERE COALESCE(new_cases, 0) > 0 OR COALESCE(deaths, 0) > 0
