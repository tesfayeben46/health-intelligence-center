# ML registry

Build inputs for the platform's model registry and the model-graduation checks. The directory holds no models and no runtime code of its own: model packages live in `bundle/seed-pack/app/ml/`, and training and prediction run through the ML runner (`../runner`).

## Contents

| File | Purpose |
|---|---|
| `Dockerfile` | The `nhdw/mlflow` image: MLflow 2.18.0 tracking server with `psycopg2` (Postgres backend store) and `boto3` (S3 artifact store) |
| `validators.py` | Validation harness the gateway runs on a submitted model package before it can be promoted |
| `Dockerfile.sandbox` | The `nhdw/ml-sandbox` image the harness's smoke check runs model code in |
| `.env.example` | Names of the MLflow database and object-store credentials |

## MLflow tracking server

`bundle/scripts/build-images.sh` builds `nhdw/mlflow:1.0.0` from `Dockerfile`, and `1_infrastructure/k8s/base/mlflow.yaml` runs it as the `mlflow` Deployment. The server is configured entirely from environment variables (`MLFLOW_DB_*`, `MLFLOW_HOST`, `MLFLOW_PORT`, `MLFLOW_WORKERS`, `MLFLOW_DEFAULT_ARTIFACT_ROOT`):

- Backend store: database `nhdw_mlflow` on `postgres-vault`, user `mlflow_writer`.
- Artifacts: stored by the server in `s3://nhdw-artifacts/mlflow` on SeaweedFS.
- Proxied artifact access (`--serve-artifacts`, default artifact root `mlflow-artifacts:/`). The server is the only component holding S3 credentials; clients such as the ML runner and JupyterHub notebooks upload and download artifacts through the server over HTTP and need only `MLFLOW_TRACKING_URI`. Experiments created with an explicit `s3://` artifact root keep it, and the server reads and writes them with its own credentials.

The ML runner pins the same MLflow version (`../runner/Dockerfile`) so client and server match.

## Graduation validators

`validators.py` is import-only. `validate_all(tar_path)` runs four checks on a package tarball and returns one result per check, which the gateway stores in `ops_artifact.validator_result`:

| Check | What it verifies |
|---|---|
| `schema` | `driver.py`, `deployment.yaml` and `feature_spec.yaml` are present |
| `deps` | No imports that reach the network or the host (`socket`, `ctypes`, `subprocess`, `os.system`, `requests`, `urllib`) |
| `resources` | The `resource_request` in `deployment.yaml` is within 8 CPU and 16 GiB |
| `smoke` | The model is imported, fitted and run on a small synthetic input in a `--network=none`, read-only container running as user 65534, and the run is timed |

All checks run even when an earlier one fails, so the operator gets a full report. Where no docker daemon is available (the gateway inside the cluster has no docker socket) the smoke check returns a not-passed result marked `skipped` instead of raising; it is not a required gate. `SANDBOX_IMAGE` overrides the sandbox image (default `nhdw/ml-sandbox:1.0.0`).

The gateway image carries a copy of this file: `bundle/scripts/build-images.sh` copies `validators.py` into the gateway build context before building `nhdw/gateway`, so this file is the single source.

## Sandbox image

`Dockerfile.sandbox` installs pandas, numpy, statsmodels, scikit-learn and scipy at build time, so the smoke container needs no network access. It is not in the `build-images.sh` image list and is built by hand when the smoke check is used, for example:

```bash
docker build -t nhdw/ml-sandbox:1.0.0 -f 2_pipelines/ml/registry/Dockerfile.sandbox 2_pipelines/ml/registry
```

## Notes

Model metrics recorded in MLflow describe behaviour on the platform's demonstration data, not performance on real data; see the Models section of [docs/data-provenance.md](../../../docs/data-provenance.md).
