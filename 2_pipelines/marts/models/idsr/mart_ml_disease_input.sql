/*
    mart_ml_disease_input: densified woreda by disease by epi-week ML feature mart.

    The training/scoring input for the disease-forecast model family (ears,
    outbreak, seasonal, ewars_lite, trained by the 2_pipelines/ml runner). It fixes
    three data traps the widget-facing marts are allowed to keep:

      1. Densification: mart_idsr_disease_weekly drops zero weeks (absence = 0
         for a reporting woreda). Here the full grid (surveillance woredas by 
         priority diseases by every ISO week of the observed span) is emitted
         with cases/deaths COALESCEd to 0, so baselines see true zeros.
      2. Climate broadcast: rainfall/temp/humidity live at region grain
         (hub/sat_climate_week); they are broadcast onto every woreda of the
         region. Not read from mart_idsr_climate_drivers, which carries neither
         woreda nor disease_code. DHIS2 codes region org-units 'ET-XX' while
         the climate source keys on the bare 'XX'; the 'ET-' prefix is
         stripped explicitly at the join seam below.
      3. Population: annual POP-TOTAL denominators seeded into DHIS2
         (0_external/sources/IDSR/seed_population_dhis2.py) flow through the
         real pipeline into the dataValue vault satellites on Yearly (YYYY)
         periods. LEFT JOINed by (woreda by year) and nullable where a DHIS2
         ingest run has not carried them: models must tolerate NULL population.

    Business-vault reads: every satellite via the v_*_current views (sats
    are insert-only; the current view is the "latest version" contract),
    period decoding via
    sat_dhis2_period_resolved. The BV spine does not apply (facility by 
    indicator by monthly vs this mart's woreda by disease by weekly grain).

    Grain: (woreda_uid, disease_code, year, week), unique and tested.
    Consumed by: ml-runner feature_spec queries (the bundle/seed-pack/app/ml packages).
    (No slash-star sequences in this header: Postgres block comments nest.)
*/
WITH ou AS (                                   -- woreda org-units (level 4)
    -- Region is path-derived (like mart_hmis_indicator_coverage), not sliced
    -- out of the woreda code: /<root>/<region>/<zone>/<woreda>.
    SELECT sat.hk_dhis2_org_unit,
           sat.code AS woreda_uid,
           sat.name AS woreda_name,
           SPLIT_PART(sat.path, '/', 3) AS region_ou_uid
    FROM {{ source('nhdw_vault_business', 'v_sat_dhis2_org_unit_current') }} sat
    WHERE sat.code LIKE 'WOR-%'
),
region AS (                                    -- level-2 region org-units
    -- DHIS2 codes regions 'ET-XX' (country root is bare 'ET'); the climate
    -- source keys weeks on the bare 2-letter 'XX'. Strip the 'ET-' prefix
    -- once here so region_id joins climate directly and matches the 2-letter
    -- region_id every other idsr mart emits.
    -- (The hub join stays: the DHIS2 uid `id` is the hub BK, the correct DV form.)
    SELECT h.id AS region_ou_uid,
           REGEXP_REPLACE(sat.code, '^ET-', '') AS region_id
    FROM {{ source('nhdw_vault_raw', 'hub_dhis2_org_unit') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_dhis2_org_unit_current') }} sat USING (hk_dhis2_org_unit)
    WHERE sat.code LIKE 'ET-%'
),
de AS (                                        -- IDSR disease elements -> disease + metric
    SELECT sat.hk_dhis2_data_element,
           SPLIT_PART(sat.code, '-', 2) AS disease_code,
           SPLIT_PART(sat.code, '-', 3) AS metric          -- 'C' cases | 'D' deaths
    FROM {{ source('nhdw_vault_business', 'v_sat_dhis2_data_element_current') }} sat
    WHERE sat.code LIKE 'IDSR-%'
),
surveillance_woredas AS (
    -- The org-unit dimension carries every woreda in the national tree (~570),
    -- but only the woredas assigned to the IDSR_WEEKLY dataSet report
    -- surveillance, and the dataSet<->orgUnit assignment does not flow into the
    -- vault. Grid woredas = dimension woredas observed in ≥1 IDSR fact: a
    -- woreda that ever reported stays in the grid for every week by disease
    -- (0-filled), while never-assigned woredas are excluded, not
    -- reported as zero.
    SELECT DISTINCT dv_lnk.hk_dhis2_org_unit
    FROM {{ source('nhdw_vault_raw', 'lnk_dhis2_data_value') }} dv_lnk
    JOIN de USING (hk_dhis2_data_element)
),
pe AS (                                        -- weekly periods only -> week_start
    -- sat_dhis2_period_resolved owns period decoding (weekly start_date =
    -- ISO week Monday); no inline yyyyWnn re-parsing.
    SELECT sat.hk_dhis2_period,
           sat.start_date AS week_start
    FROM {{ source('nhdw_vault_business', 'sat_dhis2_period_resolved') }} sat
    WHERE sat.period_type = 'weekly'
),
weeks AS (                                     -- densified Monday spine (observed span)
    SELECT gs::date                    AS week_start,
           EXTRACT(isoyear FROM gs)::int AS year,
           EXTRACT(week    FROM gs)::int AS week
    FROM (SELECT MIN(week_start) AS lo, MAX(week_start) AS hi FROM pe) bounds
    CROSS JOIN generate_series(bounds.lo, bounds.hi, interval '1 week') gs
),
diseases AS (
    SELECT DISTINCT disease_code FROM de
),
weekly_facts AS (                              -- observed cases/deaths, woreda by disease by week
    SELECT dv_lnk.hk_dhis2_org_unit,
           de.disease_code,
           pe.week_start,
           SUM(dv_sat.value::numeric) FILTER (WHERE de.metric = 'C') AS new_cases,
           SUM(dv_sat.value::numeric) FILTER (WHERE de.metric = 'D') AS deaths
    FROM {{ source('nhdw_vault_raw', 'lnk_dhis2_data_value') }} dv_lnk
    JOIN {{ source('nhdw_vault_business', 'v_sat_dhis2_data_value_current') }} dv_sat USING (hk_dhis2_data_value)
    JOIN de USING (hk_dhis2_data_element)
    JOIN pe USING (hk_dhis2_period)
    GROUP BY 1, 2, 3
),
climate AS (                                   -- region by week meteorological drivers
    -- Live climate rows already carry the bare 2-letter region id, but apply
    -- the same ET- normalisation on this side of the seam too, so the join
    -- survives either id convention showing up in a future climate feed.
    SELECT REGEXP_REPLACE(h.region_id, '^ET-', '') AS region_id,
           s.week_start::date       AS week_start,
           s.rainfall_mm::numeric   AS rainfall_mm,
           s.temp_celsius::numeric  AS temp_celsius,
           s.humidity_pct::numeric  AS humidity_pct
    FROM {{ source('nhdw_vault_raw', 'hub_climate_week') }} h
    JOIN {{ source('nhdw_vault_business', 'v_sat_climate_week_current') }} s USING (hk_climate_week)
),
pop_de AS (                                    -- annual population denominator element
    SELECT sat.hk_dhis2_data_element
    FROM {{ source('nhdw_vault_business', 'v_sat_dhis2_data_element_current') }} sat
    WHERE sat.code = 'POP-TOTAL'
),
population AS (                                -- woreda by year population (Yearly 'YYYY' periods)
    -- One dataValue link row per (woreda, year) by grain; the current-record
    -- view supplies the latest satellite version.
    SELECT dv_lnk.hk_dhis2_org_unit,
           EXTRACT(YEAR FROM per.start_date)::int AS year,
           dv_sat.value::numeric                  AS population
    FROM {{ source('nhdw_vault_raw', 'lnk_dhis2_data_value') }} dv_lnk
    JOIN {{ source('nhdw_vault_business', 'v_sat_dhis2_data_value_current') }} dv_sat USING (hk_dhis2_data_value)
    JOIN pop_de USING (hk_dhis2_data_element)
    JOIN {{ source('nhdw_vault_business', 'sat_dhis2_period_resolved') }} per USING (hk_dhis2_period)
    WHERE per.period_type = 'yearly'
),
grid AS (                                      -- the densified frame
    SELECT ou.hk_dhis2_org_unit,
           ou.woreda_uid,
           ou.woreda_name,
           r.region_id,
           d.disease_code,
           w.year, w.week, w.week_start
    FROM ou
    JOIN surveillance_woredas USING (hk_dhis2_org_unit)
    LEFT JOIN region r ON r.region_ou_uid = ou.region_ou_uid
    CROSS JOIN diseases d
    CROSS JOIN weeks w
)
SELECT
    g.woreda_uid,
    g.woreda_name,
    g.region_id,
    g.disease_code,
    -- Same label map as mart_idsr_disease_weekly; keep the two in sync.
    CASE g.disease_code
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
        ELSE g.disease_code
    END AS disease_label,
    g.year,
    g.week,
    g.week_start,
    COALESCE(f.new_cases, 0)::int AS cases,
    COALESCE(f.deaths, 0)::int    AS deaths,
    c.rainfall_mm,
    c.temp_celsius,
    c.humidity_pct,
    p.population
FROM grid g
LEFT JOIN weekly_facts f
       ON  f.hk_dhis2_org_unit = g.hk_dhis2_org_unit
       AND f.disease_code      = g.disease_code
       AND f.week_start        = g.week_start
LEFT JOIN climate c
       ON  c.region_id  = g.region_id
       AND c.week_start = g.week_start
LEFT JOIN population p
       ON  p.hk_dhis2_org_unit = g.hk_dhis2_org_unit
       AND p.year              = g.year
