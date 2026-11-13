"""dq_detection: runs the data-quality detector on a schedule.

The detection logic lives in the gateway (``routes/dq.py``,
``POST /api/dq/detect``): failing dbt tests from the lake artifacts, stale
ingests against the ops_governance.dq_threshold SLAs, per-table volume
outliers, and the undecided MDM steward backlog. Findings are deduplicated by
``dq_flag.fingerprint`` and appended to the durable ``dq_flag_event`` history,
which feeds the governance KPIs. As with alert_evaluation, this DAG only calls
the endpoint.

Incidents land in the in-app queue only. The detector has no email path; any
mailer integration must sit behind an explicit ``DQ_EMAIL_ENABLED=true``
(default false, because the SMTP relay delivers to real inboxes).

Runs daily at 06:15 UTC, after the OpenMetadata catalog ingestion (05:00) and
ml_drift_check (06:00), outside pipeline hours. Re-runs are no-ops on
unchanged findings.

Env contract (base/airflow.yaml scheduler container):
  NHDW_GATEWAY_URL   in-cluster gateway base URL.
  DQ_DETECT_TOKEN    shared service token (dq-detect-secrets/TOKEN), sent as
                     the X-Detect-Token header.
"""
from __future__ import annotations

import os

import pendulum
from airflow.decorators import dag, task

GATEWAY_URL = os.environ.get(
    "NHDW_GATEWAY_URL", "http://nhdw-gateway.nhdw.svc.cluster.local:8000"
)


@task
def run_detection() -> dict:
    """POST the gateway's detect endpoint with the service token."""
    import requests

    token = os.environ.get("DQ_DETECT_TOKEN", "")
    if not token:
        raise RuntimeError("DQ_DETECT_TOKEN not set; cannot authorize the detect call")
    resp = requests.post(
        f"{GATEWAY_URL}/api/dq/detect",
        headers={"X-Detect-Token": token},
        timeout=120,
    )
    resp.raise_for_status()
    summary = resp.json()
    print(f"[dq_detection] {summary}")
    return summary


@dag(
    dag_id="dq_detection",
    description="Raise DQ incidents (dbt failures, stale sources, volume outliers, MDM backlog) into the governed queue.",
    schedule="15 6 * * *",  # daily 06:15 UTC, after the OM catalog ingest (05:00) and ml_drift_check (06:00)
    start_date=pendulum.datetime(2026, 9, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1},
    doc_md=__doc__,
    tags=["dq", "governance"],
)
def dq_detection():
    run_detection()


dq_detection()
