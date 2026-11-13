"""ml_drift_check: the model-agnostic monitoring DAG.

Runs the daily monitoring pass over every deployed model the same way
``ml_batch_predict`` runs inference: by fanning out over
``ops_ml.model_deployment`` rows rather than hardcoding models. For each
enabled deployment it launches two ``nhdw/ml-runner`` pods:

    drift_check       ml-runner drift-check <model>
        Per-feature PSI (and KS) of the current feature frame against the
        model's training reference profile, written to ops_ml.model_drift. On
        a PSI breach (threshold 0.2) the same pod runs a generic drift-triggered
        retrain (refit @champion and a fresh reference profile). A model with
        no baseline gets one bootstrapped from the first batch, labelled
        'first-seen'.

    score_performance ml-runner score-performance <model>
        Joins the model's latest predictions against the declarative manifest
        ``actuals:`` block (mart, value_col, join_keys) and writes MAE, MAPE and
        95%-interval coverage to ops_ml.model_performance. Models with no
        actuals block skip themselves (exit 0).

Onboarding a model to monitoring is a data operation: the row that schedules
it in model_deployment is the row this DAG iterates. No per-model DAG and no
image change. The drift step needs only the training
reference profile (logged by every runner train); the performance step needs
only the optional manifest ``actuals:`` block.

Env contract (base/airflow.yaml scheduler container, shared with
ml_batch_predict): OPS_PG_* (ops_reader, lists deployments) and ML_RUNNER_IMAGE
(the dev overlay patches it to nhdw/ml-runner:dev). Runner pods get the same
secret-sourced env as the inference pods; no new secret material.
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


# Same runner env as ml_batch_predict, repeated so each DAG file is
# self-contained.
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

RUNNER_ENV_FROM = [
    k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name="mlflow-secrets")),
    k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name="minio-secrets")),
    k8s.V1EnvFromSource(
        secret_ref=k8s.V1SecretEnvSource(name="postgres-vault-secrets")
    ),
]


@task
def deployed_models() -> list[str]:
    """Every enabled model in ops_ml.model_deployment. Drift and performance
    apply to any deployed model, not only batch ones."""
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
                "SELECT model_name FROM ops_ml.model_deployment "
                "WHERE enabled ORDER BY model_name"
            )
            names = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    print(f"[ml_drift_check] {len(names)} deployed model(s): {names}")
    return names


def _drift_argv(model: str) -> list[str]:
    return ["drift-check", model]


def _score_argv(model: str) -> list[str]:
    return ["score-performance", model]


@dag(
    dag_id="ml_drift_check",
    schedule="0 6 * * *",  # daily 06:00 UTC, after nightly inference (04:00)
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1},
    tags=["ml", "monitoring", "drift"],
    doc_md=__doc__,
)
def ml_drift_check():
    models = deployed_models()

    drift_check = KubernetesPodOperator.partial(
        task_id="drift_check",
        name="ml-drift-check",
        namespace=NAMESPACE,
        image=ML_RUNNER_IMAGE,
        image_pull_policy="IfNotPresent",
        env_vars=RUNNER_ENV,
        env_from=RUNNER_ENV_FROM,
        in_cluster=True,
        get_logs=True,
        on_finish_action="delete_succeeded_pod",  # keep failed pods for debugging
        startup_timeout_seconds=300,
    ).expand(arguments=models.map(_drift_argv))

    score_performance = KubernetesPodOperator.partial(
        task_id="score_performance",
        name="ml-score-performance",
        namespace=NAMESPACE,
        image=ML_RUNNER_IMAGE,
        image_pull_policy="IfNotPresent",
        env_vars=RUNNER_ENV,
        env_from=RUNNER_ENV_FROM,
        in_cluster=True,
        get_logs=True,
        on_finish_action="delete_succeeded_pod",
        startup_timeout_seconds=300,
    ).expand(arguments=models.map(_score_argv))

    # Score after the drift pass so a drift-triggered retrain's fresh champion
    # is the version whose predictions get scored on the same run.
    drift_check >> score_performance


ml_drift_check()
