/*
    v_programme_resolution: health-programme conformed.
    Programmes are the top-level disease/service-line groupings:
        HIV, TB, EPI, MNCH, NCD, MALARIA, FP, NUTRITION, GENERAL, OTHER.

    Single source of truth for the indicator-code -> programme mapping, which
    lives in the governed reference vocabulary instead of in this file:
    nhdw_ref.source_to_concept_map (vocabulary 'DHIS2.dataElement') joined to
    nhdw_ref.concept. Stewards change a mapping by superseding a crosswalk row
    (bitemporal), never by editing SQL. Marts keep joining this view
    (source_code = indicator_id) instead of re-deriving the mapping locally.

    Codes the vocabulary does not cover fall back to 'OTHER'. Five codes
    where the entity model is richer (AEFI-*, CC-EXCURSION -> EPI; PHEM-*
    mislabelled HIV in entity-model.yaml) are pinned to OTHER by the parity
    supplement; upgrading them is a deliberate, stewarded vocabulary change.

    Boot-order contract: nhdw_ref must be seeded before this model builds;
    bootstrap (bundle/scripts/seed-ref-vocab.sh) guarantees it, and the DDL
    ships in 3_storage/postgres/init/34_ref_vocab.sql.

    Sources:
      - DHIS2 dataElement: one row per data element (current record).
      - Other code sources (e.g. DHIS2 dataElementGroup, EthioEMR program)
        union in once their hub/sat exists: their codes get their own
        source_vocabulary_id rows in the crosswalk, with no view rewrite.

    Doubles as the AutomateDV stage for hub_programme_conformed:
    hk_programme_conformed = hash(programme_id) is pre-computed here.
*/
{{ config(materialized='view', schema='nhdw_vault_business') }}

WITH xwalk AS (
    -- Current crosswalk rows only: not invalidated, validity window covers
    -- today; if a code was superseded (two windows), the newest valid_from
    -- wins deterministically.
    SELECT DISTINCT ON (m.source_code)
        m.source_code,
        c.concept_code AS programme_id
    FROM {{ source('ref_vocab', 'source_to_concept_map') }} m
    JOIN {{ source('ref_vocab', 'concept') }} c
      ON c.concept_id = m.target_concept_id
    WHERE m.source_vocabulary_id = 'DHIS2.dataElement'
      AND m.invalid_reason IS NULL
      AND CURRENT_DATE >= m.valid_from
      AND CURRENT_DATE <  m.valid_to
    ORDER BY m.source_code, m.valid_from DESC
),

src AS (
    SELECT
        sat.hk_dhis2_data_element AS source_hk,
        'DHIS2.dataElement' AS source_system,
        sat.name AS source_name,
        sat.code AS source_code,
        COALESCE(x.programme_id, 'OTHER') AS programme_id,
        sat.load_ts
    FROM {{ ref('v_sat_dhis2_data_element_current') }} sat
    LEFT JOIN xwalk x ON x.source_code = sat.code
    WHERE sat.code IS NOT NULL AND sat.code <> ''
)

SELECT
    src.*,
    {{ automate_dv.hash(columns=['programme_id'], alias='hk_programme_conformed') }},
    'BV.programme_resolution' AS record_source
FROM src
