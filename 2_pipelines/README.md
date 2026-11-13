# Pipelines

Everything that moves data from the source systems to the governed `bi_*` marts, plus the master-data matcher and the ML runner. Each stage is a container image run as a one-shot Kubernetes Job; nothing in this directory runs as a long-lived service.

The data the pipelines carry is generated or restored demonstration data; no source in this repository is an operational system. See [docs/data-provenance.md](../docs/data-provenance.md). For the platform-wide picture, see [docs/architecture.md](../docs/architecture.md).

## Stages

| # | Stage | Directory | Job(s) | Reads | Writes |
|---|---|---|---|---|---|
| 1 | Ingestion | `ingestor/<source>/` | `dhis2-ingest-metadata`, `dhis2-ingest-dvs`, `climate-ingest`, `ethioemr-ingest`, `hcmiswdm-ingest`, `mbrana-ingest`, `mfr-ingest`, `elmis-hist-ingest` | Source replicas (Web API, FHIR, TDS, MySQL, PostgreSQL, HTTP) | Parquet and `_manifest.json` in `nhdw-raw` on SeaweedFS |
| 2 | Raw views | `staging/raw_views/`, `staging/scripts/apply_raw_views.py` | `ch-raw-views` | `nhdw-raw` | ClickHouse `nhdw_staging.raw_<source>__<object>` views over `s3()` |
| 3 | Processed zone | `staging/scripts/build_processed_zone.py`, `staging/scripts/pii_policy.yaml` | `processed-zone` | `nhdw-raw` | PII-filtered copy in `nhdw-processed` |
| 4 | Staging | `staging/` (dbt-clickhouse) | `dbt-staging` | Raw views | Typed, deduplicated `stg_*` views with record hashes |
| 5 | Bridge | `vault/bridge/` | `bridge-sync` | ClickHouse `stg_*` | Postgres `nhdw_landing.stg_*` (full snapshot per run) |
| 6 | Data Vault | `vault/` (dbt-postgres, AutomateDV) | `dbt-vault` | `nhdw_landing`, `nhdw_ref`, steward decisions | Insert-only raw vault (`nhdw_vault_raw`) and business vault (`nhdw_vault_business`) |
| 7 | Marts | `marts/` (dbt-postgres) | `dbt-marts` | Vault | `bi_*` serving schemas |
| 8 | MDM matcher | `mdm/` | `mdm-matcher` | Business-vault resolution views | Candidate queue in `nhdw_ops.ops_governance` |
| 9 | Open data | `marts/publish_public_datasets.py` | `publish-datasets` | `bi_public` marts | CSV files and `catalogue.json` in `nhdw-static` |

The MDM matcher is a parallel master-data lane: stewards review its candidates in the admin console, and accepted decisions re-key records in the business vault on the next `dbt-vault` run.

The ML runner (`ml/runner/`) is not part of the ordered chain. The first training of the model packages in `bundle/seed-pack/app/ml/` happens at bring-up (`bundle/scripts/seed-ml.sh`). After that, Airflow runs it as a pod (`KubernetesPodOperator` in `5_orchestrator/dags/ml_batch_predict.py` and `ml_drift_check.py`) to write batch predictions to `ops_ml.ml_predictions`, check drift, score performance and retrain on drift, and the gateway starts it as a Job when an operator triggers training from the admin console. `ml/registry/` holds the MLflow server image and the model-graduation validators.

## How the stages run

The Job manifests are in `1_infrastructure/k8s/base/`: `ingestor-jobs.yaml`, `dbt-jobs.yaml` and `mdm-jobs.yaml`. `bundle/scripts/seed-realistic.sh` runs them during bring-up: the ingestors first (in small concurrent batches), then stages 2 to 9 strictly in order, waiting for each Job to complete before starting the next. `5_orchestrator/dags/pipeline_refresh.py` can re-run the chain on a schedule, except the MDM matcher, which its chain leaves out; it ships paused, and its change gate (`ml-runner probe-lake`) skips the dbt rebuild when the lake manifests have not changed.

Images are built by `bundle/scripts/build-images.sh` from the `Dockerfile` in each directory (`nhdw/<name>:1.0.0`). Object-store credentials come from the `minio-secrets` Secret; the name is historical, and the store is SeaweedFS with an S3 API. Source credentials come from `source-creds`, database credentials from `postgres-vault-secrets` and the ClickHouse secrets.

The dbt Jobs share one entry point (`entrypoint.py` in each dbt project): it runs `dbt build`, uploads `target/` artifacts to `nhdw-artifacts/dbt/<project>/` for the gateway and OpenMetadata, and generates the dbt docs site. `dbt-vault` and `dbt-marts` use `--full-refresh` only on a cold database; warm runs are incremental so satellite history is kept.

## Layout

```
ingestor/   one dlt ingestor per source: Climate, DHIS2, EthioEMR, HCMISWDM, MFR, mBrana, OpenLMIS
staging/    dbt-clickhouse staging project, raw-view definitions, processed-zone builder, PII policy
vault/      dbt-postgres Data Vault project, the ClickHouse-to-Postgres bridge, vault check scripts
marts/      dbt-postgres marts project, seeds, data tests, open-data publisher
mdm/        MDM matcher (facility and commodity candidates) and its tests
ml/         ML runner and MLflow registry image
```

Each subdirectory has its own README where there is more to say.
