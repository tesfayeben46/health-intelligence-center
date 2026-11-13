# Data provenance

The platform ships with demonstration data. The source systems are the real software, or small stand-ins where noted, populated by the generators in this repository or by restore artifacts distributed separately. Everything above the sources (ingestion, lake, staging, Data Vault, marts, gateway and dashboards) is the same code that runs against live sources. The few paths that load data directly are listed under [Direct loads](#direct-loads).

## Source systems

| Source | What runs | Data | Reproducible from a fresh clone |
|---|---|---|---|
| DHIS2 (HMIS, IDSR) | DHIS2 2.40 with PostGIS | `0_external/sources/DHIS2/scripts/seed_ethiopia.py` creates the metadata model and about 680,000 monthly HMIS values (36 months to 2026-06) through the Web API. `0_external/sources/IDSR/seed_idsr_dhis2.py` adds weekly IDSR cases to 2026-W27, coupled with a lag to the rainfall series. `seed_population_dhis2.py` adds woreda populations. | Yes |
| Climate | FastAPI service with the shape of a public climate API | `0_external/sources/Climate/generate_climate.py`: weekly rainfall and temperature to 2026-W27, fixed seed | Yes |
| HCMIS WDM | SQL Server (warehouse schema) | Schema from a schema-only backup; rows from `0_external/sources/HCMISWDM/scripts/synthesize*.py` (stock events, issues, receipts, stock-out episodes). Dispatch tables are loaded by `synthesize_dispatch.py` from the planning fixture `6_access-points/dashboards/internal-bi/ui/src/ct/data/plan.ts` (2025-M06 to 2025-M11). | With the schema backup |
| mBrana | SQL Server (shares the HCMIS server) | Schema from a backup; rows from `0_external/sources/mBrana/scripts/synthesize.py` | With the schema backup |
| EthioEMR | MySQL 8 with the OpenMRS schema | Schema and concept dictionary from a database dump; patients, visits and observations from `0_external/sources/EthioEMR/scripts/synthesize.py` | With the dump |
| MFR | HAPI FHIR R4 | `0_external/sources/MFR/scripts/seed.py` posts Organization resources built from the entity model. The warehouse MFR models read landing tables loaded from CSV (see [Direct loads](#direct-loads)). | FHIR replica: yes. Warehouse tables: with the CSVs |
| eLMIS (Zambia) | PostgreSQL 16 | A pattern-preserving mutation of a historical Zambian eLMIS extract (see `0_external/sources/OpenLMIS/provenance-manifest-summary.json`), or the fallback generator `0_external/sources/OpenLMIS/scripts/synthesize_zm_history.py` (2017-03 to 2018-08) | Yes, through the fallback generator |
| Fayda | OIDC and e-mail OTP service modelled on the Fayda flow | `0_external/fayda/seed/citizens.yaml` | Yes |
| Assistant model | Ollama serving Qwen 2.5 (`qwen2.5:14b` when the 14b weights are available, otherwise `qwen2.5:3b`, chosen by `bundle/scripts/gpu-detect.sh`) | Public model weights | Yes |

All Ethiopian generators draw from the entity model in `bundle/seeds/entity-model/` (regions, woredas, facilities, programmes, indicators, commodities, hubs), which is why records line up across sources.

## Direct loads

| Path | Loads | Used by |
|---|---|---|
| `bundle/seed-pack/app/program-demo/program_demo_data.sql` (views in `3_storage/postgres/init/03_program_demo.sql`) | Regional programme figures into `bi_facility` | MNCH continuity and regional drop-off widgets |
| `3_storage/postgres/init/30_demo_patient_phi.sql` | A patient-level fixture in `nhdw_landing` | Data-trust and facility-outlier widgets; the masking examples |
| `bundle/scripts/seed-realistic.sh` (MFR section) | `nhdw_landing.stg_mfr__*` from the CSVs in `bundle/seeds/sources/mfr/` | Facility capacity, workforce and equipment marts |

Workshop projects, model reviews and resource requests are created at boot through the platform's APIs by the `bundle/scripts/seed-*.sh` scripts.

## Public portal

- Newsroom articles are seeded from `bundle/seed-pack/app/articles/`.
- Headline indicators fall back to the published survey figures in the profile copy decks (`bundle/profiles/*/copy/public.json`) when the live mart returns nothing.
- Open datasets are exported from the public marts by `2_pipelines/marts/publish_public_datasets.py`.

## Time coverage

| Data | Ends |
|---|---|
| IDSR weekly surveillance, climate | 2026-W27 |
| HMIS monthly | 2026-06 |
| HCMIS, mBrana | the date the generators run |
| HCMIS dispatch plan | 2025-M11 |
| eLMIS (Zambia) | 2018-08 |

The warehouse is rebuilt at bring-up. The scheduled refresh DAG (`5_orchestrator/dags/pipeline_refresh.py`) ships paused.

## Models

The ML packages in `bundle/seed-pack/app/ml/` train on the data above: EARS and Farrington-style outbreak detectors, a seasonal baseline, a negative binomial early-warning model with climate covariates, a Holt-Winters stock-out forecaster and an eLMIS consumption forecaster. Reported metrics are computed on the demonstration data.
