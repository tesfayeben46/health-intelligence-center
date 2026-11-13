"""alert_evaluation: fires disease-surveillance alert notifications.

Every active ``ops_workspace.subscription`` is matched against the latest
forecast's alarm probability (the same signal the dz-* disease widgets render);
a crossing writes an owner-scoped ``ops_workspace.alert_notification``
(idempotent per forecast week) and best-effort emails the owner via the
centralised gateway mailer (external Google SMTP relay).

The matching and firing logic lives in the gateway (``routes/alerts.py``,
``POST /api/alerts/evaluate``), which already owns the disease/region
reconciliation and the Trino signal query. This DAG only calls that endpoint
on a schedule, so the reconciliation is not duplicated.

Runs every 15 minutes (the 'realtime' subscription cadence), so it picks up a
new forecast soon after ml_batch_predict writes one; re-runs against the same
forecast week are no-ops because of the unique dedup index.

Env contract (base/airflow.yaml scheduler container):
  NHDW_GATEWAY_URL   in-cluster gateway base URL.
  ALERT_EVAL_TOKEN   shared service token (alert-eval-secrets/TOKEN), sent as the
                     X-Eval-Token header; the gateway authorizes the call.
"""
from __future__ import annotations

import os

import pendulum
from airflow.decorators import dag, task

GATEWAY_URL = os.environ.get(
    "NHDW_GATEWAY_URL", "http://nhdw-gateway.nhdw.svc.cluster.local:8000"
)


@task
def evaluate_subscriptions() -> dict:
    """POST the gateway's evaluate endpoint with the service token."""
    import requests

    token = os.environ.get("ALERT_EVAL_TOKEN", "")
    if not token:
        raise RuntimeError(
            "ALERT_EVAL_TOKEN not set; cannot authorize the evaluate call"
        )
    resp = requests.post(
        f"{GATEWAY_URL}/api/alerts/evaluate",
        headers={"X-Eval-Token": token},
        timeout=60,
    )
    resp.raise_for_status()
    summary = resp.json()
    print(f"[alert_evaluation] {summary}")
    return summary


@dag(
    dag_id="alert_evaluation",
    schedule="*/15 * * * *",  # every 15 min, for 'realtime' subscriptions
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1},
    tags=["alerts"],
    doc_md=__doc__,
)
def alert_evaluation():
    evaluate_subscriptions()


alert_evaluation()
