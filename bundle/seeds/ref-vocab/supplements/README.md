# Vocabulary supplements

`build_vocab.py` merges the CSVs in this directory into `source_to_concept_map` when it generates the `nhdw_ref` vocabulary seeds (`../_build/`). They hold mappings the entity model cannot derive:

| File | Contents |
|---|---|
| `mbrana_commodity_codes.csv` | mBrana's native commodity codes (`EPI-009`, ...) mapped to platform commodity concepts |
| `dhis2_dataelement_programme_parity.csv` | DHIS2 dataElement-to-programme mappings that pin the crosswalk to what the deployed resolution views produce (for example codes they map to `OTHER`); supplement rows override derived rows with the same key, so richer mappings arrive only as explicit vocabulary changes |
| `who_codes.csv` | The curated ICD-10 and ATC subset the platform references (curation rules in the file header); `build_vocab.py` turns these into concept relationships |

## Column shape

    source_code,source_vocabulary_id,source_code_description,target_vocabulary_id,target_concept_code

Targets are symbolic: `build_vocab.py` resolves `(target_vocabulary_id, target_concept_code)` to the numeric `target_concept_id`, so supplements never hard-code ids. `source_vocabulary_id` must be one of the vocabularies `build_vocab.py` declares, and the target must be a known concept; otherwise the build fails.

## Maintenance

- Add source codes here that the entity model does not cover.
- Curate standard-vocabulary rows from the official WHO sources; do not bulk-import a full distribution or add codes that have not been checked.
- After editing, run `python3 bundle/seeds/ref-vocab/build_vocab.py` and commit the regenerated `_build/` files and `concept_registry.json`; `build_vocab.py --check` verifies they are current.
