"""/api/ml: the ML API used by every dashboard widget.

MLflow alias-based registry reads plus ephemeral runner Jobs. There is no
resident model-serving service and no per-model CronJob:

  GET  /api/ml                         list registered models (MLflow aliases
                                        + ops_ml.model_deployment join)
  GET  /api/ml/{model}                 one model: registry, deployment, last run
  GET  /api/ml/{model}/schema          input/output schema (MLflow signature and
                                        input_example); catalogue metadata, no
                                        stored prediction data
  GET  /api/ml/{model}/predictions     batch output in the flat forecast schema
                                        (ops_ml.ml_predictions)
  POST /api/ml/{model}/predict         ad-hoc inference: stage the input CSV in
                                        the object store, start a runner Job
                                        (predict-adhoc), return the job id
  GET  /api/ml/jobs/{job_id}           poll a runner Job: status and result URI
  POST /api/ml/{model}/retrain         spawn a runner train Job for the
                                        model's package
  POST /api/ml/{model}/promote         set an MLflow alias (@champion /
                                        @challenger) on a version
  GET  /api/ml/{model}/runs            recent ops_ml.ml_run rows
  GET  /api/ml/{model}/registered      raw MLflow metadata
  GET  /api/ml/{model}/versions        all MLflow versions and their aliases

  The runner Job (image nhdw/ml-runner) performs all database, MLflow and object
  store I/O for its run, including the ops_ml.ml_run row, so the gateway only
  creates the Job and polls it. RBAC comes from the pod's ServiceAccount
  (gateway-rbac.yaml: batch jobs create, get, list, delete).

  Every call is gated and audit-logged like marts, exports and observability,
  with three classes:
    * Catalogue metadata (detail, schema, versions, registered, runs, jobs, and
      predict on the caller's own CSV) is PRG/DC.CAT, the workshop personas'
      surface (R-CONS-SCI and R-CONS-RES hold a DC.CAT exception; their
      health-data deny-all stands). /schema exposes only the input/output shape,
      never stored predictions.
    * Stored predictions (/predictions) are PRG/DC.PRO health-data aggregates
      (analysts and decision makers only).
    * Model monitoring (/monitoring/fleet, /{model}/drift, /{model}/performance)
      is an admin-console surface, gated by the view_ops_console capability. The
      workshop Models page is usage-only and does not read monitoring data.
  Retrain and promote are operator actions with no data-plane verb; _manage_gate
  restricts them to R-OPS-ADM, R-STEW and R-SUPER (the reviewer set).
  """
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import time
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query

from ..auth import UserCtx, get_user
from ..settings import settings
from .. import minio_client, ops_db, opa

log = logging.getLogger("gateway.ml")
router = APIRouter(prefix="/api/ml", tags=["ml"])

MLFLOW_URL = os.environ.get("MLFLOW_URL", "http://mlflow.nhdw.svc.cluster.local:5000")

# The runner image the gateway launches for train and predict-adhoc Jobs.
# Base manifests pin the CI registry name; the dev overlay sets this env to the
# locally imported nhdw/ml-runner:dev, because kustomize `images:` remaps only
# manifest image fields, not Jobs created at runtime.
ML_RUNNER_IMAGE  = os.environ.get("ML_RUNNER_IMAGE",
                                  "nhdw-registry:5500/nhdw/ml-runner:v1")
# Where uploaded and seeded model packages live (runner package contract).
ML_PACKAGES_BASE = os.environ.get("ML_PACKAGES_BASE",
                                  "minio://nhdw-artifacts/ml-packages")
ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET", "nhdw-artifacts")
ADHOC_PREFIX     = "ml-adhoc"          # staging area inside ARTIFACTS_BUCKET

VALID_ALIASES = {"champion", "challenger"}

# Flat forecast schema (3_storage/postgres/init/11_ml_predictions.sh).
_PRED_COLS = ("predicted_at, model_version, disease, district, year, week, "
              "pred_cases_mean, pred_cases_sd, pred_cases_lwr95, "
              "pred_cases_median, pred_cases_upr95, endemic_mean, endemic_sd, "
              "outbreak_z, alarm_threshold, alarm_prob, alarm_flag, n_draws, attrs")

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")


# Helpers
def _manage_gate(user: UserCtx) -> opa.Decision:
    """Retrain and promote are operator actions, gated directly by role: the
    data-plane Rego rules have no 'manage' verb. The check uses the full role
    set, not primary_role, which is only the first R- role in an unordered
    Keycloak claim; a multi-role operator (e.g. ['R-CONS-ANA','R-STEW'])
    must not be denied."""
    allowed = any(r in ("R-OPS-ADM", "R-STEW", "R-SUPER") for r in user.roles)
    return opa.Decision(
        allow=allowed,
        reason=f"ml manage gate: roles {user.roles} "
               f"{'allowed' if allowed else 'denied'} (operators only)")


def _check_model_name(model: str) -> None:
    if not _MODEL_RE.match(model):
        raise HTTPException(400, f"invalid model name {model!r}")


async def _mlflow_get(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"{MLFLOW_URL}{path}", params=params)
            r.raise_for_status()
            return r.json() or {}
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(404, f"mlflow: {exc.response.text[:300]}")
        raise HTTPException(502, f"mlflow error: {exc.response.text[:300]}")
    except Exception as exc:
        raise HTTPException(502, f"mlflow unreachable: {exc}")


async def _mlflow_get_text(path: str) -> str | None:
    """Fetch a raw-text artifact through MLflow's artifact proxy
    (/api/2.0/mlflow-artifacts/artifacts/<run-relative-path>). Returns None when
    absent, and the caller returns an empty schema. Unlike _mlflow_get this
    never raises: a missing signature is a valid state, not an error."""
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"{MLFLOW_URL}{path}")
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.text
    except Exception as exc:                                # noqa: BLE001
        log.warning("mlflow artifact fetch failed (%s): %s", path, exc)
        return None


def _parse_signature_cols(raw: Any) -> list[dict[str, Any]]:
    """MLflow stores signature.inputs/outputs as a JSON string of
    [{type,name,required}]. Normalise to {name,type,required} for the UI."""
    if not raw:
        return []
    try:
        cols = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:                                       # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for c in cols or []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        out.append({"name": c.get("name"),
                    "type": c.get("type"),
                    "required": bool(c.get("required", True))})
    return out


async def _deployments() -> dict[str, dict[str, Any]]:
    """ops_ml.model_deployment rows keyed by model_name. A database without
    the table yields no deployment metadata instead of a 500."""
    try:
        pool = await ops_db.ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT model_name, model_alias, deployment_kind, schedule,
                       enabled, params
                FROM ops_ml.model_deployment;
            """)
        return {r["model_name"]: dict(r) for r in rows}
    except Exception as exc:
        log.warning("model_deployment read failed (schema not migrated?): %s", exc)
        return {}


def _sanitize_dns(s: str, max_len: int = 24) -> str:
    out = re.sub(r"[^a-z0-9-]", "-", (s or "").lower()).strip("-")
    return out[:max_len].strip("-") or "x"


def _k8s():
    """Kube client via the pod ServiceAccount (gateway-rbac.yaml)."""
    from kubernetes import client as k8sc, config as k8sconf
    try:
        k8sconf.load_incluster_config()
    except Exception:
        k8sconf.load_kube_config()
    return k8sc


def _namespace() -> str:
    return os.environ.get("POD_NAMESPACE", "nhdw")


def _runner_env(k8sc) -> list:
    """Environment for a runner Job, from the existing mlflow-secrets and
    postgres-vault-secrets. Both MINIO_* and AWS_* variables are set, for the
    boto3 and MLflow clients."""
    def sec(env_name: str, secret: str, key: str):
        return k8sc.V1EnvVar(
            name=env_name,
            value_from=k8sc.V1EnvVarSource(
                secret_key_ref=k8sc.V1SecretKeySelector(name=secret, key=key)))
    return [
        k8sc.V1EnvVar(name="MLFLOW_URL",           value=MLFLOW_URL),
        k8sc.V1EnvVar(name="MLFLOW_TRACKING_URI",  value=MLFLOW_URL),
        k8sc.V1EnvVar(name="MLFLOW_S3_ENDPOINT_URL", value=settings.MINIO_ENDPOINT),
        k8sc.V1EnvVar(name="MINIO_ENDPOINT",       value=settings.MINIO_ENDPOINT),
        sec("AWS_ACCESS_KEY_ID",     "mlflow-secrets", "S3_ACCESS_KEY"),
        sec("AWS_SECRET_ACCESS_KEY", "mlflow-secrets", "S3_SECRET_KEY"),
        sec("MINIO_ACCESS_KEY",      "mlflow-secrets", "S3_ACCESS_KEY"),
        sec("MINIO_SECRET_KEY",      "mlflow-secrets", "S3_SECRET_KEY"),
        k8sc.V1EnvVar(name="AWS_DEFAULT_REGION", value="et-addis-1"),
        k8sc.V1EnvVar(name="OPS_PG_HOST",
                      value=os.environ.get("OPS_PG_HOST",
                                           "postgres-vault.nhdw.svc.cluster.local")),
        k8sc.V1EnvVar(name="OPS_PG_PORT",
                      value=os.environ.get("OPS_PG_PORT", "5432")),
        sec("VAULT_ADMIN_PASSWORD", "postgres-vault-secrets", "POSTGRES_PASSWORD"),
    ]


def _create_runner_job(*, job_name: str, model: str, mode: str,
                       args: list[str], user: UserCtx,
                       annotations: dict[str, str] | None = None) -> str:
    """Create one ephemeral nhdw/ml-runner Job. Returns the namespace."""
    k8sc = _k8s()
    ns = _namespace()
    labels = {"app": "ml-runner", "tier": "ml",
              "nhdw.io/mode": mode, "nhdw.io/model": model,
              "nhdw.io/created-by": "nhdw-gateway"}
    meta = k8sc.V1ObjectMeta(
        name=job_name, namespace=ns, labels=labels,
        annotations={"nhdw.io/triggered-by": user.username or "",
                     "nhdw.io/triggered-from": "gateway-api",
                     "nhdw.io/model": model,
                     **(annotations or {})})
    container = k8sc.V1Container(
        name="runner", image=ML_RUNNER_IMAGE, image_pull_policy="IfNotPresent",
        args=list(args), env=_runner_env(k8sc))
    job = k8sc.V1Job(
        metadata=meta,
        spec=k8sc.V1JobSpec(
            backoff_limit=0,
            ttl_seconds_after_finished=86400,
            template=k8sc.V1PodTemplateSpec(
                metadata=k8sc.V1ObjectMeta(labels=labels,
                                           annotations=meta.annotations),
                spec=k8sc.V1PodSpec(restart_policy="Never",
                                    containers=[container]))))
    try:
        k8sc.BatchV1Api().create_namespaced_job(ns, job)
    except Exception as exc:
        log.exception("runner job create failed (%s %s)", mode, model)
        raise HTTPException(500, f"kubernetes job create failed: {exc}")
    return ns


# Registry reads
def _model_summary(m: dict[str, Any], dep: dict[str, Any]) -> dict[str, Any]:
    aliases = {a.get("alias"): a.get("version") for a in (m.get("aliases") or [])}
    tags = {t["key"]: t["value"] for t in (m.get("tags") or [])}
    versions = [int(v.get("version") or 0) for v in (m.get("latest_versions") or [])]
    serving_alias = dep.get("model_alias") or "champion"
    return {
        "name":             m.get("name"),
        "latest_version":   str(max(versions)) if versions else None,
        "champion_version": aliases.get(serving_alias) or aliases.get("champion"),
        "aliases":          aliases,
        "deployment_kind":  dep.get("deployment_kind")
                            or tags.get("deployment_kind", "batch"),
        "schedule":         dep.get("schedule"),
        "enabled":          dep.get("enabled"),
        "last_updated":     m.get("last_updated_timestamp"),
    }


@router.get("")
async def list_models(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Every MLflow-registered model with its aliases (@champion, ...) joined
    to its ops_ml.model_deployment row (schedule, enabled, kind)."""
    data = await _mlflow_get("/api/2.0/mlflow/registered-models/search",
                             {"max_results": "200"})
    deployments = await _deployments()
    out = [_model_summary(m, deployments.get(m.get("name"), {}))
           for m in (data.get("registered_models") or [])]
    await ops_db.log_gateway_query(
        user=user, method="GET", path="/api/ml",
        mart_scope=["mlflow.registered_models"], filters={},
        row_count=len(out), byte_count=None, latency_ms=0,
        http_status=200, policy_decision="allow")
    return {"models": out, "count": len(out)}


# Declared before /{model} so `jobs` is never parsed as a model name.
@router.get("/jobs/{job_id}")
async def job_status(job_id: str,
                     user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Poll a gateway-created runner Job: status and result URI (the
    predict-adhoc output CSV gets a presigned download once the Job succeeds)."""
    _check_model_name(job_id)          # same DNS-label charset rules
    k8sc = _k8s()
    from kubernetes.client.rest import ApiException
    try:
        job = k8sc.BatchV1Api().read_namespaced_job(job_id, _namespace())
    except ApiException as exc:
        if exc.status == 404:
            raise HTTPException(404, f"job {job_id!r} not found")
        raise HTTPException(502, f"k8s api error: {exc}")
    except Exception as exc:
        raise HTTPException(502, f"k8s api unreachable: {exc}")

    labels = job.metadata.labels or {}
    ann = job.metadata.annotations or {}
    if labels.get("nhdw.io/created-by") != "nhdw-gateway":
        raise HTTPException(404, f"job {job_id!r} not found")
    model = labels.get("nhdw.io/model", "")

    decision = await opa.decide(user, action="read", resource="PRG", attributes={"data_class": "DC.CAT", "granularity": "aggregate", "sensitivity": "default"})
    if not decision.allow:
        raise HTTPException(403, decision.reason or "denied by policy")

    st = job.status
    if st.succeeded:
        phase = "succeeded"
    elif st.failed:
        phase = "failed"
    elif st.active:
        phase = "running"
    else:
        phase = "pending"
    error = None
    for cond in (st.conditions or []):
        if cond.type == "Failed" and cond.status == "True":
            error = cond.message or cond.reason

    result_uri = ann.get("nhdw.io/output-uri")
    result_url = None
    if phase == "succeeded" and result_uri and result_uri.startswith("minio://"):
        bucket, _, key = result_uri.removeprefix("minio://").partition("/")
        try:
            result_url = await minio_client.presign_get(
                key=key, bucket=bucket, filename=f"{job_id}.csv")
        except Exception as exc:                       # presign is best-effort
            log.warning("presign failed for %s: %s", result_uri, exc)

    return {
        "job_id":          job_id,
        "model":           model,
        "mode":            labels.get("nhdw.io/mode"),
        "status":          phase,
        "active":          st.active or 0,
        "succeeded":       st.succeeded or 0,
        "failed":          st.failed or 0,
        "start_time":      str(st.start_time) if st.start_time else None,
        "completion_time": str(st.completion_time) if st.completion_time else None,
        "error":           error,
        "result_uri":      result_uri,
        "result_url":      result_url,
    }


async def _monitoring_gate(user: UserCtx) -> None:
    """Model-monitoring reads (fleet health, per-model drift and performance)
    are admin-console only: R-OPS-* and R-SUPER via the view_ops_console
    capability (those roles hold no data-plane mart grants by design).
    Workshop personas (R-CONS-SCI, R-CONS-RES) cannot reach this monitoring
    data; the workshop Models page is usage-only (catalogue metadata,
    input/output schema, own ad-hoc predict). Fails closed."""
    from .perm_admin import _require_capability
    await _require_capability(user, "view_ops_console")


# Declared before /{model} so `monitoring` is never parsed as a model name.
@router.get("/monitoring/fleet")
async def monitoring_fleet(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Fleet-wide model health for the admin Model Monitoring page. Per deployed
    model: latest run, drift summary (from its latest scored run), latest
    performance metric, and a staleness flag (enabled batch schedule with no
    successful run in 7 days)."""
    await _monitoring_gate(user)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            WITH latest_run AS (
                SELECT DISTINCT ON (model_name)
                       model_name, id, status, started_at, finished_at
                FROM ops_ml.ml_run
                ORDER BY model_name, started_at DESC
            ), drift AS (
                SELECT d.model_name,
                       COUNT(*)                          AS features_checked,
                       COUNT(*) FILTER (WHERE d.drifted) AS drifted_features,
                       MAX(d.psi)                        AS max_psi,
                       MAX(d.computed_at)                AS drift_computed_at
                FROM ops_ml.model_drift d
                JOIN (SELECT model_name, MAX(computed_at) AS latest
                      FROM ops_ml.model_drift GROUP BY model_name) l
                  ON l.model_name = d.model_name AND l.latest = d.computed_at
                GROUP BY d.model_name
            ), perf AS (
                SELECT DISTINCT ON (model_name)
                       model_name, metric, value, computed_at, sample_size
                FROM ops_ml.model_performance
                ORDER BY model_name, computed_at DESC
            )
            SELECT dep.model_name,
                   dep.model_alias                        AS alias,
                   dep.deployment_kind,
                   dep.schedule,
                   dep.enabled,
                   lr.started_at                          AS latest_run_at,
                   lr.status                              AS latest_run_status,
                   COALESCE(dr.drifted_features, 0)::int  AS drifted_features,
                   COALESCE(dr.features_checked, 0)::int  AS features_checked,
                   -- numeric -> float8: asyncpg serializes numeric as a JSON
                   -- STRING (Decimal); the SPAs are promised numbers.
                   dr.max_psi::float8                     AS max_psi,
                   dr.drift_computed_at,
                   pf.metric                              AS latest_metric,
                   pf.value::float8                       AS latest_metric_value,
                   pf.sample_size,
                   pf.computed_at                         AS perf_computed_at,
                   (dep.enabled AND dep.deployment_kind = 'batch'
                    AND (lr.started_at IS NULL
                         OR lr.started_at < now() - interval '7 days')) AS stale
            FROM ops_ml.model_deployment dep
            LEFT JOIN latest_run lr ON lr.model_name = dep.model_name
            LEFT JOIN drift dr      ON dr.model_name = dep.model_name
            LEFT JOIN perf pf       ON pf.model_name = dep.model_name
            ORDER BY dep.model_name;
        """)
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.get("/{model}/drift")
async def model_drift(model: str,
                      limit: int = Query(default=200, ge=1, le=2000),
                      user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Per-feature drift rows (PSI/KS against the training profile), newest
    first, for the admin model monitoring views."""
    _check_model_name(model)
    await _monitoring_gate(user)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT run_id, feature, kind,
                   psi::float8 AS psi, ks::float8 AS ks,
                   psi_threshold::float8 AS psi_threshold, drifted,
                   note, computed_at
            FROM ops_ml.model_drift
            WHERE model_name = $1
            ORDER BY computed_at DESC, feature
            LIMIT $2;
        """, model, limit)
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.get("/{model}/performance")
async def model_performance(model: str,
                            metric: str | None = Query(default=None),
                            limit: int = Query(default=200, ge=1, le=2000),
                            user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Accuracy-vs-actuals windows scored after each batch run, newest first."""
    _check_model_name(model)
    await _monitoring_gate(user)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT model_version, window_start, window_end, metric,
                   value::float8 AS value,
                   sample_size, run_id, computed_at
            FROM ops_ml.model_performance
            WHERE model_name = $1 AND ($2::text IS NULL OR metric = $2)
            ORDER BY computed_at DESC
            LIMIT $3;
        """, model, metric, limit)
    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.get("/{model}")
async def model_detail(model: str,
                       user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """One model: registry summary (aliases), deployment row, last run."""
    _check_model_name(model)
    decision = await opa.decide(user, action="read", resource="PRG", attributes={"data_class": "DC.CAT", "granularity": "aggregate", "sensitivity": "default"})
    if not decision.allow:
        raise HTTPException(403, decision.reason or "denied by policy")
    data = await _mlflow_get("/api/2.0/mlflow/registered-models/get",
                             {"name": model})
    reg = data.get("registered_model") or {}
    deployments = await _deployments()
    dep = deployments.get(model, {})
    last_run = None
    try:
        pool = await ops_db.ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, model_version, deployment_kind, started_at,
                       finished_at, status, row_count, latency_ms,
                       trigger_kind, error_msg
                FROM ops_ml.ml_run WHERE model_name = $1
                ORDER BY started_at DESC LIMIT 1;
            """, model)
        last_run = dict(row) if row else None
    except Exception as exc:
        log.warning("ml_run read failed for %s: %s", model, exc)
    # Package artifacts: the ml-packages/<name>/current/ copy is the
    # authoritative source for the input signature (feature_spec.yaml) and the
    # model card. When absent (e.g. a model imported without a package) the
    # fields are null and the UI shows placeholders.
    signature = None
    model_card = None
    try:
        import yaml as _yaml
        raw = await minio_client.read_object(
            key=f"ml-packages/{model}/current/feature_spec.yaml",
            bucket=ARTIFACTS_BUCKET)
        if raw:
            spec = _yaml.safe_load(raw) or {}
            signature = (spec.get("input") or {}).get("signature")
        card = await minio_client.read_object(
            key=f"ml-packages/{model}/current/model_card.md",
            bucket=ARTIFACTS_BUCKET)
        if card:
            model_card = card.decode("utf-8", "replace")
    except Exception as exc:
        log.warning("package artifact read failed for %s: %s", model, exc)
    return {
        "model":       model,
        **_model_summary(reg, dep),
        "description": reg.get("description"),
        "deployment":  dep or None,
        "last_run":    last_run,
        "signature":   signature,
        "model_card":  model_card,
    }


@router.get("/{model}/schema")
async def model_schema(model: str,
                       user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """The model's input and output schema, in the catalogue-metadata class
    (PRG/DC.CAT) that the workshop personas read for the registry detail.

    Derived from the champion (else latest) version's MLflow model signature
    (the MLmodel artifact, fetched through MLflow's artifact proxy) and its
    input_example, falling back to the package feature_spec.yaml (inputs only)
    when no signature is published. Exposes only the shape a caller must
    supply and the shape returned, never stored prediction data. Returns an
    empty schema (published: false) when neither source has a signature; the
    UI then shows 'schema not published'."""
    _check_model_name(model)
    decision = await opa.decide(user, action="read", resource="PRG", attributes={"data_class": "DC.CAT", "granularity": "aggregate", "sensitivity": "default"})
    if not decision.allow:
        raise HTTPException(403, decision.reason or "denied by policy")

    # Version to describe: @champion is the serving contract; fall back to the
    # newest registered version so an unpromoted model still shows its shape.
    reg = (await _mlflow_get("/api/2.0/mlflow/registered-models/get",
                             {"name": model})).get("registered_model") or {}
    aliases = {a.get("alias"): a.get("version") for a in (reg.get("aliases") or [])}
    latest = [int(v.get("version") or 0) for v in (reg.get("latest_versions") or [])]
    version = aliases.get("champion") or (str(max(latest)) if latest else None)

    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    input_example: dict[str, Any] = {}
    published = False

    # 1. MLflow model signature (authoritative: has inputs and outputs).
    source = None
    if version:
        try:
            mv = (await _mlflow_get("/api/2.0/mlflow/model-versions/get",
                                    {"name": model, "version": str(version)})
                  ).get("model_version") or {}
            source = mv.get("source")
        except HTTPException:
            source = None
    if source and ":/" in source:
        # e.g. mlflow-artifacts:/3/<run>/artifacts/model to a run-relative path
        rel = source.split(":/", 1)[1].strip("/")
        mlmodel = await _mlflow_get_text(
            f"/api/2.0/mlflow-artifacts/artifacts/{rel}/MLmodel")
        if mlmodel:
            try:
                import yaml as _yaml
                sig = ((_yaml.safe_load(mlmodel) or {}).get("signature") or {})
                inputs = _parse_signature_cols(sig.get("inputs"))
                outputs = _parse_signature_cols(sig.get("outputs"))
                published = bool(inputs or outputs)
            except Exception as exc:                        # noqa: BLE001
                log.warning("MLmodel signature parse failed for %s: %s", model, exc)
        # input_example.json is pandas split-orient {columns, data}; first row.
        ex = await _mlflow_get_text(
            f"/api/2.0/mlflow-artifacts/artifacts/{rel}/input_example.json")
        if ex:
            try:
                doc = json.loads(ex)
                cols = doc.get("columns") or []
                data = doc.get("data") or []
                if cols and data:
                    input_example = {c: data[0][i] for i, c in enumerate(cols)
                                     if i < len(data[0])}
            except Exception as exc:                        # noqa: BLE001
                log.warning("input_example parse failed for %s: %s", model, exc)

    # 2. Fallback: the package feature_spec.yaml carries the input signature
    #    ([{name,dtype}]) even for models with no logged MLflow signature.
    if not inputs:
        try:
            import yaml as _yaml
            raw = await minio_client.read_object(
                key=f"ml-packages/{model}/current/feature_spec.yaml",
                bucket=ARTIFACTS_BUCKET)
            if raw:
                spec = _yaml.safe_load(raw) or {}
                for c in ((spec.get("input") or {}).get("signature") or []):
                    if isinstance(c, dict) and c.get("name"):
                        inputs.append({"name": c.get("name"),
                                       "type": c.get("dtype"),
                                       "required": True})
                if inputs:
                    published = True
        except Exception as exc:                            # noqa: BLE001
            log.warning("feature_spec fallback read failed for %s: %s", model, exc)

    # Attach the example value per input column (placeholder in the run form).
    for col in inputs:
        if col["name"] in input_example:
            col["example"] = input_example[col["name"]]

    return {
        "model":         model,
        "version":       str(version) if version is not None else None,
        "published":     published,
        "inputs":        inputs,
        "outputs":       outputs,
        "input_example": input_example,
    }


@router.get("/{model}/predictions")
async def predictions(model: str,
                      user: UserCtx = Depends(get_user),
                      latest_run: bool = Query(True),
                      disease: str | None = Query(default=None),
                      district: str | None = Query(default=None),
                      year: int | None = Query(default=None),
                      week: int | None = Query(default=None),
                      limit: int = Query(default=500, ge=1, le=5000)) -> dict[str, Any]:
    """Batch predictions for {model} in the flat forecast schema, filtered to
    the most recent successful run (unless latest_run=false) and the requested
    dimensions. `district` is the geography id for the disease models and the
    commodity name for stockout_forecast."""
    _check_model_name(model)
    decision = await opa.decide(user, action="read", resource="PRG", attributes={"data_class": "DC.PRO", "granularity": "aggregate", "sensitivity": "default"})
    if not decision.allow:
        raise HTTPException(403, decision.reason or "denied by policy")
    t0 = time.perf_counter()
    pool = await ops_db.ensure_pool()
    where = ["model_name = $1"]
    args: list[Any] = [model]
    if latest_run:
        where.append(
            "run_id = (SELECT id FROM ops_ml.ml_run "
            "          WHERE model_name = $1 AND status='success' "
            "          ORDER BY finished_at DESC NULLS LAST LIMIT 1)")
    if disease:
        args.append(disease);  where.append(f"disease = ${len(args)}")
    if district:
        args.append(district); where.append(f"district = ${len(args)}")
    if year is not None:
        args.append(year);     where.append(f"year = ${len(args)}")
    if week is not None:
        args.append(week);     where.append(f"week = ${len(args)}")
    args.append(limit)
    sql = (f"SELECT {_PRED_COLS} FROM ops_ml.ml_predictions "
           f"WHERE {' AND '.join(where)} "
           f"ORDER BY year DESC, week DESC, alarm_prob DESC NULLS LAST "
           f"LIMIT ${len(args)}")
    async with pool.acquire() as conn:
        rows = [dict(r) for r in await conn.fetch(sql, *args)]
    await ops_db.log_gateway_query(
        user=user, method="GET", path=f"/api/ml/{model}/predictions",
        mart_scope=[f"ml:{model}"], filters={"disease": disease,
                                             "district": district,
                                             "year": year, "week": week},
        row_count=len(rows), byte_count=None,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        http_status=200, policy_decision="allow")
    return {"model": model, "rows": rows, "row_count": len(rows)}


# Runner Jobs: ad-hoc predict and retrain
def _rows_to_csv(rows: list[dict[str, Any]]) -> bytes:
    """Serialize JSON records to CSV (header from the union of keys,
    first-row order preserved)."""
    fieldnames: list[str] = []
    for row in rows:
        for k in row:
            if k not in fieldnames:
                fieldnames.append(k)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, restval="")
    w.writeheader()
    for row in rows:
        w.writerow(row)
    return buf.getvalue().encode()


@router.post("/{model}/predict")
async def predict(model: str,
                  user: UserCtx = Depends(get_user),
                  payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Ad-hoc inference: stage the caller's input in the object store, start a
    runner Job in predict-adhoc mode, and return the job id for polling.

    Body: {"rows": [{...}, ...]}  or  {"csv": "<raw csv text>"}.
    The runner validates the CSV against the model signature, resolves
    models:/<model>@champion, writes the output CSV next to the input, and
    records the ops_ml.ml_run row itself. Poll GET /api/ml/jobs/{job_id}."""
    _check_model_name(model)
    decision = await opa.decide(user, action="read", resource="PRG", attributes={"data_class": "DC.CAT", "granularity": "aggregate", "sensitivity": "default"})
    if not decision.allow:
        raise HTTPException(403, decision.reason or "denied by policy")

    rows = payload.get("rows")
    csv_text = payload.get("csv")
    if isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows):
        body = _rows_to_csv(rows)
    elif isinstance(csv_text, str) and csv_text.strip():
        body = csv_text.encode()
    else:
        raise HTTPException(400,
            'body must be {"rows": [{...}, ...]} or {"csv": "<csv text>"}')

    job_id = f"ml-adhoc-{_sanitize_dns(model, 20)}-{uuid4().hex[:8]}"
    input_key  = f"{ADHOC_PREFIX}/{job_id}/input.csv"
    output_key = f"{ADHOC_PREFIX}/{job_id}/output.csv"
    await minio_client.write_export(key=input_key, body=body,
                                    content_type="text/csv",
                                    bucket=ARTIFACTS_BUCKET)
    input_uri  = f"minio://{ARTIFACTS_BUCKET}/{input_key}"
    output_uri = f"minio://{ARTIFACTS_BUCKET}/{output_key}"

    ns = _create_runner_job(
        job_name=job_id, model=model, mode="predict-adhoc",
        args=["predict-adhoc", model, "--input", input_uri,
              "--output", output_uri],
        user=user,
        annotations={"nhdw.io/input-uri": input_uri,
                     "nhdw.io/output-uri": output_uri})

    await ops_db.log_gateway_query(
        user=user, method="POST", path=f"/api/ml/{model}/predict",
        mart_scope=[f"ml:{model}"], filters={"job_id": job_id,
                                             "input_bytes": len(body)},
        row_count=None, byte_count=len(body), latency_ms=0,
        http_status=202, policy_decision="allow")
    return {"model": model, "job_id": job_id, "namespace": ns,
            "status": "submitted", "input_uri": input_uri,
            "output_uri": output_uri, "poll": f"/api/ml/jobs/{job_id}"}


@router.post("/{model}/retrain")
async def retrain(model: str, user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Trigger a one-off retrain: start a runner Job in train mode against
    the model's package (ops_ml.model_deployment.params.package_ref when
    set, else the conventional ml-packages/<model>/current/ location).
    The Job re-fits, registers a new MLflow version and logs the reference
    feature profile; promotion to @champion stays a separate, explicit
    POST /api/ml/{model}/promote. Poll GET /api/ml/jobs/{job_name}."""
    _check_model_name(model)
    decision = _manage_gate(user)
    if not decision.allow:
        raise HTTPException(403, decision.reason or "denied by policy")

    dep = (await _deployments()).get(model, {})
    params = dep.get("params") or {}
    if isinstance(params, str):                       # jsonb decoded upstream,
        try:                                          # but stay defensive
            params = json.loads(params)
        except Exception:
            params = {}
    package_ref = params.get("package_ref") or f"{ML_PACKAGES_BASE}/{model}/current/"

    actor = _sanitize_dns(user.username or "anon", 16)
    job_name = f"ml-train-{_sanitize_dns(model, 20)}-{actor}-{uuid4().hex[:6]}"[:63]
    ns = _create_runner_job(
        job_name=job_name, model=model, mode="train",
        args=["train", package_ref],
        user=user,
        annotations={"nhdw.io/package-ref": package_ref})

    await ops_db.log_gateway_query(
        user=user, method="POST", path=f"/api/ml/{model}/retrain",
        mart_scope=[f"ml:{model}"], filters={"package_ref": package_ref},
        row_count=1, byte_count=None, latency_ms=0,
        http_status=202, policy_decision="allow")
    return {"model": model, "job_name": job_name, "namespace": ns,
            "package_ref": package_ref, "poll": f"/api/ml/jobs/{job_name}"}


# Runs and registry metadata
@router.get("/{model}/runs")
async def runs(model: str, user: UserCtx = Depends(get_user),
               limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    """Recent runs of a model (train, batch and ad-hoc), for the admin ML pages
    and the internal-bi predictions page."""
    _check_model_name(model)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, model_version, deployment_kind, started_at, finished_at,
                   status, row_count, latency_ms, trigger_kind, error_msg
            FROM ops_ml.ml_run
            WHERE model_name = $1
            ORDER BY started_at DESC
            LIMIT $2;
        """, model, limit)
    return {"model": model, "runs": [dict(r) for r in rows]}


@router.get("/{model}/registered")
async def registered(model: str, user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Raw MLflow metadata for one model: tags, description, aliases."""
    _check_model_name(model)
    return await _mlflow_get("/api/2.0/mlflow/registered-models/get",
                             {"name": model})


@router.get("/{model}/versions")
async def versions(model: str, user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """All MLflow versions for `model`, newest first, each with its aliases.
    Used by the admin retraining page's version table and promotion picker."""
    _check_model_name(model)
    decision = await opa.decide(user, action="read", resource="PRG", attributes={"data_class": "DC.CAT", "granularity": "aggregate", "sensitivity": "default"})
    if not decision.allow:
        raise HTTPException(403, decision.reason or "denied by policy")
    data = await _mlflow_get("/api/2.0/mlflow/model-versions/search",
                             {"filter": f"name='{model}'", "max_results": "200"})
    # model-versions/search omits aliases in MLflow 2.18; derive them from the
    # registered-model's alias map so version rows carry their badges.
    alias_by_version: dict[str, list[str]] = {}
    try:
        reg = (await _mlflow_get("/api/2.0/mlflow/registered-models/get",
                                 {"name": model})).get("registered_model") or {}
        for a in (reg.get("aliases") or []):
            alias_by_version.setdefault(str(a.get("version")), []).append(a.get("alias"))
    except HTTPException:
        pass
    out = []
    for mv in (data.get("model_versions") or []):
        out.append({
            "version":                mv.get("version"),
            "aliases":                (mv.get("aliases")
                                       or alias_by_version.get(str(mv.get("version")), [])),
            "creation_timestamp":     mv.get("creation_timestamp"),
            "last_updated_timestamp": mv.get("last_updated_timestamp"),
            "run_id":                 mv.get("run_id"),
            "source":                 mv.get("source"),
            "description":            mv.get("description"),
        })
    out.sort(key=lambda v: int(v["version"] or 0), reverse=True)
    return {"model": model, "versions": out}


@router.post("/{model}/promote")
async def promote(model: str, version: str = Query(...),
                  alias: str = Query("champion"),
                  user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Point an MLflow alias (@champion or @challenger) at a model version.
    Aliases are exclusive per model, so setting one moves it. Audit-logged."""
    _check_model_name(model)
    decision = _manage_gate(user)
    if not decision.allow:
        raise HTTPException(403, decision.reason or "denied by policy")
    if alias not in VALID_ALIASES:
        raise HTTPException(400,
            f"unknown alias {alias!r}; valid: {sorted(VALID_ALIASES)}")
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{MLFLOW_URL}/api/2.0/mlflow/registered-models/alias",
                json={"name": model, "alias": alias, "version": version})
            r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502,
            f"mlflow alias set failed: {exc.response.text[:300]}")
    except Exception as exc:
        raise HTTPException(502, f"mlflow unreachable: {exc}")
    await ops_db.log_gateway_query(
        user=user, method="POST", path=f"/api/ml/{model}/promote",
        mart_scope=[f"ml:{model}"], filters={"version": version, "alias": alias},
        row_count=1, byte_count=None, latency_ms=0,
        http_status=200, policy_decision="allow")
    return {"model": model, "version": version, "alias": alias}
