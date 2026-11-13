# ML runner

The single execution primitive for NHDW model code. Image: `nhdw/ml-runner:1.0.0`, built from this directory by `bundle/scripts/build-images.sh`; the manifests reference it as `nhdw-registry:5500/nhdw/ml-runner:1.0.0`.

Model code executes in NHDW in one way: an ephemeral Job running this CLI. Seed
models at boot, the nightly Airflow `ml_batch_predict` DAG, the daily
`ml_drift_check` DAG, the gateway's retrain endpoint and ad-hoc CSV predictions
from the workshop all start the same image with different arguments. Community
code is never resident: it runs in a throwaway pod that exits when the verb
finishes.

The runner owns all infrastructure I/O (Postgres, MLflow, the SeaweedFS object
store). A model package receives a `DataFrame` and returns one; it never
receives credentials. The package contract is documented
in `bundle/seed-pack/app/ml/_template/` (the annotated reference package).

## CLI

```
runner.py train <package-ref> [--alias champion] [--run-label L] [--trigger manual]
runner.py predict-batch <model_name> [--params '{}'] [--alias champion] [--trigger schedule]
runner.py predict-adhoc <model_name> --input <minio uri csv> --output <minio uri csv>
runner.py drift-check <model_name> [--alias champion] [--psi-threshold 0.2] [--no-retrain-on-drift]
runner.py score-performance <model_name> [--alias champion]
runner.py probe-lake [--bucket nhdw-raw]
```

`minio://` URIs are object-store URIs (the scheme name is historical); `s3://` is accepted too.

### `train <package-ref>`

`<package-ref>` is either
* `minio://nhdw-artifacts/ml-packages/<name>/<vN>/`: an object prefix
  holding the package files (or exactly one `.tar.gz`, the gateway-upload
  shape), or
* a mounted directory, e.g. `/seed/<name>` (the boot path mounts
  `bundle/seed-pack/app/ml` at `/seed`).

Steps: fetch package -> layered pip install of `requirements.txt` extras
(baked libs -> `/wheels` delta with `--no-index` -> network fallback, logged loudly)
-> run `feature_spec.yaml`'s `input.query` against the warehouse -> validate
against `input.signature` -> `train.py:train(df, params)` returns an
`mlflow.pyfunc.PythonModel` -> one direct predict (batch packages must emit
the flat forecast schema at train time) -> log pyfunc + signature to
experiment `/models/<name>` -> register a new version -> set alias
(`@champion` by default, the seed path; `--alias ''` skips) -> log the
reference feature profile (`drift/reference_profile.json`, per-column
quantiles and histograms of the training frame, the drift baseline) -> and
log the package files under `package/` so later batch runs are
self-contained -> write an `ops_ml.ml_run` row (`mlflow_run_id`,
`reference_profile_uri`, row_count, latency).

`train.py`'s classes are pickled by value (cloudpickle), so the logged
model reloads anywhere without the package source.

### `predict-batch <model_name>`

Resolves `models:/<name>@champion` -> downloads `package/feature_spec.yaml`
+ `package/params.yaml` from the champion version's MLflow run -> runs the
feature query against the warehouse -> `predict(df, params)` (CLI `--params`
JSON overrides the package defaults; delivered via the MLflow params
mechanism, where scalars declared in `params.yaml` form the signature's params
schema) -> validates the output against the unified flat forecast schema
(`ops_ml.ml_predictions` columns; `alarm_flag` is *derived* here as
`alarm_prob >= 0.5`, `disease NULL -> ''`, unknown extra columns fold into
`attrs` jsonb) -> `DELETE WHERE run_id` then insert (idempotent re-runs)
-> closes the `ml_run` row with status/row_count/latency.

Scheduling: the Airflow DAG iterates `ops_ml.model_deployment` rows
(`enabled AND deployment_kind='batch'`) and runs one of these per row.
Scheduling a model = INSERT a row (seed rows: `bundle/seed-pack/app/ml/deployments.yaml`).

### `predict-adhoc <model_name> --input … --output …`

Staged CSV in the object store -> validated against the registered model's input
signature -> `predict` -> result CSV uploaded to the `--output` URI ->
`ml_run` row (`on_demand`). Nothing is written to `ops_ml.ml_predictions`.
This is the backend for the gateway's async `POST /api/ml/<name>/predict`
job flow and the workshop CSV-upload page.

### `drift-check`, `score-performance`, `probe-lake`

* `drift-check` re-runs the champion's feature query, computes per-feature PSI
  and KS against the training reference profile and writes
  `ops_ml.model_drift`. A model without a profile gets a first-seen baseline
  from the current batch. When a feature's PSI reaches the threshold, the runner
  retrains `@<alias>` unless `--no-retrain-on-drift` is given. Drift rows attach
  to the latest predictions run instead of opening a new `ml_run`.
* `score-performance` joins the latest predictions to the actuals declared in
  the package manifest's `actuals:` block and writes MAE, MAPE and interval
  coverage to `ops_ml.model_performance`. Models without an `actuals:` block
  are skipped.
* `probe-lake` reads every `_manifest.json` in the raw bucket and prints a
  per-source fingerprint (max watermark, rows, bytes, files), also written to
  the Airflow XCom path when present, so `pipeline_refresh` can skip the dbt
  rebuild when nothing changed.

## Env contract

All values come from existing Kubernetes secrets and config; the runner adds no
secret material of its own.

| Var | Default | Source |
|---|---|---|
| `MLFLOW_TRACKING_URI` (alias `MLFLOW_URL`) | `http://mlflow.nhdw.svc.cluster.local:5000` | plain env |
| `MLFLOW_S3_ENDPOINT_URL` | `http://seaweedfs-s3.nhdw.svc.cluster.local:8333` | plain env |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | none | `mlflow-secrets` (`S3_ACCESS_KEY`/`S3_SECRET_KEY`) |
| `AWS_DEFAULT_REGION` | `et-addis-1` | plain env |
| `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | fall back to the three MLflow S3 vars | optional override when package or ad-hoc I/O needs a different object-store principal |
| `OPS_PG_HOST` / `OPS_PG_PORT` | `postgres-vault.nhdw.svc.cluster.local` / `5432` | plain env |
| `OPS_PG_USER` / `OPS_PG_DB` | `vault_admin` / `nhdw_ops` | plain env |
| `VAULT_ADMIN_PASSWORD` | none (required) | `postgres-vault-secrets` (`POSTGRES_PASSWORD`) |
| `MARTS_PG_HOST/PORT/USER/PASSWORD` | the `OPS_PG_*` values | plain env |
| `MARTS_PG_DB` | `nhdw_vault` | plain env |
| `NHDW_WHEELS_DIR` | `/wheels` | plain env |

## Layout

```
runner.py           CLI (train / predict-batch / predict-adhoc / drift-check /
                    score-performance / probe-lake)
lib/db.py           Postgres: feature queries, ml_run lifecycle,
                    flat-schema DELETE+INSERT writer
lib/mlflow_io.py    tracking / registry / aliases / run artifacts
lib/minio_io.py     object-store (minio:// or s3://) download/upload + package-prefix fetch
lib/drift.py        PSI / KS drift computation against the reference profile
lib/package.py      package parse + contract checks + layered pip + train import
lib/profile.py      reference feature profile (drift baseline)
lib/schemas.py      input-signature, model-signature and flat-output validation
Dockerfile          python:3.12-slim + pinned science stack (mlflow 2.18.0
                    matching the server)
```

## Local smoke

```sh
docker build -t nhdw/ml-runner:1.0.0 2_pipelines/ml/runner
docker run --rm nhdw/ml-runner:1.0.0 --help
docker run --rm nhdw/ml-runner:1.0.0 train --help
```

In the cluster, Jobs are created by the bootstrap seed step, the Airflow DAGs
and the gateway, never by per-model manifests.

Model metrics the runner logs describe behaviour on the platform's
demonstration data, not performance on real data; see the Models section of
[docs/data-provenance.md](../../../docs/data-provenance.md).
