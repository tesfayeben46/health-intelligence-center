"""pipeline_refresh: nightly, change-gated warehouse refresh.

The expensive tier (the full dbt marts rebuild) runs only when the raw lake
has changed, so the warehouse stays current without lake or mart bloat.

    ingest (dlt ingestors, parallel)
        Each ingestor is delta-aware: the incremental sources (DHIS2
        dataValueSets, EthioEMR, Climate) restore their watermark from lake
        state and pull only new rows; the snapshot sources (MFR, HCMIS, mBrana,
        DHIS2 metadata, eLMIS history) `replace` in place. An unchanged source
        writes almost nothing, so the ingest step does not grow the lake.

    probe_lake  (ml-runner probe-lake)
        Fingerprints every `_manifest.json` sidecar (per-source max watermark
        and row/byte/file totals) into one deterministic hash of the raw tier.

    detect_change  (ShortCircuit)
        Compares the fingerprint with the last successful refresh's, stored in
        the Airflow Variable `pipeline_refresh_fingerprint`. Identical: the DAG
        short-circuits (no dbt, no marts rebuild, no inference). Changed: it
        records the new fingerprint and continues.

    dbt pipeline (ordered), then trigger ml_batch_predict
        ch-raw-views -> processed-zone -> dbt-staging -> bridge-sync -> dbt-vault ->
        dbt-marts -> publish-datasets, then the inference DAG runs so forecasts
        follow the fresh marts.

Adding a source is a new ingest task; the gate and the dbt chain do not change.
The change signal is the lake fingerprint computed by the in-cluster ml-runner,
which already owns object-store I/O, so no per-source probe code is needed.

Why the DAG ships paused
------------------------
The change gate (probe_lake, detect_change) is self-contained and can be run
on its own through the ml-runner `probe-lake` verb. The ingest and dbt stages
depend on two things outside this file:

  1. Image references. As with ml_batch_predict's ML_RUNNER_IMAGE, every image
     below is an env var defaulting to the CI pin (a kustomize `images:` block
     cannot reach a string inside a DAG). The vars: INGESTOR_*_IMAGE,
     STAGING_DBT_IMAGE, VAULT_BRIDGE_IMAGE, VAULT_DBT_IMAGE, MARTS_DBT_IMAGE.
     The dev overlay sets them on the airflow Deployment; the base manifest
     does not.
  2. Canonical specs. The dbt and bridge stages use per-layer images and, for
     processed-zone, an mc-init container, all defined once in
     1_infrastructure/k8s/base/dbt-jobs.yaml (and ingestor-jobs.yaml). The
     ordered DBT_CHAIN and INGESTORS below mirror those Jobs but carry only
     part of their env. The intended production form triggers those Jobs
     instead of re-declaring their pod specs, which needs the airflow Role
     extended to batch/jobs (it grants pods only).

`is_paused_upon_creation=True` keeps the DAG from firing until someone enables it.
"""

from __future__ import annotations

import json
import os

import pendulum
from airflow.decorators import dag, task
from airflow.models import Variable
from airflow.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from kubernetes.client import models as k8s

NAMESPACE = "nhdw"
RAW_BUCKET = "nhdw-raw"
FINGERPRINT_VAR = "pipeline_refresh_fingerprint"

# Object-store endpoint, read from the Airflow Deployment env so the backend can
# change without rebuilding this image. Defaults to the SeaweedFS S3 gateway;
# NHDW_S3_ENDPOINT overrides. MINIO_* names are historical.
MINIO_URL = os.environ.get("NHDW_S3_ENDPOINT", "http://seaweedfs-s3.nhdw.svc.cluster.local:8333")
CH_HOST = "clickhouse.nhdw.svc.cluster.local"
PG_HOST = "postgres-vault.nhdw.svc.cluster.local"
HOST = "host.k3d.internal"          # k3d route to the host-published source replicas

ML_RUNNER_IMAGE = os.environ.get("ML_RUNNER_IMAGE", "nhdw-registry:5500/nhdw/ml-runner:v1")


def _img(env: str, name: str) -> str:
    """Image ref from env (CI-pin default; dev overlay remaps to :dev)."""
    return os.environ.get(env, f"nhdw-registry:5500/nhdw/{name}:v1")


def _sec(name: str, secret: str, key: str) -> k8s.V1EnvVar:
    return k8s.V1EnvVar(name=name, value_from=k8s.V1EnvVarSource(
        secret_key_ref=k8s.V1SecretKeySelector(name=secret, key=key)))


def _plain(name: str, value: str) -> k8s.V1EnvVar:
    return k8s.V1EnvVar(name=name, value=value)


# Lake (S3) credentials, mapped the way every ingestor reads them.
MINIO_ENV = [
    _plain("MINIO_ENDPOINT", MINIO_URL),
    _plain("BOTO_EXPERIMENTAL__NO_EMPTY_CONTINUE", "true"),   # botocore#3755: empty PUT poisons the connection
    _sec("MINIO_ACCESS_KEY", "minio-secrets", "ROOT_USER"),
    _sec("MINIO_SECRET_KEY", "minio-secrets", "ROOT_PASSWORD"),
    _plain("MINIO_RAW_BUCKET", RAW_BUCKET),
    _plain("MINIO_REGION", "et-addis-1"),
]

CH_ENV = [
    _plain("CH_HOST", CH_HOST), _plain("CH_PORT", "8123"), _plain("CH_USER", "nhdw"),
    _sec("CH_PASSWORD", "clickhouse-secrets", "PASSWORD"),
    # build_processed_zone.py reads LAKE_BASE and its code default is a legacy
    # host (minio-datalake:9000), so it must be passed explicitly.
    _plain("LAKE_BASE", MINIO_URL),
    _plain("BOTO_EXPERIMENTAL__NO_EMPTY_CONTINUE", "true"),
]

# Runner env for the probe pod (owns object-store I/O).
RUNNER_ENV = [
    _plain("MLFLOW_S3_ENDPOINT_URL", MINIO_URL),
    _plain("BOTO_EXPERIMENTAL__NO_EMPTY_CONTINUE", "true"),
    _sec("AWS_ACCESS_KEY_ID", "mlflow-secrets", "S3_ACCESS_KEY"),
    _sec("AWS_SECRET_ACCESS_KEY", "mlflow-secrets", "S3_SECRET_KEY"),
    _plain("AWS_DEFAULT_REGION", "et-addis-1"),
]
RUNNER_ENV_FROM = [
    k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name="minio-secrets")),
]

# source -> (image env, image name, args, source-specific env)
INGESTORS = [
    ("dhis2_metadata", "INGESTOR_DHIS2_IMAGE", "dhis2-ingestor", ["metadata"], [
        _plain("DHIS2_BASE_URL", f"http://{HOST}:8080"),
        _sec("DHIS2_USER", "source-creds", "DHIS2_USER"),
        _sec("DHIS2_PASSWORD", "source-creds", "DHIS2_PASSWORD"),
        _plain("DHIS2_PAGE_SIZE", "200")]),
    ("dhis2_dvs", "INGESTOR_DHIS2_IMAGE", "dhis2-ingestor", ["data-value-sets"], [
        _plain("DHIS2_BASE_URL", f"http://{HOST}:8080"),
        _sec("DHIS2_USER", "source-creds", "DHIS2_USER"),
        _sec("DHIS2_PASSWORD", "source-creds", "DHIS2_PASSWORD"),
        _plain("DHIS2_PAGE_SIZE", "200"),
        _plain("DHIS2_INITIAL_LAST_UPDATED", "2020-01-01")]),
    ("climate", "INGESTOR_CLIMATE_IMAGE", "climate-ingestor", ["weekly"], [
        _plain("CLIMATE_API_BASE_URL", f"http://{HOST}:8095")]),
    ("ethioemr", "INGESTOR_ETHIOEMR_IMAGE", "ethioemr-ingestor", ["extract"], [
        _plain("ETHIOEMR_MYSQL_HOST", HOST), _plain("ETHIOEMR_MYSQL_PORT", "33306"),
        _plain("ETHIOEMR_MYSQL_DB", "openmrs"), _plain("ETHIOEMR_MYSQL_USER", "ingestor"),
        _sec("ETHIOEMR_MYSQL_PASSWORD", "source-creds", "ETHIOEMR_READER_PASSWORD")]),
    ("hcmiswdm", "INGESTOR_HCMISWDM_IMAGE", "hcmiswdm-ingestor", ["extract"], [
        _plain("HCMISWDM_HOST", HOST), _plain("HCMISWDM_PORT", "1433"),
        _plain("HCMISWDM_DATABASE", "HCMISWDM"), _plain("HCMISWDM_USER", "nscd_reader"),
        _sec("HCMISWDM_PASSWORD", "source-creds", "HCMISWDM_READER_PASSWORD"),
        _plain("HCMISWDM_TRUST_SERVER_CERTIFICATE", "yes")]),
    ("mbrana", "INGESTOR_MBRANA_IMAGE", "mbrana-ingestor", ["extract"], [
        _plain("MBRANA_HOST", HOST), _plain("MBRANA_PORT", "1433"),
        _plain("MBRANA_DATABASE", "HCMISWCloud"), _plain("MBRANA_USER", "ingestor"),
        _sec("MBRANA_PASSWORD", "source-creds", "MBRANA_READER_PASSWORD"),
        _plain("MBRANA_TRUST_SERVER_CERTIFICATE", "yes")]),
    ("mfr", "INGESTOR_MFR_IMAGE", "mfr-ingestor", ["all"], [
        _plain("MFR_FHIR_BASE_URL", f"http://{HOST}:8093/fhir")]),
    ("elmis_hist", "INGESTOR_OPENLMIS_IMAGE", "openlmis-ingestor", ["history"], [
        _plain("OPENLMIS_HIST_HOST", HOST), _plain("OPENLMIS_HIST_PORT", "5434"),
        _plain("OPENLMIS_HIST_DB", "elmis"),
        _sec("OPENLMIS_HIST_USER", "source-creds", "OPENLMIS_HIST_USER"),
        _sec("OPENLMIS_HIST_PASSWORD", "source-creds", "OPENLMIS_HIST_PASSWORD"),
        _plain("DLT_PIPELINE_NAME", "openlmis")]),   # eLMIS historical lane; immutable, so replace
]

# ordered dbt / bridge pipeline -> (task_id, image env, image name, args, env)
# Image names and args mirror the canonical Jobs in 1_infrastructure/k8s/base/
# dbt-jobs.yaml (per-layer images: staging-dbt, vault-bridge, vault-dbt,
# marts-dbt), which remain the source of truth. The full per-stage env (the
# bridge's CH native port and landing PG creds, processed-zone's mc-init
# container) lives there; see the module docstring. These rows give the order.
DBT_CHAIN = [
    ("ch_raw_views", "STAGING_DBT_IMAGE", "staging-dbt", [], CH_ENV),
    # Runs the same builder as the canonical processed-zone Job (see _CMDS);
    # without an explicit command the image's dbt entrypoint prints --help and
    # exits 0. build_processed_zone.py uses s3_truncate_on_insert, so re-runs are
    # idempotent without the Job's mc wipe.
    ("processed_zone", "STAGING_DBT_IMAGE", "staging-dbt", [], CH_ENV),
    ("dbt_staging", "STAGING_DBT_IMAGE", "staging-dbt", ["build", "--target", "dev"], CH_ENV),
    ("bridge_sync", "VAULT_BRIDGE_IMAGE", "vault-bridge", [], CH_ENV + [
        _plain("PG_HOST", PG_HOST), _plain("PG_PORT", "5432"),
        _plain("PG_DATABASE", "nhdw_vault"), _plain("PG_SCHEMA", "nhdw_landing"),
        _plain("PG_USER", "landing_writer"),   # required by bridge/sync.py, as in the Job manifest
        _sec("PG_PASSWORD", "postgres-vault-secrets", "LANDING_WRITER_PASSWORD")]),
    ("dbt_vault", "VAULT_DBT_IMAGE", "vault-dbt", [], CH_ENV),
    ("dbt_marts", "MARTS_DBT_IMAGE", "marts-dbt", [], CH_ENV),
    ("publish_datasets", "MARTS_DBT_IMAGE", "marts-dbt", [], CH_ENV),
]


# Rows whose container must run something other than the image's dbt entrypoint.
_CMDS = {
    "processed_zone": ["python", "/dbt/scripts/build_processed_zone.py"],
}


def _pod(task_id: str, image: str, args: list[str], env, name: str,
         env_from=None):
    return KubernetesPodOperator(
        task_id=task_id, name=name, namespace=NAMESPACE, image=image,
        image_pull_policy="IfNotPresent", cmds=_CMDS.get(task_id),
        arguments=args, env_vars=env,
        env_from=env_from or [], in_cluster=True, get_logs=True,
        on_finish_action="delete_succeeded_pod", startup_timeout_seconds=300)


@task.short_circuit
def detect_change(probe_result: dict) -> bool:
    """Continue only if the lake fingerprint differs from the last refresh.

    On change, record the new fingerprint so the NEXT run compares against it.
    On no change, short-circuit: the whole dbt and inference subtree is skipped.
    """
    fp = (probe_result or {}).get("fingerprint")
    last = Variable.get(FINGERPRINT_VAR, default_var=None)
    if fp and fp == last:
        print(f"[pipeline_refresh] lake unchanged (fingerprint {fp[:12]}); "
              "skipping dbt rebuild + inference")
        return False
    print(f"[pipeline_refresh] lake CHANGED ({str(last)[:12]} -> {str(fp)[:12]}); "
          "rebuilding marts")
    if fp:
        Variable.set(FINGERPRINT_VAR, fp)
    return True


@dag(
    dag_id="pipeline_refresh",
    schedule="0 2 * * *",  # nightly 02:00 UTC, ahead of inference (04:00)
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1},
    is_paused_upon_creation=True,  # see "Why the DAG ships paused" above
    tags=["pipeline", "ingest", "dbt"],
    doc_md=__doc__,
)
def pipeline_refresh():
    ingest = [
        _pod(f"ingest_{tid}", _img(ienv, iname), args,
             MINIO_ENV + extra, name=f"ingest-{tid.replace('_', '-')}")
        for tid, ienv, iname, args, extra in INGESTORS
    ]

    probe = KubernetesPodOperator(
        task_id="probe_lake", name="pipeline-probe-lake", namespace=NAMESPACE,
        image=ML_RUNNER_IMAGE, image_pull_policy="IfNotPresent",
        arguments=["probe-lake", "--bucket", RAW_BUCKET],
        env_vars=RUNNER_ENV, env_from=RUNNER_ENV_FROM, in_cluster=True,
        get_logs=True, do_xcom_push=True,
        on_finish_action="delete_succeeded_pod", startup_timeout_seconds=300)

    gate = detect_change(probe.output)

    prev = None
    dbt_tasks = []
    for tid, ienv, iname, args, env in DBT_CHAIN:
        t = _pod(tid, _img(ienv, iname), args, env, name=tid.replace("_", "-"))
        dbt_tasks.append(t)
        if prev is not None:
            prev >> t
        prev = t

    run_inference = TriggerDagRunOperator(
        task_id="trigger_inference", trigger_dag_id="ml_batch_predict",
        wait_for_completion=False)

    ingest >> probe >> gate >> dbt_tasks[0]
    dbt_tasks[-1] >> run_inference


pipeline_refresh()
