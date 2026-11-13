"""Postgres I/O for the ml-runner: the only layer that touches a database.

Two logical connections:
  * ops        (nhdw_ops):   ops_ml.ml_run lifecycle + ops_ml.ml_predictions
  * warehouse  (nhdw_vault): read-only feature queries (feature_spec.yaml)

Model packages never see credentials: the runner hands them a DataFrame and
takes a DataFrame back. The env contract adds no new secret material:

  OPS_PG_HOST      default postgres-vault.nhdw.svc.cluster.local
  OPS_PG_PORT      default 5432
  OPS_PG_USER      default vault_admin
  OPS_PG_DB        default nhdw_ops
  VAULT_ADMIN_PASSWORD   (required; postgres-vault-secrets/POSTGRES_PASSWORD)
  MARTS_PG_HOST/PORT/USER/PASSWORD  default to the OPS values
  MARTS_PG_DB      default nhdw_vault

Idempotency contract (ops_ml.ml_predictions): DELETE WHERE run_id = $1 then
INSERT, in one transaction: re-running a run_id is always safe.
"""
from __future__ import annotations

import json
import logging
import os
import uuid

import pandas as pd
import psycopg2
from psycopg2.extras import Json, execute_values

log = logging.getLogger("runner.db")


def _ops_params() -> dict:
    return dict(
        host=os.environ.get("OPS_PG_HOST", "postgres-vault.nhdw.svc.cluster.local"),
        port=int(os.environ.get("OPS_PG_PORT", "5432")),
        user=os.environ.get("OPS_PG_USER", "vault_admin"),
        password=os.environ["VAULT_ADMIN_PASSWORD"],
        dbname=os.environ.get("OPS_PG_DB", "nhdw_ops"),
    )


def _warehouse_params() -> dict:
    ops = _ops_params()
    return dict(
        host=os.environ.get("MARTS_PG_HOST", ops["host"]),
        port=int(os.environ.get("MARTS_PG_PORT", str(ops["port"]))),
        user=os.environ.get("MARTS_PG_USER", ops["user"]),
        password=os.environ.get("MARTS_PG_PASSWORD", ops["password"]),
        dbname=os.environ.get("MARTS_PG_DB", "nhdw_vault"),
    )


def fetch_df(sql: str) -> pd.DataFrame:
    """Run a read-only feature query against the warehouse -> DataFrame.

    Cursor-based (no sqlalchemy) so column names come straight from the
    cursor description and psycopg2 stays the single PG dependency.
    """
    with psycopg2.connect(**_warehouse_params()) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [d.name for d in cur.description]
            rows = cur.fetchall()
    log.info("warehouse query returned %d rows by %d cols", len(rows), len(columns))
    return pd.DataFrame(rows, columns=columns)


# ops_ml.ml_run lifecycle

def open_run(model_name: str, model_version: str, deployment_kind: str,
             trigger_kind: str, run_label: str, input_filters: dict,
             mlflow_run_id: str | None = None) -> str:
    """INSERT a status='running' ml_run row; returns its uuid."""
    run_id = str(uuid.uuid4())
    with psycopg2.connect(**_ops_params()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ops_ml.ml_run
                    (id, model_name, model_version, deployment_kind,
                     trigger_kind, input_filters, run_label, mlflow_run_id)
                VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s);
                """,
                (run_id, model_name, model_version, deployment_kind,
                 trigger_kind, Json(input_filters), run_label, mlflow_run_id))
        conn.commit()
    log.info("ml_run %s opened (%s %s v%s)", run_id, deployment_kind,
             model_name, model_version)
    return run_id


def close_run(run_id: str, status: str, row_count: int | None = None,
              latency_ms: int | None = None, error_msg: str | None = None,
              reference_profile_uri: str | None = None) -> None:
    """Close an open ml_run row (status ∈ success|failed)."""
    with psycopg2.connect(**_ops_params()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ops_ml.ml_run
                   SET status = %s, finished_at = now(), row_count = %s,
                       latency_ms = %s, error_msg = %s,
                       reference_profile_uri = COALESCE(%s, reference_profile_uri)
                 WHERE id = %s::uuid;
                """,
                (status, row_count, latency_ms, error_msg,
                 reference_profile_uri, run_id))
        conn.commit()
    log.info("ml_run %s closed · %s · rows=%s · %sms", run_id, status,
             row_count, latency_ms)


def record_run(model_name: str, model_version: str, deployment_kind: str,
               trigger_kind: str, run_label: str, input_filters: dict,
               status: str, row_count: int | None, latency_ms: int | None,
               error_msg: str | None = None, mlflow_run_id: str | None = None,
               reference_profile_uri: str | None = None) -> str:
    """One-shot ml_run insert for runs whose version is only known at the end
    (train mode registers a new version, so open-then-close doesn't fit)."""
    run_id = str(uuid.uuid4())
    with psycopg2.connect(**_ops_params()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ops_ml.ml_run
                    (id, model_name, model_version, deployment_kind,
                     trigger_kind, input_filters, run_label, mlflow_run_id,
                     reference_profile_uri, status, finished_at,
                     row_count, latency_ms, error_msg)
                VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(),
                        %s, %s, %s);
                """,
                (run_id, model_name, model_version, deployment_kind,
                 trigger_kind, Json(input_filters), run_label, mlflow_run_id,
                 reference_profile_uri, status, row_count, latency_ms,
                 error_msg))
        conn.commit()
    log.info("ml_run %s recorded · %s · %s v%s", run_id, status, model_name,
             model_version)
    return run_id


# ops_ml.ml_predictions writer (FLAT forecast schema)

_INSERT_COLUMNS = (
    "run_id", "model_name", "model_version", "disease", "district",
    "year", "week",
    "pred_cases_mean", "pred_cases_sd", "pred_cases_lwr95",
    "pred_cases_median", "pred_cases_upr95",
    "endemic_mean", "endemic_sd", "outbreak_z",
    "alarm_threshold", "alarm_prob", "alarm_flag", "n_draws", "attrs",
)


def write_predictions(run_id: str, records: list[dict]) -> int:
    """DELETE WHERE run_id, then bulk-insert: the idempotency contract.

    `records` come pre-validated from schemas.validate_flat_output(); each is
    a dict keyed by the flat-schema columns (attrs already a plain dict).
    """
    payload = [
        (run_id, r["model_name"], r["model_version"], r["disease"],
         r["district"], r["year"], r["week"],
         r["pred_cases_mean"], r["pred_cases_sd"], r["pred_cases_lwr95"],
         r["pred_cases_median"], r["pred_cases_upr95"],
         r["endemic_mean"], r["endemic_sd"], r["outbreak_z"],
         r["alarm_threshold"], r["alarm_prob"], r["alarm_flag"],
         r["n_draws"], Json(r["attrs"]))
        for r in records
    ]
    with psycopg2.connect(**_ops_params()) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ops_ml.ml_predictions WHERE run_id = %s::uuid;",
                        (run_id,))
            execute_values(
                cur,
                f"INSERT INTO ops_ml.ml_predictions ({', '.join(_INSERT_COLUMNS)}) VALUES %s",
                payload,
                template="(%s::uuid, " + ", ".join(["%s"] * (len(_INSERT_COLUMNS) - 1)) + ")",
                page_size=1000)
        conn.commit()
    log.info("ml_predictions: %d rows written for run %s", len(payload), run_id)
    return len(payload)


# deployment registry read (drift-triggered retrain needs the package ref) ─

def read_deployment_params(model_name: str) -> dict:
    """ops_ml.model_deployment.params for one model ({} if no row).

    The drift-triggered retrain resolves its package the same way the gateway's
    /retrain does: params.package_ref when set, else the conventional
    ml-packages/<name>/current/ location (see routes/ml.py)."""
    with psycopg2.connect(**_ops_params()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT params FROM ops_ml.model_deployment "
                        "WHERE model_name = %s;", (model_name,))
            row = cur.fetchone()
    return (row[0] if row and isinstance(row[0], dict) else {}) or {}


# latest predictions run (drift + performance attach to it, never open a new
#    ml_run: a monitoring run must not outrank the predict run in the
# latest-successful-run reads that feed the forecast widgets)

def latest_predict_run(model_name: str) -> tuple[str, str] | None:
    """(run_id, model_version) of the model's most recent successful run that
    actually wrote predictions, or None. Mirrors the gateway's guarded
    latest-run pattern (fetchers.py); a train/drift run writes no
    ml_predictions and is correctly skipped."""
    with psycopg2.connect(**_ops_params()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id::text, r.model_version
                FROM ops_ml.ml_run r
                WHERE r.model_name = %s AND r.status = 'success'
                  AND r.id IN (SELECT run_id FROM ops_ml.ml_predictions
                               WHERE model_name = %s)
                ORDER BY r.finished_at DESC NULLS LAST
                LIMIT 1;
                """,
                (model_name, model_name))
            row = cur.fetchone()
    return (row[0], row[1]) if row else None


def fetch_predictions_df(run_id: str, model_name: str) -> pd.DataFrame:
    """The forecast rows of one run, for the performance scorer's join."""
    with psycopg2.connect(**_ops_params()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT disease, district, year, week, model_version,
                       pred_cases_mean, pred_cases_lwr95, pred_cases_upr95
                FROM ops_ml.ml_predictions
                WHERE run_id = %s::uuid AND model_name = %s;
                """,
                (run_id, model_name))
            cols = [d.name for d in cur.description]
            rows = cur.fetchall()
    return pd.DataFrame(rows, columns=cols)


# ops_ml.model_drift writer (per-run, per-feature PSI/KS)

def write_drift(run_id: str, model_name: str, records: list[dict],
                psi_threshold: float, note: str | None = None) -> int:
    """DELETE the model's rows for this run then INSERT: idempotent re-runs
    (same DELETE-by-run_id contract as ml_predictions)."""
    payload = [
        (run_id, model_name, r["feature"], r["psi"], r["ks"], r["drifted"],
         r.get("kind"), psi_threshold, note)
        for r in records
    ]
    with psycopg2.connect(**_ops_params()) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ops_ml.model_drift "
                        "WHERE run_id = %s::uuid AND model_name = %s;",
                        (run_id, model_name))
            if payload:
                execute_values(
                    cur,
                    "INSERT INTO ops_ml.model_drift "
                    "(run_id, model_name, feature, psi, ks, drifted, kind, "
                    " psi_threshold, note) VALUES %s",
                    payload,
                    template="(%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s)",
                    page_size=500)
        conn.commit()
    log.info("model_drift: %d feature rows written for %s (run %s)",
             len(payload), model_name, run_id)
    return len(payload)


# ops_ml.model_performance writer (windowed accuracy vs mart actuals)

def write_performance(model_name: str, model_version: str,
                      window_start, window_end, metrics: dict,
                      run_id: str | None, sample_size: int | None) -> int:
    """One row per metric. DELETE the (model, window) triple first so
    re-scoring the same window is idempotent while new windows append (a
    accuracy time series)."""
    payload = [
        (model_name, model_version, window_start, window_end, metric,
         (None if value is None else float(value)), run_id, sample_size)
        for metric, value in metrics.items()
    ]
    with psycopg2.connect(**_ops_params()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM ops_ml.model_performance "
                "WHERE model_name = %s AND window_start IS NOT DISTINCT FROM %s "
                "  AND window_end IS NOT DISTINCT FROM %s;",
                (model_name, window_start, window_end))
            if payload:
                execute_values(
                    cur,
                    "INSERT INTO ops_ml.model_performance "
                    "(model_name, model_version, window_start, window_end, "
                    " metric, value, run_id, sample_size) VALUES %s",
                    payload,
                    template="(%s, %s, %s, %s, %s, %s, %s::uuid, %s)",
                    page_size=100)
        conn.commit()
    log.info("model_performance: %d metric rows written for %s (%s to %s)",
             len(payload), model_name, window_start, window_end)
    return len(payload)


def to_jsonable(obj):
    """Best-effort conversion for attrs values (numpy scalars, dates, …)."""
    return json.loads(json.dumps(obj, default=str))
