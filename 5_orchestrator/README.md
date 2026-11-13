# Orchestrator (Airflow on Kubernetes)

Airflow schedules the platform's recurring batch work: ML inference and
monitoring, alert evaluation, data-quality detection, catalog ingestion, and a
change-gated warehouse refresh. Heavy work runs in pods that Airflow launches
(KubernetesPodOperator) or in the gateway (HTTP calls); Airflow itself only
schedules and tracks it.

## Deployment

- Manifest: `1_infrastructure/k8s/base/airflow.yaml` (and the Helm equivalent
  `1_infrastructure/helm/nhdw/templates/airflow.yaml`). One Deployment with a
  scheduler and a webserver container, LocalExecutor, `Recreate` strategy so
  two schedulers never share the metadata DB.
- Metadata DB: `nhdw_airflow` on postgres-vault, created by
  `3_storage/postgres/init/21_airflow_db.sh`.
- UI: Service `airflow:8080`; the dev overlay exposes
  `https://airflow.127.0.0.1.nip.io`. Login is Keycloak OIDC in the
  `privileged-ops` realm (`webserver_config.py`); if the OIDC secret is
  missing the webserver falls back to the break-glass admin user from
  `airflow-secrets`.

## DAGs

All schedules are UTC.

| DAG | Schedule | What it does |
|---|---|---|
| `pipeline_refresh` | `0 2 * * *` | Runs the ingestors, fingerprints the raw lake (`ml-runner probe-lake`), and only if the fingerprint changed runs the dbt chain (raw views, processed zone, staging, bridge, vault, marts, publish) and triggers `ml_batch_predict`. Ships paused (`is_paused_upon_creation=True`); see the module docstring. |
| `ml_batch_predict` | `0 4 * * *` | Reads enabled `deployment_kind='batch'` rows from `ops_ml.model_deployment` and launches one `ml-runner predict-batch <model>` pod per row (dynamic task mapping). |
| `openmetadata_ingestion` | `0 5 * * *` | Runs OpenMetadata ingestion connectors (Postgres, ClickHouse, S3 data lake, dbt lineage, Airflow) as pods. Best-effort: catalog only. |
| `ml_drift_check` | `0 6 * * *` | For every enabled model: `ml-runner drift-check` (PSI/KS against the training profile, retrain on breach), then `ml-runner score-performance` against actuals. |
| `dq_detection` | `15 6 * * *` | Calls the gateway `POST /api/dq/detect` with a service token; findings go to the in-app DQ queue. |
| `alert_evaluation` | `*/15 * * * *` | Calls the gateway `POST /api/alerts/evaluate` with a service token; matches subscriptions against the latest forecast. |

Enabling, disabling or parameterising a model for inference and monitoring is
a data operation on its `ops_ml.model_deployment` row; no manifest or DAG
change is needed.

## Configuration

DAGs read their settings from the Airflow container env set in
`base/airflow.yaml`:

| Variable | Used by |
|---|---|
| `OPS_PG_*` (`ops_reader`) | `ml_batch_predict`, `ml_drift_check` (list deployments) |
| `ML_RUNNER_IMAGE` | runner pods; the dev overlay points it at the local image |
| `NHDW_S3_ENDPOINT` | object-store endpoint for spawned pods (SeaweedFS S3 gateway) |
| `NHDW_GATEWAY_URL`, `ALERT_EVAL_TOKEN`, `DQ_DETECT_TOKEN` | `alert_evaluation`, `dq_detection` |
| `INGESTOR_*_IMAGE`, `*_DBT_IMAGE`, `VAULT_BRIDGE_IMAGE` | `pipeline_refresh`; set by the dev overlay only |

Spawned pods take credentials from existing Secrets (`mlflow-secrets`,
`postgres-vault-secrets`, `minio-secrets`, `seaweedfs-secrets`,
`openmetadata-secrets`, `source-creds`). The `minio-*` names are historical;
they hold the SeaweedFS S3 credentials.

## Image

`nhdw/airflow:1.0.0`, built from this directory's `Dockerfile`
(`bundle/scripts/build-images.sh`). The base is `apache/airflow:2.10.4-python3.12`;
the DAGs and `webserver_config.py` are copied in and compiled at build time.
DAGs are baked into the image, not volume-mounted, so changing a DAG means
rebuilding the image and restarting the Deployment.

## RBAC

The Deployment runs as ServiceAccount `airflow`. Its Role in the `nhdw`
namespace grants pod create/get/list/watch/delete/patch, `pods/log` get,
`pods/exec` (KubernetesPodOperator reads XCom through the sidecar) and event
reads. It does not grant `batch/jobs`.

## Manual trigger

```bash
kubectl -n nhdw exec deploy/airflow -c scheduler -- airflow dags trigger ml_batch_predict
kubectl -n nhdw exec deploy/airflow -c scheduler -- airflow dags list-runs --dag-id ml_batch_predict
```
