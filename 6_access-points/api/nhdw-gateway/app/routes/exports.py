"""/api/exports: generate and list dashboard exports."""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import UserCtx, get_user
from .. import mart_attrs, masking, minio_client, ops_db, opa, permissions, trino_client
from ..mart_attrs import MartAttrsMissing
from ..dimensions import columns_for
from ..settings import settings

router = APIRouter(prefix="/api/exports", tags=["exports"])


def _render(payload: dict[str, Any], fmt: str) -> tuple[bytes, str]:
    rows = payload["rows"]
    if fmt == "json":
        return (json.dumps(payload, default=str, separators=(",", ":")).encode(),
                "application/json")
    if fmt == "csv":
        if not rows:
            return b"", "text/csv"
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=payload["columns"], extrasaction="ignore")
        w.writeheader()
        for r in rows: w.writerow(r)
        return out.getvalue().encode(), "text/csv"
    raise HTTPException(400, f"unsupported format: {fmt}")


@router.get("/me")
async def my_exports(user: UserCtx = Depends(get_user),
                     limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    rows = await ops_db.my_exports(user, limit)
    # Add a fresh presigned URL for any export that's still 'ready'.
    for r in rows:
        if r["status"] == "ready" and r["minio_key"]:
            r["download_url"] = await minio_client.presign_get(
                key=r["minio_key"], ttl_s=settings.EXPORT_PRESIGN_TTL_S,
                filename=f"export-{str(r['id'])[:8]}.{r['format']}")
    return rows


@router.post("/marts/{schema}/{table}")
async def export_mart(schema: str, table: str, request: Request,
                      user: UserCtx = Depends(get_user),
                      format: str = Query("csv", regex="^(csv|json)$"),
                      retention_days: int = Query(90, ge=1, le=365)) -> dict:
    mart = f"{schema}.{table}"

    # 1. OPA decision: verb 'export' on the mart's subject, with its governance
    #    attributes and the caller's scope. The rego rules match on the
    #    subject, not on <schema.table>.
    try:
        attrs = await mart_attrs.lookup(schema, table)
    except MartAttrsMissing:
        raise HTTPException(422, f"mart {mart} is not in the governance catalog")
    _pool = await ops_db.ensure_pool()
    async with _pool.acquire() as _c:
        _scope = await permissions.effective_user_scope(_c, user)
    colclass = await mart_attrs.columns_for_mart(schema, table)
    decision = await opa.decide(user, action="export", resource=attrs.subject,
                                attributes=attrs.opa_attributes, scope=_scope,
                                columns={n: c.sensitivity for n, c in colclass.items()})
    if not decision.allow:
        await ops_db.log_policy_decision(
            user=user, action="export.mart", resource=mart, decision="deny",
            rule_hits=decision.rule_hits, explanation={"reason": decision.reason})
        await ops_db.log_gateway_query(
            user=user, method="POST", path=f"/api/exports/marts/{mart}",
            mart_scope=[mart], filters={}, row_count=None, byte_count=None,
            latency_ms=0, http_status=403, policy_decision="deny",
            denied_reason=decision.reason)
        raise HTTPException(403, decision.reason or "denied by policy")

    # 2. Parse filters from the query string (same shape as marts.py).
    params = dict(request.query_params.multi_items())
    params.pop("format", None); params.pop("retention_days", None)
    columns = [c.strip() for c in params.pop("_columns", "").split(",") if c.strip()] or None
    order_by = [o.strip() for o in params.pop("_orderby", "").split(",") if o.strip()] or None
    limit = int(params.pop("_limit", "5000"))
    where: dict[str, Any] = {}
    for k, v in params.items():
        cur = where.get(k)
        if isinstance(cur, list): cur.append(v)
        elif cur is not None: where[k] = [cur, v]
        else: where[k] = v

    # 2b. AND OPA's row_filter into the WHERE and drop masked columns, so a
    #     scoped user exports only their granted rows (the same row-level
    #     enforcement the widgets apply). Dimensions map to mart columns
    #     through the shared registry in app/dimensions.py.
    if decision.row_filter or decision.masked_columns:
        _cr = await trino_client.query(
            "SELECT column_name FROM marts.information_schema.columns "
            f"WHERE table_schema = '{schema}' AND table_name = '{table}'")
        _mcols = {r["column_name"] for r in _cr.get("rows", [])}
        for _dim, _vals in (decision.row_filter or {}).items():
            if not _vals:
                continue
            _col = next((c for c in columns_for(_dim) if c in _mcols), None)
            if _col:
                where[_col] = list(_vals)
        # Remove only drop-class masked columns from the SELECT (never
        # fetched). Transform classes (generalize/partial/hash/tokenize) stay
        # in the SELECT and are transformed after the fetch by apply_masking.
        _masked = {c for c in (decision.masked_columns or [])
                   if colclass.get(c) is None or colclass[c].mask_class == "drop"}
        if _masked:
            columns = [c for c in (columns or sorted(_mcols)) if c not in _masked]

    # 3. Register the pending row first so a write failure leaves a trace.
    export_id = await ops_db.register_export(
        user=user, mart_scope=[mart],
        sql_text=None,
        filter_envelope={"filters": where, "scope": user.scope_dict()},
        policy_decision={"allow": True, "rule_hits": decision.rule_hits,
                         "obligations": decision.obligations,
                         "masked_columns": decision.masked_columns},
        format=format, retention_ttl_days=retention_days)
    await ops_db.log_policy_decision(
        user=user, action="export.mart", resource=mart, decision="allow",
        rule_hits=decision.rule_hits, explanation={"obligations": decision.obligations},
        correlation_id=export_id)

    # 4. Trino query, render, write to the object store.
    t0 = time.perf_counter()
    today = dt.date.today()
    minio_key = (f"user={user.kc_sub or 'anon'}/"
                 f"{today:%Y/%m/%d}/{export_id}.{format}")
    try:
        result = await trino_client.fetch_mart(
            schema=schema, table=table, columns=columns,
            where=where or None, order_by=order_by, limit=limit)
        # Masking obligation on the exported rows: transform classes mask in
        # place. Drop classes were removed from the SELECT above; this pass
        # also covers SELECT * exports and the transforms the pre-drop cannot
        # express.
        if decision.masked_columns:
            result["rows"], _dropped = masking.apply_masking(
                result["rows"], colclass, decision.masked_columns)
            if _dropped:
                result["columns"] = [c for c in result.get("columns", [])
                                     if c not in set(_dropped)]
        body, content_type = _render(result, format)
        etag, sha256, byte_count = await minio_client.write_export(
            key=minio_key, body=body, content_type=content_type)
        await ops_db.finalize_export(
            export_id=export_id, status="ready", minio_key=minio_key,
            row_count=result["row_count"], byte_count=byte_count, sha256=sha256,
            citation_blurb=f"NHDW, {mart}, "
                           f"exported by {user.username or user.kc_sub} "
                           f"on {today.isoformat()}.")
        url = await minio_client.presign_get(
            key=minio_key, filename=f"export-{export_id[:8]}.{format}")
    except Exception as exc:
        await ops_db.finalize_export(export_id=export_id, status="failed",
                                     error_msg=str(exc)[:1000])
        await ops_db.log_gateway_query(
            user=user, method="POST", path=f"/api/exports/marts/{mart}",
            mart_scope=[mart], filters=where, row_count=None, byte_count=None,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            http_status=500, policy_decision="error", denied_reason=str(exc))
        raise HTTPException(500, f"export render failed: {exc}")

    await ops_db.log_gateway_query(
        user=user, method="POST", path=f"/api/exports/marts/{mart}",
        mart_scope=[mart], filters=where,
        row_count=result["row_count"], byte_count=byte_count,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        http_status=200, policy_decision="allow")
    await ops_db.log_data_event(
        user=user, kind="export.created",
        payload={"export_id": export_id, "mart": mart, "format": format,
                 "row_count": result["row_count"], "byte_count": byte_count},
        correlation_id=export_id)

    return {
        "export_id":    export_id,
        "download_url": url,
        "format":       format,
        "row_count":    result["row_count"],
        "byte_count":   byte_count,
        "expires_in_s": settings.EXPORT_PRESIGN_TTL_S,
    }
