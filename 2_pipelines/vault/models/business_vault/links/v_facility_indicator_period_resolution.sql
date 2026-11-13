/*
    v_facility_indicator_period_resolution: AutomateDV stage for
    lnk_facility_indicator_period (the business-vault spine).

    Grain: one row per DHIS2 data value at
        facility by indicator by monthly period,
    keyed on the conformed hubs. Values that differ only by category /
    attribute option combo share one spine key; the link macro's
    first-row dedupe collapses them to a single edge.

    Source: lnk_dhis2_data_value: the only feed carrying the
    facility by indicator by period combination natively. HCMIS supply
    facts join the facility dimension but not indicator/period in the
    HMIS sense; they keep their own link.

    Wiring: each leg resolves to its conformed hub by business key
    (INNER joins), so every FK the link stores exists in its parent hub
    by construction:
        facility  -> hub_facility_conformed.conformed_bk = org-unit code
                    (level-5 FAC-% codes are the shared facility_id, the
                    same key v_facility_resolution conforms on)
        indicator -> hub_indicator_conformed.indicator_id = data-element code
        period    -> hub_period_conformed (monthly, YYYYMM -> YYYY-MM)
    PK = hash of the hex of the three conformed participant keys, in FK
    declaration order.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

WITH dv AS (
    -- Period code lives on hub_dhis2_period (no sat carries it); org-unit /
    -- data-element codes come from the current-record sat views. FAC-%
    -- level-5 org units only (admin nodes are geography, not facility) and
    -- monthly (YYYYMM) periods only: the HMIS reporting grain.
    SELECT
        lnk.load_ts,
        ou.code AS ou_code,
        de.code AS de_code,
        SUBSTRING(p.period::text FROM 1 FOR 4) || '-'
            || SUBSTRING(p.period::text FROM 5 FOR 2) AS period_id
    FROM {{ ref('lnk_dhis2_data_value') }} lnk
    JOIN {{ ref('hub_dhis2_period') }} p USING (hk_dhis2_period)
    JOIN {{ ref('v_sat_dhis2_org_unit_current') }} ou USING (hk_dhis2_org_unit)
    JOIN {{ ref('v_sat_dhis2_data_element_current') }} de USING (hk_dhis2_data_element)
    WHERE ou.code LIKE 'FAC-%'
      AND p.period::text ~ '^[0-9]{6}$'
),
conformed AS (
    SELECT
        fc.hk_facility_conformed,
        ic.hk_indicator_conformed,
        pc.hk_period_conformed,
        dv.load_ts
    FROM dv
    JOIN {{ ref('hub_facility_conformed') }}  fc ON fc.conformed_bk = dv.ou_code
    JOIN {{ ref('hub_indicator_conformed') }} ic ON ic.indicator_id = dv.de_code
    JOIN {{ ref('hub_period_conformed') }}    pc ON pc.period_grain = 'monthly'
                                                AND pc.period_id    = dv.period_id
)
SELECT
    conformed.*,
    {{ automate_dv.hash(columns=["encode(hk_facility_conformed, 'hex')",
                                 "encode(hk_indicator_conformed, 'hex')",
                                 "encode(hk_period_conformed, 'hex')"],
                        alias='hk_facility_indicator_period') }},
    'BV.facility_indicator_period' AS record_source
FROM conformed
