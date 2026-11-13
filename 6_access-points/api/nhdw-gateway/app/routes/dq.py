"""/api/dq: data-quality and lineage endpoints for the Admin dashboard.

Reads dbt artifacts (manifest.json, run_results.json, catalog.json) from the
object store at nhdw-artifacts/dbt/<project>/, where the dbt-marts entrypoint
uploads them after each build. A local bind-mounted target directory is the
fallback for docker-compose setups.

  GET  /api/dq/summary        dbt build health: pass/fail counts and runtime
  GET  /api/dq/tests          per-test outcome, duration, message, ack state
  GET  /api/dq/models         per-model latest status with its dbt tests
  GET  /api/dq/lineage        DAG nodes and edges with tier and test status
                              (the admin page shows a summary; the full graph
                              is in dbt Docs)
  GET  /api/dq/sources        per-source completeness, validity, timeliness and
                              report volume from bi_quality.mart_data_quality
  GET  /api/dq/ingestion      per-source freshness from the raw-lake
                              _manifest.json sidecars: last ingest, records in
                              the last cycle, lag against SLA, changed last run
  GET  /api/dq/dimensions     five-dimension scorecard (completeness, validity,
                              timeliness, consistency, uniqueness)
  GET  /api/dq/reconciliation cross-source agreement from the business-vault
                              same-as links (facility and commodity master)
  GET  /api/dq/compliance     k-anonymity compliance for bi_public marts
  POST /api/dq/ack            acknowledge a failing or warning dbt test;
                              writes ops_governance.dq_flag and dq_ack
  POST /api/dq/detect         run the incident detector (service token or
                              admin); upserts ops_governance.dq_flag and
                              appends dq_flag_event history
  GET  /api/dq/flags          the incident queue (paged; ?status=open|all)
  GET  /api/dq/kpi            governance KPIs over the incident history

Every route requires the ops-console capability; /detect also accepts a
service token for the Airflow DAG.

dbt artifacts are cached in-process for ARTIFACT_CACHE_TTL_S (default 30 s);
the raw-lake manifest scan has its own cache so the freshness widget does not
list the bucket on every request.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import (APIRouter, Depends, Header, HTTPException, Query,
                     Request)
from pydantic import BaseModel, Field

from ..auth import UserCtx, get_user
from ..settings import settings
from .. import opa, ops_db, trino_client
# Ops-console routes gate on the RBAC capability, not opa.decide: no Rego rule
# matches admin.* actions, so opa.decide would deny them under OPA_ENFORCE.
from .perm_admin import _require_capability
# The detector reuses the classification gap scan so the on-demand list and the
# raised flag cannot disagree.
from . import classification

log = logging.getLogger("gateway.dq")
router = APIRouter(prefix="/api/dq", tags=["dq"])

DBT_TARGET_DIR = Path(os.environ.get("DBT_TARGET_DIR", "/dbt-target/marts"))
DBT_PROJECT    = os.environ.get("DBT_PROJECT_NAME", "marts")
ARTIFACTS_BUCKET = os.environ.get("DBT_ARTIFACTS_BUCKET", "nhdw-artifacts")
RAW_BUCKET     = os.environ.get("DBT_RAW_BUCKET", "nhdw-raw")
ARTIFACT_CACHE_TTL_S = int(os.environ.get("DQ_CACHE_TTL_S", "30"))

# in-process artifact cache: {filename: (loaded_at_ts, content_dict)}
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
# Separate cache for the heavier raw-lake ingestion scan.
_ingest_cache: tuple[float, list[dict[str, Any]]] | None = None

# Directly ingested sources: raw-lake prefix (nhdw-raw/<prefix>/...) and the
# default freshness SLA in hours that ingestion lag is graded against. Each
# source has its own ingestor, including Climate (nhdw-raw/climate/ feeds
# hub_climate_week).
SOURCES: list[dict[str, Any]] = [
    {"key": "DHIS2",    "raw_prefix": "dhis2",    "sla_hours": 6},
    {"key": "EthioEMR", "raw_prefix": "ethioemr", "sla_hours": 1},
    {"key": "HCMIS",    "raw_prefix": "hcmiswdm", "sla_hours": 4},
    {"key": "mBrana",   "raw_prefix": "mbrana",   "sla_hours": 2},
    {"key": "eLMIS-hist", "raw_prefix": "elmis_hist", "sla_hours": 24 * 30},   # immutable archive lane: re-run on demand
    {"key": "MFR",      "raw_prefix": "mfr",      "sla_hours": 48},
    {"key": "Climate",  "raw_prefix": "climate",  "sla_hours": 168},
]
_SOURCE_BY_PREFIX = {s["raw_prefix"]: s for s in SOURCES if s["raw_prefix"]}

# mart_data_quality labels its climate row 'EWARS/Climate' (an accepted_values
# dbt test pins that string). Map it to the canonical source key so each
# quality row joins the same source as its ingestion-freshness row.
_MART_SOURCE_ALIAS = {"EWARS/Climate": "Climate"}


def _load_artifact(name: str) -> dict[str, Any]:
    """Read a dbt artifact (manifest.json, run_results.json, ...).

    Prefers the object store (`nhdw-artifacts/dbt/<project>/<name>`) so the
    gateway sees the latest dbt-marts run, then the bind-mounted path. Cached
    in-process for ARTIFACT_CACHE_TTL_S."""
    now = time.time()
    hit = _cache.get(name)
    if hit and now - hit[0] < ARTIFACT_CACHE_TTL_S:
        return hit[1]

    # Object store first (SeaweedFS, S3 API).
    try:
        import boto3
        s3 = boto3.client(
            "s3",
            endpoint_url=settings.MINIO_ENDPOINT,
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            region_name="us-east-1",
        )
        key = f"dbt/{DBT_PROJECT}/{name}"
        obj = s3.get_object(Bucket=ARTIFACTS_BUCKET, Key=key)
        data = json.load(io.BytesIO(obj["Body"].read()))
        _cache[name] = (now, data)
        return data
    except Exception as exc:                                    # noqa: BLE001
        log.debug("MinIO artifact fetch failed for %s: %s; trying local mount", name, exc)

    # Fall back to bind-mount.
    f = DBT_TARGET_DIR / name
    if f.exists():
        with f.open() as fh:
            data = json.load(fh)
        _cache[name] = (now, data)
        return data
    return {}


def _load_run_results() -> dict[str, Any]:
    return _load_artifact("run_results.json")


def _load_manifest() -> dict[str, Any]:
    return _load_artifact("manifest.json")


async def _gate(user: UserCtx, action: str = "admin.dq.read",
                resource: str = "dbt") -> None:
    """Shared gate: every /api/dq/* route requires the view_ops_console
    capability. `action` and `resource` are accepted for call-site
    compatibility and unused, since admin.* actions match no Rego rule."""
    await _require_capability(user, "view_ops_console")


def _s3():
    """boto3 S3 client for the object store (same as in _load_artifact)."""
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=settings.MINIO_ENDPOINT,
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY,
        region_name="us-east-1",
    )


_DAY_RE = re.compile(r"/year=(\d{4})/month=(\d{2})/day=(\d{2})/")


def _scan_source_manifests(s3, prefix: str, *, cap: int = 160
                           ) -> list[dict[str, Any]]:
    """List and read the `_manifest.json` sidecars under nhdw-raw/<prefix>/.

    Returns one dict per manifest with the day parsed from its key. Capped
    at `cap` newest manifests so a busy source cannot stall the request."""
    out: list[dict[str, Any]] = []
    paginator = s3.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=RAW_BUCKET, Prefix=f"{prefix}/"):
        for obj in page.get("Contents") or []:
            k = obj["Key"]
            if k.endswith("_manifest.json"):
                keys.append(k)
    # Newest partitions first (keys sort lexically by year/month/day).
    keys.sort(reverse=True)
    for k in keys[:cap]:
        m = _DAY_RE.search(k)
        day = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None
        try:
            body = s3.get_object(Bucket=RAW_BUCKET, Key=k)["Body"].read()
            doc = json.loads(body)
        except Exception:                                       # noqa: BLE001
            continue
        out.append({
            "day":                   day,
            "object":                doc.get("object"),
            "row_count":             int(doc.get("row_count") or 0),
            "completed_at":          doc.get("completed_at"),
            "watermark_used":        doc.get("watermark_used"),
            "watermark_advanced_to": doc.get("watermark_advanced_to"),
            "run_id":                doc.get("run_id"),
        })
    return out


def _rollup_source(key: str, manifests: list[dict[str, Any]], sla_hours: int
                   ) -> dict[str, Any]:
    """Aggregate a source's manifests into one freshness/status row."""
    if not manifests:
        return {"source": key, "ingested": False, "last_ingest": None,
                "records_last_cycle": None, "lag_hours": None,
                "sla_hours": sla_hours, "status": "unknown",
                "changed_last_run": None, "run_id": None, "trend": []}

    # Latest ingest = max completed_at.
    def _ts(v: str | None) -> float:
        if not v:
            return 0.0
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except Exception:                                       # noqa: BLE001
            return 0.0

    latest = max(manifests, key=lambda m: _ts(m.get("completed_at")))
    last_ts = _ts(latest.get("completed_at"))
    lag_hours = round((time.time() - last_ts) / 3600.0, 1) if last_ts else None

    # Records in the most recent day partition; per-day trend, oldest first.
    by_day: dict[str, int] = {}
    changed_by_day: dict[str, bool] = {}
    for m in manifests:
        d = m.get("day")
        if not d:
            continue
        by_day[d] = by_day.get(d, 0) + (m.get("row_count") or 0)
        # "changed" = the ingestor advanced its watermark or wrote rows.
        adv = bool(m.get("watermark_advanced_to")
                   and m.get("watermark_advanced_to") != m.get("watermark_used"))
        changed_by_day[d] = changed_by_day.get(d, False) or adv or bool(m.get("row_count"))
    days_sorted = sorted(by_day)
    trend = [{"day": d, "rows": by_day[d]} for d in days_sorted][-12:]
    latest_day = days_sorted[-1] if days_sorted else None
    records_last_cycle = by_day.get(latest_day) if latest_day else None
    changed_last_run = changed_by_day.get(latest_day) if latest_day else None

    # A source is "down" only after at least 3 days of silence; a few hours
    # past SLA is "stale" (batch cadences leave sources idle overnight).
    # Sources with long SLAs (MFR, Climate) use 2x SLA as the down threshold.
    down_after = max(72.0, sla_hours * 2)
    if lag_hours is None:
        status = "unknown"
    elif lag_hours <= sla_hours:
        status = "live"
    elif lag_hours <= down_after:
        status = "stale"
    else:
        status = "down"

    return {"source": key, "ingested": True,
            "last_ingest": latest.get("completed_at"),
            "records_last_cycle": records_last_cycle,
            "lag_hours": lag_hours, "sla_hours": sla_hours,
            "status": status, "changed_last_run": changed_last_run,
            "run_id": latest.get("run_id"), "trend": trend}


@router.get("/summary")
async def summary(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    await _gate(user)
    rr = _load_run_results()
    results = rr.get("results") or []
    # dbt uses `success` for model runs, `pass`/`fail`/`warn`/`skip`/`error`
    # for tests. Bucket both into the same shape.
    pass_n = sum(1 for r in results
                  if r.get("status") in ("pass", "success"))
    fail_n = sum(1 for r in results if r.get("status") in ("fail", "error"))
    warn_n = sum(1 for r in results if r.get("status") == "warn")
    skip_n = sum(1 for r in results if r.get("status") == "skip")
    total_runtime = sum(float(r.get("execution_time") or 0) for r in results)
    _, _bands = await _thresholds()
    return {
        "bands":      _bands,
        "total":      len(results),
        "pass":       pass_n,
        "fail":       fail_n,
        "warn":       warn_n,
        "skip":       skip_n,
        "runtime_s":  round(total_runtime, 2),
        "generated_at": rr.get("generated_at") or rr.get("metadata", {}).get(
            "generated_at"),
    }


@router.get("/tests")
async def tests(user: UserCtx = Depends(get_user),
                status: str | None = Query(default=None,
                                            description="filter: pass|fail|warn|skip|error"),
                limit:  int = Query(default=200, ge=1, le=2000)
                ) -> list[dict[str, Any]]:
    await _gate(user)
    rr = _load_run_results()
    rows: list[dict[str, Any]] = []
    for r in rr.get("results") or []:
        if status and r.get("status") != status:
            continue
        rows.append({
            "unique_id":    r.get("unique_id"),
            "status":       r.get("status"),
            "execution_time": r.get("execution_time"),
            "failures":     r.get("failures"),
            "message":      r.get("message"),
            "compiled_at":  r.get("timing", [{}])[-1].get("started_at")
                              if r.get("timing") else None,
            "acked":        False,
            "acked_by":     None,
            "acked_at":     None,
        })
        if len(rows) >= limit:
            break

    # Annotate operator acknowledgements (ops_governance.dq_flag + dq_ack).
    acked = await _acked_dbt_tests({row["unique_id"] for row in rows})
    for row in rows:
        info = acked.get(row["unique_id"])
        if info:
            row["acked"]    = True
            row["acked_by"] = info.get("last_by")
            row["acked_at"] = info.get("last_at")
    return rows


@router.get("/models")
async def models(user: UserCtx = Depends(get_user)) -> list[dict[str, Any]]:
    """Per-model rollup, pairing each model with the dbt tests that reference
    it (from manifest.json `depends_on.nodes`)."""
    await _gate(user)
    rr = _load_run_results()
    manifest = _load_manifest()
    nodes = manifest.get("nodes") or {}
    results_by_uid = {r["unique_id"]: r
                      for r in (rr.get("results") or []) if "unique_id" in r}

    out: list[dict[str, Any]] = []
    for uid, node in nodes.items():
        if not uid.startswith("model."):
            continue
        # Collect test-result statuses for this model.
        test_results: list[dict[str, Any]] = []
        for tuid, tnode in nodes.items():
            if not tuid.startswith("test."):
                continue
            if uid in (tnode.get("depends_on", {}).get("nodes") or []):
                tr = results_by_uid.get(tuid)
                if tr:
                    test_results.append({"test_id": tuid,
                                          "status": tr.get("status"),
                                          "execution_time": tr.get("execution_time")})
        run = results_by_uid.get(uid, {})
        out.append({
            "model_id":      uid,
            "name":          node.get("name"),
            "schema":        node.get("schema"),
            "materialized":  (node.get("config") or {}).get("materialized"),
            "run_status":    run.get("status") or "unrun",
            "run_time_s":    run.get("execution_time"),
            "tests_total":   len(test_results),
            "tests_failed":  sum(1 for t in test_results
                                  if t["status"] in ("fail", "error")),
            "tests":         test_results,
        })
    return out


@router.get("/lineage")
async def lineage(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Lineage DAG: source and model nodes plus edges. Each node carries its
    tier (source, staging, vault_raw, vault_business, mart), inferred from
    schema and dbt path, and the rolled-up status of its tests.
    """
    await _gate(user)

    manifest = _load_manifest()
    rr = _load_run_results()
    if not manifest:
        return {"nodes": [], "edges": [], "as_of": None,
                "note": "dbt manifest not yet uploaded; run dbt-marts first"}

    results_by_uid = {r["unique_id"]: r
                      for r in (rr.get("results") or []) if "unique_id" in r}
    nodes_raw = manifest.get("nodes") or {}
    sources_raw = manifest.get("sources") or {}
    metadata = manifest.get("metadata") or {}

    def tier_of(node: dict[str, Any]) -> str:
        # Schema is the most reliable signal; dbt path is the fallback.
        s = (node.get("schema") or "").lower()
        if s.startswith("nhdw_landing") or s.startswith("nhdw_staging"):
            return "staging"
        if s.startswith("nhdw_vault_raw"):
            return "vault_raw"
        if s.startswith("nhdw_vault_business"):
            return "vault_business"
        if s.startswith("bi_"):
            return "mart"
        # Fallback: dbt path.
        path = (node.get("original_file_path") or "").lower()
        if "raw_vault" in path:      return "vault_raw"
        if "business_vault" in path: return "vault_business"
        if "staging" in path:        return "staging"
        if "marts" in path:          return "mart"
        return "other"

    nodes_out: list[dict[str, Any]] = []
    # dbt sources: the external tables the pipeline reads.
    for uid, src in sources_raw.items():
        nodes_out.append({
            "id":           uid,
            "name":         src.get("name") or uid.split(".")[-1],
            "tier":         "source",
            "schema":       src.get("schema"),
            "test_status":  None,
            "tests_total":  0,
            "tests_failed": 0,
            "materialized": "source",
        })
    # Models.
    for uid, node in nodes_raw.items():
        if not uid.startswith("model."):
            continue
        # Tests targeting this model, rolled up to one status.
        tests = [t_uid for t_uid, t_node in nodes_raw.items()
                 if t_uid.startswith("test.")
                 and uid in (t_node.get("depends_on", {}).get("nodes") or [])]
        statuses = [results_by_uid.get(t_uid, {}).get("status") for t_uid in tests]
        failed = sum(1 for s in statuses if s in ("fail", "error"))
        warn = sum(1 for s in statuses if s == "warn")
        if failed > 0:
            test_status = "fail"
        elif warn > 0:
            test_status = "warn"
        elif statuses:
            test_status = "pass"
        else:
            test_status = "untested"
        nodes_out.append({
            "id":           uid,
            "name":         node.get("name"),
            "tier":         tier_of(node),
            "schema":       node.get("schema"),
            "test_status":  test_status,
            "tests_total":  len(statuses),
            "tests_failed": failed,
            "materialized": (node.get("config") or {}).get("materialized"),
            "run_status":   results_by_uid.get(uid, {}).get("status", "unrun"),
            "description":  (node.get("description") or "")[:240],
        })

    # Edges: every model node's depends_on.nodes (excluding tests).
    edges: list[dict[str, str]] = []
    valid_ids = {n["id"] for n in nodes_out}
    for uid, node in nodes_raw.items():
        if not uid.startswith("model."):
            continue
        for upstream in (node.get("depends_on", {}).get("nodes") or []):
            if upstream in valid_ids:
                edges.append({"from": upstream, "to": uid})

    return {
        "nodes":       nodes_out,
        "edges":       edges,
        "as_of":       metadata.get("generated_at"),
        "tier_order":  ["source", "staging", "vault_raw", "vault_business", "mart"],
    }


# Source pulse: per-source completeness and validity from mart_data_quality.
@router.get("/sources")
async def sources(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Roll bi_quality.mart_data_quality up to one row per source. Returns an
    empty list with a note when the mart is not queryable."""
    await _gate(user)
    q = """
        SELECT source,
               max(period_id)                  AS latest_period,
               round(avg(completeness_pct), 1) AS completeness,
               round(avg(validity_pct), 1)     AS validity,
               round(avg(timeliness_pct), 1)   AS timeliness,
               sum(reports_received)           AS reports
        FROM marts.bi_quality.mart_data_quality
        GROUP BY source
        ORDER BY source
    """
    try:
        res = await trino_client.query(q)
        rows = [{**r, "source": _MART_SOURCE_ALIAS.get(r.get("source"),
                                                        r.get("source"))}
                for r in res["rows"]]
        return {"sources": rows, "note": None,
                "expected": [s["key"] for s in SOURCES]}
    except Exception as exc:                                    # noqa: BLE001
        return {"sources": [], "expected": [s["key"] for s in SOURCES],
                "note": f"mart_data_quality not yet queryable: {exc}"}


# Ingestion freshness from the raw-lake _manifest.json sidecars.
@router.get("/ingestion")
async def ingestion(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Per-source freshness from nhdw-raw manifests: last successful ingest,
    records in the last cycle, lag against SLA, whether the source changed
    in the last run, and a short daily-volume trend."""
    await _gate(user)
    global _ingest_cache
    now = time.time()
    if _ingest_cache and now - _ingest_cache[0] < ARTIFACT_CACHE_TTL_S:
        return {"sources": _ingest_cache[1], "note": None}

    out: list[dict[str, Any]] = []
    note: str | None = None
    _live_slas, _ = await _thresholds()
    try:
        s3 = _s3()
        for src in SOURCES:
            manifests = _scan_source_manifests(s3, src["raw_prefix"])
            out.append(_rollup_source(src["key"], manifests,
                                       int(_live_slas.get(src["key"], src["sla_hours"]))))
        _ingest_cache = (now, out)
    except Exception as exc:                                    # noqa: BLE001
        note = f"raw-lake scan unavailable: {exc}"
    return {"sources": out, "note": note}


# DQ dimension scorecard.
@router.get("/dimensions")
async def dimensions(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Five-dimension rollup. Completeness, validity and timeliness come from
    mart_data_quality; consistency and uniqueness are the pass rates of the
    dbt `relationships` and `unique` test families."""
    await _gate(user)
    dims: dict[str, float | None] = {"completeness": None, "validity": None,
                                     "timeliness": None, "consistency": None,
                                     "uniqueness": None}
    basis: dict[str, str] = {}

    try:
        res = await trino_client.query("""
            SELECT round(avg(completeness_pct), 1) AS c,
                   round(avg(validity_pct), 1)     AS v,
                   round(avg(timeliness_pct), 1)   AS t
            FROM marts.bi_quality.mart_data_quality
        """)
        if res["rows"]:
            r = res["rows"][0]
            dims["completeness"] = r.get("c")
            dims["validity"]     = r.get("v")
            dims["timeliness"]   = r.get("t")
            for k in ("completeness", "validity", "timeliness"):
                if dims[k] is not None:
                    basis[k] = "avg across mart_data_quality"
    except Exception as exc:                                    # noqa: BLE001
        basis["mart_error"] = str(exc)

    rr = _load_run_results()
    fam = {"unique": [0, 0], "relationship": [0, 0]}
    for r in rr.get("results") or []:
        uid = r.get("unique_id") or ""
        if not uid.startswith("test."):
            continue
        st = r.get("status")
        for name in ("unique", "relationship"):
            if name in uid:
                fam[name][1] += 1
                if st in ("pass", "success"):
                    fam[name][0] += 1
    if fam["unique"][1]:
        dims["uniqueness"] = round(100.0 * fam["unique"][0] / fam["unique"][1], 1)
        basis["uniqueness"] = f"{fam['unique'][0]}/{fam['unique'][1]} unique tests pass"
    if fam["relationship"][1]:
        dims["consistency"] = round(100.0 * fam["relationship"][0] / fam["relationship"][1], 1)
        basis["consistency"] = f"{fam['relationship'][0]}/{fam['relationship'][1]} relationship tests pass"
    return {"dimensions": dims, "basis": basis}


# Cross-source reconciliation: master-data agreement in the business vault.
@router.get("/reconciliation")
async def reconciliation(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Cross-source agreement from the business-vault same-as links: the share
    of conformed facilities and commodities recognised by at least two source
    systems. A check is omitted if its link or view cannot be reached.

    source_system lives on the resolution view (v_facility_resolution,
    v_commodity_resolution), not on the keys-only link (Data Vault 2.0). The
    link joins its resolution view on the same-as key `hk_same_as_*`, not on
    the source-hub key `hk_source_*`: source hubs hash unqualified integer
    business keys whose ranges overlap between systems (HCMIS and mBrana share
    many identical hk_source_facility and hk_source_commodity values), so that
    join would fan one edge across systems. The same-as key embeds
    source_system and maps each edge to exactly one system. Record channels
    (MFR.location/organization, mBrana.dbo/ussd) are folded to their parent
    system with split_part(source_system, '.', 1), so "two sources" means two
    source systems, not two channels of one system."""
    await _gate(user)
    checks: list[dict[str, Any]] = []
    note: str | None = None

    def _clean(s: str | None) -> str:
        return (s or "").lstrip("!")

    for label, link_tbl, conformed_col, resview, same_as_pk, systems in (
        ("Facility master", "lnk_same_as_facility", "hk_facility_conformed",
         "v_facility_resolution", "hk_same_as_facility",
         "DHIS2, HCMIS, MFR, mBrana"),
        ("Commodity master", "lnk_same_as_commodity", "hk_commodity_conformed",
         "v_commodity_resolution", "hk_same_as_commodity",
         "HCMIS, mBrana"),
    ):
        try:
            per = await trino_client.query(f"""
                SELECT split_part(rv.source_system, '.', 1) AS source_system,
                       count(distinct l.{conformed_col})    AS n
                FROM marts.nhdw_vault_business.{link_tbl} l
                JOIN marts.nhdw_vault_business.{resview}  rv
                  ON l.{same_as_pk} = rv.{same_as_pk}
                GROUP BY split_part(rv.source_system, '.', 1)
                ORDER BY 1
            """)
            agg = await trino_client.query(f"""
                SELECT count(*) AS total, count_if(n_src >= 2) AS matched
                FROM (
                    SELECT l.{conformed_col} AS k,
                           count(distinct split_part(rv.source_system, '.', 1))
                                                            AS n_src
                    FROM marts.nhdw_vault_business.{link_tbl} l
                    JOIN marts.nhdw_vault_business.{resview}  rv
                      ON l.{same_as_pk} = rv.{same_as_pk}
                    GROUP BY l.{conformed_col}
                ) t
            """)
            total   = (agg["rows"][0]["total"]   if agg["rows"] else 0) or 0
            matched = (agg["rows"][0]["matched"] if agg["rows"] else 0) or 0
            pct = round(100.0 * matched / total, 1) if total else None
            checks.append({
                "check":       label,
                "systems":     systems,
                "conformed":   total,
                "matched":     matched,
                "agreement_pct": pct,
                "sources":     [{"source": _clean(r["source_system"]),
                                 "count": r["n"]} for r in per["rows"]],
            })
        except Exception as exc:                                # noqa: BLE001
            note = ("business-vault same-as links not reachable via Trino "
                    f"(check bi_reader SELECT grant on nhdw_vault_business): {exc}")
    return {"checks": checks, "note": note}


# k-anonymity compliance for bi_public marts.
@router.get("/compliance")
async def compliance(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Whether every bi_public mart satisfies k >= 5, from the singular
    `k_anonymity_public_marts` dbt test (a failure means a group with n < 5
    was published)."""
    await _gate(user)
    rr = _load_run_results()
    manifest = _load_manifest()
    kanon: dict[str, Any] | None = None
    for r in rr.get("results") or []:
        uid = r.get("unique_id") or ""
        if "k_anonymity" in uid:
            kanon = {"test_id": uid, "status": r.get("status"),
                     "failures": r.get("failures")}
            break
    public_marts = [node.get("name")
                    for uid, node in (manifest.get("nodes") or {}).items()
                    if uid.startswith("model.")
                    and (node.get("schema") or "") == "bi_public"]
    return {"k_anon": {
        "present":           kanon is not None,
        "passed":            bool(kanon and kanon["status"] in ("pass", "success")),
        "status":            kanon["status"] if kanon else None,
        "failures":          kanon["failures"] if kanon else None,
        "public_mart_count": len(public_marts),
        "public_marts":      sorted(m for m in public_marts if m),
    }}


# Acknowledge a failing or warning dbt test (ops_governance.dq_flag, dq_ack).
class AckBody(BaseModel):
    unique_id: str = Field(..., min_length=1)
    model:    str | None = None
    test:     str | None = None
    message:  str | None = None
    severity: str | None = None
    note:     str | None = None


_SEV_MAP = {"fail": "error", "error": "error", "warn": "warn"}


def _fingerprint(*parts: str) -> str:
    """Dedupe hash shared by the detector and /ack; the key schemes are
    documented on the ops_governance.dq_flag.fingerprint column."""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]



async def _acked_dbt_tests(unique_ids: set[str]) -> dict[str, dict[str, Any]]:
    """Which of these dbt test unique_ids already carry an operator ack."""
    if not unique_ids:
        return {}
    try:
        pool = await ops_db.ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT f.key->>'unique_id'                          AS uid,
                       count(a.*)                                   AS n,
                       max(a.acked_at)                              AS last_at,
                       (array_agg(COALESCE(u.username, a.acker::text)
                                  ORDER BY a.acked_at DESC))[1]     AS last_by
                FROM ops_governance.dq_flag f
                JOIN ops_governance.dq_ack  a ON a.flag_id = f.id
                LEFT JOIN ops_identity."user" u ON u.kc_sub = a.acker
                WHERE f.error_class = 'dbt_test'
                  AND f.key->>'unique_id' = ANY($1::text[])
                GROUP BY f.key->>'unique_id'
            """, list(unique_ids))
        return {r["uid"]: {
                    "count":   r["n"],
                    "last_at": r["last_at"].isoformat() if r["last_at"] else None,
                    "last_by": r["last_by"],
                } for r in rows}
    except Exception as exc:                                    # noqa: BLE001
        log.warning("dq ack lookup failed: %s", exc)
        return {}


@router.post("/ack")
async def ack(body: AckBody, user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Record an operator acknowledgement of a dbt test: find or create the
    governance flag for the test, then append an ack by the caller."""
    await _gate(user, action="admin.dq.ack", resource="dq")
    if not user.kc_sub:
        raise HTTPException(400, "ack requires an identified user")
    # dq_ack.acker references ops_identity.user, so the caller's row must exist.
    await ops_db.upsert_user(user)

    sev  = _SEV_MAP.get((body.severity or "").lower(), "warn")
    model = body.model or "dbt"
    desc = (body.message or f"{body.test or body.unique_id} acknowledged").strip()[:500]

    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            flag_id = await conn.fetchval("""
                SELECT id FROM ops_governance.dq_flag
                WHERE error_class = 'dbt_test'
                  AND key->>'unique_id' = $1
                LIMIT 1
            """, body.unique_id)
            if flag_id is None:
                flag_id = await conn.fetchval("""
                    INSERT INTO ops_governance.dq_flag
                        (raised_by, source, table_name, key, error_class,
                         severity, description, status, fingerprint)
                    VALUES ('system', 'dbt', $1, $2::jsonb, 'dbt_test',
                            $3, $4, 'acked', $5)
                    RETURNING id
                """, model, json.dumps({"unique_id": body.unique_id}), sev, desc,
                     _fingerprint("dbt", body.unique_id))
            else:
                await conn.execute(
                    "UPDATE ops_governance.dq_flag SET status = 'acked' WHERE id = $1",
                    flag_id)
            acked_at = await conn.fetchval("""
                INSERT INTO ops_governance.dq_ack (flag_id, acker, note)
                VALUES ($1, $2::uuid, $3)
                RETURNING acked_at
            """, flag_id, user.kc_sub, body.note)

    await ops_db.log_data_event(user=user, kind="dq.test.ack",
                                payload={"unique_id": body.unique_id,
                                         "flag_id": str(flag_id),
                                         "model": model, "severity": sev})
    return {"ok": True, "flag_id": str(flag_id), "acked_by": user.username,
            "acked_at": acked_at.isoformat() if acked_at else None}


# DQ detector: raises tracked incidents into ops_governance.dq_flag.
# Deduplicated by dq_flag.fingerprint (unique among non-resolved rows): a
# re-run updates the open flag instead of duplicating it, and a resolved
# finding that recurs opens a new row.
#
# Detection classes are derived from existing signals; dbt tests are not
# re-implemented, their failures are ingested:
#   fk_orphan, null_in_required, dbt_test_failure
#       from failing or warning dbt tests in the run_results of all three
#       projects (relationships -> fk_orphan, not_null -> null_in_required)
#   stale_ingest        newest lake manifest older than the source SLA
#                       (ops_governance.dq_threshold scope='sla_hours',
#                       else the SOURCES default)
#   volume_outlier      newest manifest size outside the table's own history
#                       (at least 4 samples, more than 3x or under 1/3 of
#                       the median)
#   mdm_backlog         undecided MDM match candidates
#   classification_gap  sensitive-looking mart columns with no classification
#
# There is no email path: incidents go to the in-app queue only. Any mailer
# integration must sit behind an explicit DQ_EMAIL_ENABLED=true flag (default
# off), because the SMTP relay delivers to real inboxes.

def _service_authorized(x_detect_token: str | None) -> bool:
    tok = settings.DQ_DETECT_TOKEN
    return bool(tok) and x_detect_token == tok

_DBT_PROJECTS = ("staging", "vault", "marts")
_TEST_CLASS = {"relationships": "fk_orphan", "not_null": "null_in_required"}

_thr_cache: tuple[float, dict[str, float], dict[str, float]] | None = None


async def _thresholds() -> tuple[dict[str, float], dict[str, float]]:
    """(SLA hours by source, UI bands) from ops_governance.dq_threshold,
    cached for 60 s. Any failure falls back to the coded defaults, so this
    table cannot take the DQ endpoints down."""
    global _thr_cache
    now = time.time()
    if _thr_cache and now - _thr_cache[0] < 60:
        return _thr_cache[1], _thr_cache[2]
    slas = {s_["key"]: float(s_["sla_hours"]) for s_ in SOURCES}
    bands = {"scorecard_good": 90.0, "scorecard_watch": 75.0,
             "agreement_good": 85.0, "agreement_watch": 60.0}
    try:
        pool = await ops_db.ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT scope, key, value FROM ops_governance.dq_threshold "
                "WHERE scope IN ('sla_hours', 'ui_band')")
        for r in rows:
            (slas if r["scope"] == "sla_hours" else bands)[r["key"]] = float(r["value"])
    except Exception as exc:                                    # noqa: BLE001
        log.warning("dq_threshold unreadable (%s); coded defaults", exc)
    _thr_cache = (now, slas, bands)
    return slas, bands



def _load_project_artifact(project: str, name: str) -> dict[str, Any]:
    """run_results or manifest for any dbt project (the module-level helpers
    read only the marts project)."""
    try:
        s3 = _s3()
        obj = s3.get_object(Bucket=ARTIFACTS_BUCKET, Key=f"dbt/{project}/{name}")
        return json.load(io.BytesIO(obj["Body"].read()))
    except Exception as exc:                                    # noqa: BLE001
        log.debug("detector: no %s for %s (%s)", name, project, exc)
        return {}


def _detect_dbt_failures() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for proj in _DBT_PROJECTS:
        rr = _load_project_artifact(proj, "run_results.json")
        man = _load_project_artifact(proj, "manifest.json")
        nodes = (man.get("nodes") or {})
        for r in rr.get("results", []):
            uid, status = r.get("unique_id", ""), (r.get("status") or "").lower()
            if not uid.startswith("test.") or status in ("pass", "success", "skipped", ""):
                continue
            node = nodes.get(uid) or {}
            test_name = (node.get("test_metadata") or {}).get("name") or node.get("name", uid)
            models = [d.split(".")[-1] for d in (node.get("depends_on") or {}).get("nodes", [])
                      if d.startswith(("model.", "seed."))]
            out.append({
                # error_class 'dbt_test' matches the rows /ack creates; the
                # finer class is carried in key->>'test_class'.
                "source": "dbt", "table_name": (models[0] if models else proj),
                "error_class": "dbt_test",
                "severity": "error" if status in ("fail", "error") else "warn",
                "description": f"[{proj}] {test_name} {status}: {str(r.get('message') or '')[:300]}",
                "key": {"unique_id": uid, "project": proj,
                        "test_class": _TEST_CLASS.get((node.get("test_metadata") or {}).get("name", ""),
                                                      "dbt_test_failure")},
                "fingerprint": _fingerprint("dbt", uid),
            })
    return out


def _detect_freshness_and_volume(slas: dict[str, float]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    now = time.time()
    try:
        s3 = _s3()
    except Exception as exc:                                    # noqa: BLE001
        log.warning("detector: lake unreachable (%s)", exc)
        return out
    for src in SOURCES:
        key, prefix = src["key"], src["raw_prefix"]
        if not prefix:
            continue
        manifests = _scan_source_manifests(s3, prefix)
        by_table: dict[str, list[dict[str, Any]]] = {}
        for m in manifests:
            by_table.setdefault(m.get("object_name") or m.get("table") or "?", []).append(m)
        # stale_ingest: newest manifest across the whole source against the SLA
        newest = 0.0
        for ms in by_table.values():
            for m in ms:
                ts = m.get("completed_at") or m.get("started_at")
                try:
                    newest = max(newest, datetime.fromisoformat(
                        str(ts).replace("Z", "+00:00")).timestamp())
                except Exception:                               # noqa: BLE001
                    continue
        sla = slas.get(key, 24.0)
        if newest and (now - newest) > sla * 3600:
            age_h = int((now - newest) / 3600)
            out.append({
                "source": key, "table_name": "*",
                "error_class": "stale_ingest", "severity": "warn",
                "description": f"newest lake manifest is {age_h}h old (SLA {int(sla)}h)",
                "key": {"prefix": prefix, "age_hours": age_h},
                "fingerprint": _fingerprint("stale", key),
            })
        # volume_outlier per table (needs history)
        for table, ms in by_table.items():
            sizes = sorted(float(m.get("bytes_written") or 0) for m in ms
                           if m.get("bytes_written"))
            if len(sizes) < 4 or not sizes[-1]:
                continue
            hist = sizes[:-1]
            med = hist[len(hist) // 2]
            latest = sizes[-1]
            if med and (latest > med * 3 or latest < med / 3):
                out.append({
                    "source": key, "table_name": table,
                    "error_class": "volume_outlier", "severity": "warn",
                    "description": f"latest load {int(latest)}B vs median {int(med)}B",
                    "key": {"prefix": prefix, "table": table},
                    "fingerprint": _fingerprint("volume", key, table),
                })
    return out


async def _detect_mdm_backlog(conn) -> list[dict[str, Any]]:
    try:
        n = await conn.fetchval("""
            SELECT count(*) FROM ops_governance.mdm_match_candidate c
            WHERE NOT EXISTS (SELECT 1 FROM ops_governance.mdm_match_decision d
                              WHERE d.candidate_id = c.candidate_id)
        """)
    except Exception as exc:                                    # noqa: BLE001
        log.debug("detector: mdm stores unreadable (%s); skipping backlog", exc)
        return []
    if not n:
        return []
    return [{
        "source": "mdm", "table_name": "mdm_match_candidate",
        "error_class": "mdm_backlog", "severity": "warn" if n > 100 else "info",
        "description": f"{n} match candidates await a steward decision",
        "key": {"undecided": int(n)},
        "fingerprint": _fingerprint("mdm", "backlog"),
    }]


async def _detect_classification_gaps() -> list[dict[str, Any]]:
    """Mart columns that look sensitive and have no classification row.

    Masking is a denylist, so an unclassified column is served in the clear.
    The steward endpoint lists these on demand; the flag brings the gap to the
    incident queue without anyone having to look. One flag covers the whole
    scan rather than one per column: the work item is reviewing the gap list,
    and per-column flags would flood the queue after a rename."""
    try:
        scanned, gaps = await classification.scan_unclassified()
    except Exception as exc:                                    # noqa: BLE001
        log.debug("detector: mart catalog unreadable (%s); skipping gap scan", exc)
        return []
    if not gaps:
        return []
    sample = ", ".join(f"{g['table_name']}.{g['column_name']}" for g in gaps[:5])
    return [{
        "source": "governance", "table_name": "column_classification",
        "error_class": "classification_gap",
        "severity": "warn" if len(gaps) > 10 else "info",
        "description": (f"{len(gaps)} of {scanned} mart columns look sensitive but "
                        f"are unclassified (served in the clear): {sample}"
                        + (" ..." if len(gaps) > 5 else "")),
        "key": {"gaps": len(gaps), "scanned": scanned,
                "columns": [f"{g['schema_name']}.{g['table_name']}.{g['column_name']}"
                            for g in gaps[:50]]},
        "fingerprint": _fingerprint("classification", "gap"),
    }]


async def _upsert_flags(conn, findings: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in findings:
        # The flag and its first history row are written in one transaction.
        # Otherwise a failure between them (for example a missing sequence
        # grant on the event table) leaves a raised flag with no recorded
        # transition, breaking the append-only history the governance KPIs
        # are computed from.
        async with conn.transaction():
            row = await conn.fetchrow("""
                INSERT INTO ops_governance.dq_flag
                    (raised_by, source, table_name, key, error_class,
                     severity, description, status, fingerprint)
                VALUES ('detector', $1, $2, $3::jsonb, $4, $5, $6, 'open', $7)
                ON CONFLICT (fingerprint) WHERE fingerprint IS NOT NULL AND status <> 'resolved'
                DO UPDATE SET description = EXCLUDED.description,
                              severity    = EXCLUDED.severity,
                              updated_at  = now()
                RETURNING id, (xmax = 0) AS inserted
            """, f["source"], f["table_name"], json.dumps(f["key"]),
                 f["error_class"], f["severity"], f["description"][:500], f["fingerprint"])
            if row:
                # `inserted` is the normal raise; the EXISTS check repairs a
                # flag that an earlier partial write left without history.
                needs_event = row["inserted"] or not await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM ops_governance.dq_flag_event"
                    " WHERE flag_id = $1)", row["id"])
                if needs_event:
                    # The raise is the first recorded transition.
                    await conn.execute("""
                        INSERT INTO ops_governance.dq_flag_event (flag_id, actor, from_status, to_status, note)
                        VALUES ($1, 'detector', NULL, 'open', left($2, 200))
                    """, row["id"], f["description"])
        counts[f["error_class"]] = counts.get(f["error_class"], 0) + 1
    return counts


async def _run_detection() -> dict[str, Any]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        slas, _ = await _thresholds()
        findings = _detect_dbt_failures()
        findings += _detect_freshness_and_volume(slas)
        findings += await _detect_mdm_backlog(conn)
        findings += await _detect_classification_gaps()
        counts = await _upsert_flags(conn, findings)
        open_total = await conn.fetchval(
            "SELECT count(*) FROM ops_governance.dq_flag WHERE status = 'open'")
    return {"findings": len(findings), "by_class": counts, "open_flags": int(open_total)}




@router.post("/detect")
async def detect(request: Request,
                 x_detect_token: str | None = Header(default=None, alias="X-Detect-Token")
                 ) -> dict[str, Any]:
    """Run every detection and upsert incidents. Authorised by the service
    token (Airflow dq_detection DAG) or an admin bearer token, as for the
    alert evaluator. Idempotent per fingerprint; never sends email."""
    service = _service_authorized(x_detect_token)
    user = None
    if not service:
        try:
            user = await get_user(
                request,
                authorization=request.headers.get("authorization"),
                x_mock_user=request.headers.get("x-mock-user"),
                x_mock_role=request.headers.get("x-mock-role"))
            await _gate(user, action="admin.dq.detect", resource="dq")
        except HTTPException:
            raise HTTPException(403, "admin or valid service token required")
    t0 = time.perf_counter()
    summary = await _run_detection()
    log.info("dq detection %s", summary)
    try:
        payload = {**summary, "actor": "detector" if service else
                   (user.username if user else "unknown")}
        if user is not None:
            await ops_db.log_data_event(user=user, kind="dq.detect.run", payload=payload)
        else:
            # Service-token runs have no UserCtx: write the audit row directly
            # with kc_sub NULL, in the shape ops_db.log_data_event uses.
            pool = await ops_db.ensure_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO ops_audit.data_event (kc_sub, kind, payload) "
                    "VALUES (NULL, $1, $2::jsonb)",
                    "dq.detect.run", json.dumps(payload))
    except Exception as exc:                                    # noqa: BLE001
        log.warning("dq detect audit event failed: %s", exc)
    return {**summary, "latency_ms": int((time.perf_counter() - t0) * 1000)}


# Governance history: the incident queue and KPIs computed from it.
# The durable record (dq_flag, dq_flag_event, dq_ack, plus read-only queries
# over the MDM stewardship stores) gives failure, latency and recurrence
# figures per source and rule.

def _flag_out(r: Any) -> dict[str, Any]:
    """Explicit row shape for the SPA (same pattern as mdm.py's _cand_out)."""
    return {
        "id":          str(r["id"]),
        "raised_at":   r["raised_at"].isoformat(),
        "updated_at":  r["updated_at"].isoformat() if r["updated_at"] else None,
        "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None,
        "raised_by":   r["raised_by"],
        "source":      r["source"],
        "table_name":  r["table_name"],
        "error_class": r["error_class"],
        "severity":    r["severity"],
        "description": r["description"],
        "status":      r["status"],
        "events":      int(r["events"]),
        "acks":        int(r["acks"]),
    }


@router.get("/flags")
async def flags(user: UserCtx = Depends(get_user),
                status: str | None = Query(default="open"),
                limit: int = Query(default=50, ge=1, le=500),
                offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
    """The incident queue (newest first). status=all lists every state."""
    await _gate(user)
    pool = await ops_db.ensure_pool()
    where = "" if status in (None, "all") else "WHERE f.status = $3"
    args: list[Any] = [limit, offset] if not where else [limit, offset, status]
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT f.id, f.raised_at, f.updated_at, f.resolved_at, f.raised_by,
                   f.source, f.table_name, f.error_class, f.severity,
                   f.description, f.status,
                   (SELECT count(*) FROM ops_governance.dq_flag_event e
                     WHERE e.flag_id = f.id)                       AS events,
                   (SELECT count(*) FROM ops_governance.dq_ack a
                     WHERE a.flag_id = f.id)                       AS acks
            FROM ops_governance.dq_flag f
            {where}
            ORDER BY f.updated_at DESC
            LIMIT $1 OFFSET $2
        """, *args)
        counts = await conn.fetch(
            "SELECT status, count(*) AS n FROM ops_governance.dq_flag GROUP BY 1")
    return {"flags": [_flag_out(r) for r in rows],
            "counts": {r["status"]: int(r["n"]) for r in counts},
            "limit": limit, "offset": offset}


@router.get("/kpi")
async def kpi(user: UserCtx = Depends(get_user),
              weeks: int = Query(default=12, le=52)) -> dict[str, Any]:
    """Governance KPIs from the durable incident/stewardship history."""
    await _gate(user)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        open_by = await conn.fetch("""
            SELECT error_class, severity, source, count(*) AS n
            FROM ops_governance.dq_flag WHERE status = 'open'
            GROUP BY 1, 2, 3 ORDER BY n DESC
        """)
        raised_weekly = await conn.fetch("""
            SELECT to_char(date_trunc('week', ts), 'YYYY-MM-DD') AS week,
                   count(*) AS raised
            FROM ops_governance.dq_flag_event
            WHERE to_status = 'open' AND ts > now() - ($1::int * interval '1 week')
            GROUP BY 1 ORDER BY 1
        """, weeks)
        latency = await conn.fetchrow("""
            SELECT
              round(avg(EXTRACT(epoch FROM a.first_ack - f.raised_at)) / 3600, 1) AS avg_hours_to_ack,
              round(avg(EXTRACT(epoch FROM f.resolved_at - f.raised_at))
                    FILTER (WHERE f.resolved_at IS NOT NULL) / 3600, 1)           AS avg_hours_to_resolve,
              count(*) FILTER (WHERE f.resolved_at IS NOT NULL)                   AS resolved_total
            FROM ops_governance.dq_flag f
            LEFT JOIN (SELECT flag_id, min(acked_at) AS first_ack
                       FROM ops_governance.dq_ack GROUP BY 1) a ON a.flag_id = f.id
        """)
        recurrence = await conn.fetchval("""
            SELECT count(*) FROM (
                SELECT fingerprint FROM ops_governance.dq_flag
                WHERE fingerprint IS NOT NULL
                GROUP BY fingerprint HAVING count(*) > 1) t
        """)
        steward = {}
        try:
            steward = dict(await conn.fetchrow("""
                SELECT (SELECT count(*) FROM ops_governance.mdm_match_candidate c
                        WHERE NOT EXISTS (SELECT 1 FROM ops_governance.mdm_match_decision d
                                          WHERE d.candidate_id = c.candidate_id)) AS backlog,
                       (SELECT count(*) FROM ops_governance.mdm_match_decision)   AS decisions
            """))
        except Exception as exc:                                # noqa: BLE001
            log.debug("kpi: mdm stores unreadable (%s)", exc)
    return {
        "open_by": [{"error_class": r["error_class"], "severity": r["severity"],
                     "source": r["source"], "n": int(r["n"])} for r in open_by],
        "raised_weekly": [{"week": r["week"], "raised": int(r["raised"])}
                          for r in raised_weekly],
        "avg_hours_to_ack": float(latency["avg_hours_to_ack"]) if latency["avg_hours_to_ack"] is not None else None,
        "avg_hours_to_resolve": float(latency["avg_hours_to_resolve"]) if latency["avg_hours_to_resolve"] is not None else None,
        "resolved_total": int(latency["resolved_total"] or 0),
        "reopened_fingerprints": int(recurrence or 0),
        "steward": {k: int(v) for k, v in steward.items()} if steward else {},
    }
