"""openmetadata_ingestion: feeds the OpenMetadata catalog from the platform.

One KubernetesPodOperator task per connector, each running the pinned
openmetadata/ingestion image with a workflow YAML written at pod start
(same shape as `metadata ingest -c`):

    dbt         model and column lineage from the dbt artifacts in the lake
                (nhdw-artifacts/dbt/{staging,vault,marts}/manifest+catalog.json)
    postgres    nhdw_vault: bi_* marts, raw/business vault, landing schemas
    clickhouse  nhdw_staging raw_/stg_ views
    datalake    the SeaweedFS S3 raw zone (bucket nhdw-raw)
    airflow     this Airflow's own DAGs, into the OM pipeline service

Auth: the `ingestion-bot` JWT from openmetadata-secrets/INGESTION_BOT_TOKEN
(issued by an OM admin; see 1_infrastructure/k8s/base/openmetadata.yaml).
OpenMetadata is catalog-only (never in the query or enforcement path), so
every task is best-effort: a failed connector run leaves the platform
untouched and the next scheduled run catches up. Runs daily at 05:00 UTC,
after the nightly pipeline and inference jobs.

Connector creds are existing platform identities: bi_reader, the ClickHouse
app user, the SeaweedFS admin identity for bucket listing, and the vault admin
role for reading the Airflow metadata DB.
"""
from __future__ import annotations

import os

import pendulum
from airflow.decorators import dag
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from kubernetes.client import models as k8s

INGESTION_IMAGE = os.environ.get(
    "NHDW_OM_INGESTION_IMAGE", "docker.getcollate.io/openmetadata/ingestion:2.0.2"
)
OM_API = os.environ.get(
    "NHDW_OM_API", "http://openmetadata.nhdw.svc.cluster.local:8585/api"
)
S3_URL = os.environ.get("NHDW_S3_ENDPOINT", "http://seaweedfs-s3.nhdw.svc.cluster.local:8333")


def _secret_env(name: str, secret: str, key: str) -> k8s.V1EnvVar:
    return k8s.V1EnvVar(
        name=name,
        value_from=k8s.V1EnvVarSource(
            secret_key_ref=k8s.V1SecretKeySelector(name=secret, key=key)
        ),
    )


_COMMON_ENV = [
    _secret_env("OM_JWT", "openmetadata-secrets", "INGESTION_BOT_TOKEN"),
]

_WORKFLOW_TAIL = """
sink:
  type: metadata-rest
  config: {}
workflowConfig:
  loggerLevel: INFO
  openMetadataServerConfig:
    hostPort: %(api)s
    authProvider: openmetadata
    securityConfig:
      jwtToken: ${OM_JWT}
""" % {"api": OM_API}

_CONNECTORS: dict[str, dict] = {
    "postgres": {
        "env": [
            _secret_env("SRC_PASS", "postgres-vault-secrets", "BI_READER_PASSWORD"),
        ],
        "yaml": """
source:
  type: postgres
  serviceName: nhdw-vault
  serviceConnection:
    config:
      type: Postgres
      hostPort: postgres-vault.nhdw.svc.cluster.local:5432
      username: bi_reader
      authType:
        password: ${SRC_PASS}
      database: nhdw_vault
  sourceConfig:
    config:
      type: DatabaseMetadata
      schemaFilterPattern:
        includes: ["bi_.*", "nhdw_vault_raw", "nhdw_vault_business", "nhdw_landing", "nhdw_vault_governance", "nhdw_vault_ml"]
""",
    },
    "clickhouse": {
        "env": [
            _secret_env("SRC_PASS", "clickhouse-secrets", "PASSWORD"),
        ],
        "yaml": """
source:
  type: clickhouse
  serviceName: nhdw-staging
  serviceConnection:
    config:
      type: Clickhouse
      hostPort: clickhouse.nhdw.svc.cluster.local:8123
      username: nhdw
      password: ${SRC_PASS}
      databaseSchema: nhdw_staging
  sourceConfig:
    config:
      type: DatabaseMetadata
""",
    },
    "datalake": {
        "env": [
            _secret_env("SRC_KEY", "seaweedfs-secrets", "ADMIN_ACCESS_KEY"),
            _secret_env("SRC_SECRET", "seaweedfs-secrets", "ADMIN_SECRET_KEY"),
        ],
        "yaml": """
source:
  type: datalake
  serviceName: nhdw-lake
  serviceConnection:
    config:
      type: Datalake
      configSource:
        securityConfig:
          awsAccessKeyId: ${SRC_KEY}
          awsSecretAccessKey: ${SRC_SECRET}
          awsRegion: us-east-1   # OM validates against real AWS regions; SeaweedFS ignores it
          endPointURL: %(s3)s
      bucketName: nhdw-raw
  sourceConfig:
    config:
      type: DatabaseMetadata
""" % {"s3": S3_URL},
    },
    "dbt": {
        "env": [
            _secret_env("SRC_KEY", "seaweedfs-secrets", "ADMIN_ACCESS_KEY"),
            _secret_env("SRC_SECRET", "seaweedfs-secrets", "ADMIN_SECRET_KEY"),
        ],
        # One dbt pass per project, run sequentially in a single pod. Each
        # project's lineage must land on the service that holds its models:
        # staging builds in ClickHouse (nhdw-staging), vault and marts in
        # Postgres (nhdw-vault). Mapping staging to nhdw-vault would drop every
        # raw-view to staging-model edge, because those tables are not there.
        "script_override": """
set -e
for PRJ in staging vault marts; do
if [ "$PRJ" = staging ]; then SVC=nhdw-staging; else SVC=nhdw-vault; fi
cat > /tmp/dbt-$PRJ.yaml <<CFG
source:
  type: dbt
  serviceName: $SVC
  sourceConfig:
    config:
      type: DBT
      dbtConfigSource:
        dbtConfigType: s3
        dbtSecurityConfig:
          awsAccessKeyId: ${SRC_KEY}
          awsSecretAccessKey: ${SRC_SECRET}
          awsRegion: us-east-1   # OM validates against real AWS regions; SeaweedFS ignores it
          endPointURL: %(s3)s
        dbtPrefixConfig:
          dbtBucketName: nhdw-artifacts
          dbtObjectPrefix: dbt/$PRJ
%(tail)s
CFG
metadata ingest -c /tmp/dbt-$PRJ.yaml || echo "dbt $PRJ ingest failed (best-effort)"
done
""" % {"s3": S3_URL, "tail": _WORKFLOW_TAIL},
    },
    "airflow": {
        "env": [
            _secret_env("SRC_PASS", "postgres-vault-secrets", "POSTGRES_PASSWORD"),
        ],
        "yaml": """
source:
  type: airflow
  serviceName: nhdw-airflow
  serviceConnection:
    config:
      type: Airflow
      hostPort: http://airflow.nhdw.svc.cluster.local:8080
      numberOfStatus: 10
      connection:
        type: Postgres
        username: vault_admin
        authType:
          password: ${SRC_PASS}
        hostPort: postgres-vault.nhdw.svc.cluster.local:5432
        database: nhdw_airflow
  sourceConfig:
    config:
      type: PipelineMetadata
""",
    },
}


def _task(name: str, spec: dict) -> KubernetesPodOperator:
    if "script_override" in spec:
        script = spec["script_override"]
    else:
        script = "cat > /tmp/c.yaml <<CFG\n%s%s\nCFG\nmetadata ingest -c /tmp/c.yaml\n" % (
            spec["yaml"].strip("\n"),
            _WORKFLOW_TAIL.rstrip("\n"),
        )
    return KubernetesPodOperator(
        task_id=f"ingest_{name}",
        name=f"om-ingest-{name}",
        namespace="nhdw",
        image=INGESTION_IMAGE,
        image_pull_policy="IfNotPresent",
        cmds=["bash", "-c"],
        arguments=[script],
        env_vars=_COMMON_ENV + spec.get("env", []),
        get_logs=True,
        is_delete_operator_pod=True,
        # catalog-only: a failed connector must not fail the whole DAG run
        trigger_rule="all_done",
    )


@dag(
    dag_id="openmetadata_ingestion",
    description="Feed the OpenMetadata catalog: dbt column lineage, Postgres, ClickHouse, lake, Airflow.",
    schedule="0 5 * * *",
    start_date=pendulum.datetime(2026, 9, 1, tz="UTC"),
    catchup=False,
    tags=["metadata", "openmetadata"],
)
def openmetadata_ingestion():
    # postgres, clickhouse and datalake register the assets; dbt then overlays
    # model and column lineage; airflow adds the pipeline hop.
    pg = _task("postgres", _CONNECTORS["postgres"])
    ch = _task("clickhouse", _CONNECTORS["clickhouse"])
    lake = _task("datalake", _CONNECTORS["datalake"])
    dbt = _task("dbt", _CONNECTORS["dbt"])
    af = _task("airflow", _CONNECTORS["airflow"])
    [pg, ch, lake] >> dbt >> af


openmetadata_ingestion()
