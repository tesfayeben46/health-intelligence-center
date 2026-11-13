"""ml_batch_predict: the model-agnostic batch-inference DAG.

Scheduling a model is a data operation, not a manifest edit: each enabled
``deployment_kind='batch'`` row in ``ops_ml.model_deployment`` (nhdw_ops)
becomes one dynamically-mapped KubernetesPodOperator task that runs

    nhdw/ml-runner  predict-batch <model_name> --params '<row.params>'

in the ``nhdw`` namespace. The runner resolves ``models:/<name>@champion``
from MLflow, executes the package's feature_spec query against the warehouse,
and DELETE+INSERTs the flat forecast rows into ``ops_ml.ml_predictions``
(idempotent per run_id); see 2_pipelines/ml/runner/.

Env contract (set on the Airflow containers in base/airflow.yaml):
  OPS_PG_HOST / OPS_PG_PORT / OPS_PG_DB / OPS_PG_USER / OPS_PG_PASSWORD
      read-only creds (ops_reader) used to list the deployment rows.
  ML_RUNNER_IMAGE
      runner image ref. Base value is the CI registry pin
      (nhdw-registry:5500/nhdw/ml-runner:v1); the dev overlay patches it to
      the locally-built nhdw/ml-runner:dev (a kustomize ``images:`` remap
      cannot reach a string inside this file, so the overlay patches the env
      var instead; keep both in sync).

The runner pods use only existing secrets (mlflow-secrets,
postgres-vault-secrets, minio-secrets); no new secret material.
"""

from __future__ import annotations

import os

import pendulum
from airflow.decorators import dag, task
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from kubernetes.client import models as k8s

NAMESPACE = "nhdw"
ML_RUNNER_IMAGE = os.environ.get(
    "ML_RUNNER_IMAGE", "nhdw-registry:5500/nhdw/ml-runner:v1"
)

MLFLOW_URL = "http://mlflow.nhdw.svc.cluster.local:5000"
# Object-store endpoint, read from the Airflow Deployment env so the backend can
# change without rebuilding this image. Defaults to the SeaweedFS S3 gateway;
# NHDW_S3_ENDPOINT overrides. MINIO_* names are historical.
MINIO_URL = os.environ.get("NHDW_S3_ENDPOINT", "http://seaweedfs-s3.nhdw.svc.cluster.local:8333")
PG_HOST = "postgres-vault.nhdw.svc.cluster.local"


def _secret_env(name: str, secret: str, key: str) -> k8s.V1EnvVar:
    return k8s.V1EnvVar(
        name=name,
        value_from=k8s.V1EnvVarSource(
            secret_key_ref=k8s.V1SecretKeySelector(name=secret, key=key)
        ),
    )


# Explicit runner env. MLFLOW_URL is the runner's canonical name for the
# tracking server; MLFLOW_TRACKING_URI is set too for the MLflow client.
RUNNER_ENV = [
    k8s.V1EnvVar(name="MLFLOW_URL", value=MLFLOW_URL),
    k8s.V1EnvVar(name="MLFLOW_TRACKING_URI", value=MLFLOW_URL),
    k8s.V1EnvVar(name="MLFLOW_S3_ENDPOINT_URL", value=MINIO_URL),
    k8s.V1EnvVar(name="BOTO_EXPERIMENTAL__NO_EMPTY_CONTINUE", value="true"),   # botocore#3755
    _secret_env("AWS_ACCESS_KEY_ID", "mlflow-secrets", "S3_ACCESS_KEY"),
    _secret_env("AWS_SECRET_ACCESS_KEY", "mlflow-secrets", "S3_SECRET_KEY"),
    k8s.V1EnvVar(name="AWS_DEFAULT_REGION", value="et-addis-1"),
    k8s.V1EnvVar(name="OPS_PG_HOST", value=PG_HOST),
    k8s.V1EnvVar(name="OPS_PG_PORT", value="5432"),
    _secret_env("VAULT_ADMIN_PASSWORD", "postgres-vault-secrets", "POSTGRES_PASSWORD"),
]

# Whole secret bundles the runner may map from (MINIO_*, OPS_* role
# passwords), exposed through envFrom.
RUNNER_ENV_FROM = [
    k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name="mlflow-secrets")),
    k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name="minio-secrets")),
    k8s.V1EnvFromSource(
        secret_ref=k8s.V1SecretEnvSource(name="postgres-vault-secrets")
    ),
]


@task
def enabled_batch_deployments() -> list[list[str]]:
    """One predict-batch argv per enabled batch row in ops_ml.model_deployment."""
    import psycopg2

    conn = psycopg2.connect(
        host=os.environ["OPS_PG_HOST"],
        port=int(os.environ.get("OPS_PG_PORT", "5432")),
        dbname=os.environ.get("OPS_PG_DB", "nhdw_ops"),
        user=os.environ.get("OPS_PG_USER", "ops_reader"),
        password=os.environ["OPS_PG_PASSWORD"],
        connect_timeout=10,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT model_name, params::text
                FROM ops_ml.model_deployment
                WHERE enabled AND deployment_kind = 'batch'
                ORDER BY model_name
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    argvs = [
        ["predict-batch", model_name, "--params", params or "{}"]
        for model_name, params in rows
    ]
    print(f"[ml_batch_predict] {len(argvs)} enabled batch deployment(s): "
          f"{[a[1] for a in argvs]}")
    return argvs


@dag(
    dag_id="ml_batch_predict",
    schedule="0 4 * * *",  # daily at 04:00 UTC
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1},
    tags=["ml", "batch"],
    doc_md=__doc__,
)
def ml_batch_predict():
    KubernetesPodOperator.partial(
        task_id="predict_batch",
        name="ml-predict-batch",
        namespace=NAMESPACE,
        image=ML_RUNNER_IMAGE,
        image_pull_policy="IfNotPresent",
        env_vars=RUNNER_ENV,
        env_from=RUNNER_ENV_FROM,
        in_cluster=True,
        get_logs=True,
        on_finish_action="delete_succeeded_pod",  # keep failed pods for debugging
        startup_timeout_seconds=300,
    ).expand(arguments=enabled_batch_deployments())


ml_batch_predict()
