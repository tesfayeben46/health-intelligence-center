"""/api/marts: read marts through Trino.

GET /api/marts                              list known marts (Trino marts.bi_*)
GET /api/marts/{schema}/{table}             query a mart with filters
POST /api/marts/{schema}/{table}/export     materialise the result as an export

Filter syntax: query-string keys map to WHERE col=? (or col IN (?,?,...) for
arrays). Reserved keys: `_limit`, `_orderby`, `_columns`.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth import UserCtx, get_user
from .. import mart_attrs, masking, ops_db, opa, permissions, trino_client
from ..mart_attrs import MartAttrsMissing
from ..settings import settings

router = APIRouter(prefix="/api/marts", tags=["marts"])


@router.get("")
async def list_marts(request: Request, user: UserCtx = Depends(get_user)) -> dict:
    sql = """
        SELECT table_schema, table_name
        FROM marts.information_schema.tables
        WHERE table_schema LIKE 'bi_%'
        ORDER BY table_schema, table_name
    """
    t0 = time.perf_counter()
    res = await trino_client.query(sql)
    # Only marts in the mart_attrs catalog are queryable (the SQL executor and
    # get_mart return 403 for anything else). Filter the information_schema
    # listing to the catalogued set so the browser never offers an
    # uncatalogued intermediate or bridge table (e.g.
    # bi_dhis2_facility_with_region). A mart is listed only if it is both
    # deployed and catalogued.
    cataloged = await mart_attrs.catalog_keys()
    rows = [r for r in (res.get("rows") or [])
            if (r.get("table_schema"), r.get("table_name")) in cataloged]
    res["rows"] = rows
    res["row_count"] = len(rows)
    await ops_db.log_gateway_query(
        user=user, method="GET", path="/api/marts",
        mart_scope=[], filters={},
        row_count=res["row_count"], byte_count=None,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        http_status=200, policy_decision="allow")
    return res


@router.get("/{schema}/{table}")
async def get_mart(schema: str, table: str, request: Request,
                   user: UserCtx = Depends(get_user)) -> dict:
    mart = f"{schema}.{table}"

    # Direct-data guard: a raw mart read returns whole rows and cannot be
    # generically row-filtered here, so it is limited to callers without a
    # row scope. Scoped regional or programme users (and their PATs) get 403
    # and use the governed widgets and reports, which apply row_filter.
    if await permissions.user_is_row_scoped(await ops_db.ensure_pool(), user):
        await ops_db.log_gateway_query(
            user=user, method="GET", path=f"/api/marts/{mart}",
            mart_scope=[mart], filters={}, row_count=None, byte_count=None,
            latency_ms=0, http_status=403, policy_decision="deny",
            denied_reason="direct mart read denied for row-scoped principal")
        raise HTTPException(
            403, "direct mart access is restricted to unscoped roles; use the "
                 "governed dashboards/reports, which enforce your data scope.")

    # OPA's role policies branch on a subject code (PT/HP/FAC/PRD/PRG) and on
    # data_class, granularity and sensitivity, so the mart's catalog
    # attributes are passed in; under OPA_ENFORCE=true an uncatalogued mart is
    # not served.
    # The mart's classified columns (a denylist; {} when none) go into the OPA
    # input so the policy can emit masked_columns. The masking transform is
    # applied to the result rows below.
    colclass = await mart_attrs.columns_for_mart(schema, table)
    col_sens = {n: c.sensitivity for n, c in colclass.items()}
    try:
        attrs = await mart_attrs.lookup(schema, table)
    except MartAttrsMissing as miss:
        if settings.OPA_ENFORCE:
            await ops_db.log_gateway_query(
                user=user, method="GET", path=f"/api/marts/{mart}",
                mart_scope=[mart], filters={}, row_count=None, byte_count=None,
                latency_ms=0, http_status=403, policy_decision="deny",
                denied_reason=f"mart_attrs catalog miss: {miss}")
            raise HTTPException(
                403, "mart not cataloged; refusing to serve under OPA_ENFORCE=true")
        decision = await opa.decide(user, action="read", resource=mart,
                                    columns=col_sens)
    else:
        decision = await opa.decide(
            user, action="read", resource=attrs.subject,
            attributes=attrs.opa_attributes, columns=col_sens)

    if not decision.allow:
        await ops_db.log_gateway_query(
            user=user, method="GET", path=f"/api/marts/{mart}",
            mart_scope=[mart], filters={}, row_count=None, byte_count=None,
            latency_ms=0, http_status=403, policy_decision="deny",
            denied_reason=decision.reason)
        raise HTTPException(403, decision.reason or "denied by policy")

    # Parse filters from query string. Reserved: _limit / _orderby / _columns.
    params = dict(request.query_params.multi_items())
    where: dict[str, Any] = {}
    limit = int(params.pop("_limit", "500"))
    columns_csv = params.pop("_columns", "").strip()
    columns = [c.strip() for c in columns_csv.split(",") if c.strip()] or None
    orderby_csv = params.pop("_orderby", "").strip()
    order_by = [o.strip() for o in orderby_csv.split(",") if o.strip()] or None
    for k, v in params.items():
        # ?region=ET-OR&region=ET-AM becomes an IN list
        existing = where.get(k)
        if isinstance(existing, list):
            existing.append(v)
        elif existing is not None:
            where[k] = [existing, v]
        else:
            where[k] = v

    t0 = time.perf_counter()
    try:
        res = await trino_client.fetch_mart(
            schema=schema, table=table, columns=columns,
            where=where or None, order_by=order_by, limit=limit)
        status = 200
    except Exception as exc:
        await ops_db.log_gateway_query(
            user=user, method="GET", path=f"/api/marts/{mart}",
            mart_scope=[mart], filters=where, row_count=None, byte_count=None,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            http_status=500, policy_decision="error", denied_reason=str(exc))
        raise HTTPException(500, f"mart query failed: {exc}")

    # Apply the masking obligation to the result rows.
    if decision.masked_columns:
        res["rows"], dropped = masking.apply_masking(
            res["rows"], colclass, decision.masked_columns)
        if dropped:
            res["columns"] = [c for c in res.get("columns", []) if c not in set(dropped)]

    await ops_db.log_gateway_query(
        user=user, method="GET", path=f"/api/marts/{mart}",
        mart_scope=[mart], filters=where,
        row_count=res["row_count"], byte_count=None,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        http_status=status, policy_decision="allow",
        policy_obligations={"masked_columns": decision.masked_columns}
            if decision.masked_columns else None)
    return res
