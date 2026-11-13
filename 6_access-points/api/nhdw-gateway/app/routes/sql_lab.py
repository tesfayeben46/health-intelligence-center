"""/api/sql/execute: ad-hoc read-only SQL executor with per-table OPA checks.

The single authorisation chain for SQL execution, used by:
  - the Internal-BI SQL Lab widget
  - the chatbot's `run_sql` tool (internal delegate)
  - PAT-authenticated notebooks and exporters issuing direct SQL

Per request:
  0. Row-scoped callers are refused (raw SQL cannot be row-filtered).
  1. Strict syntax guard: SELECT/WITH/SHOW/DESCRIBE/EXPLAIN only, single
     statement, no DDL/DML keywords, a LIMIT appended to SELECT/WITH when
     absent.
  2. Parse with sqlglot (Trino dialect) and extract every referenced
     (schema, table) from FROM and JOIN clauses, including subqueries and
     CTEs.
  3. For each referenced (schema, table):
       a. mart_attrs lookup: 403 if uncatalogued
       b. opa.decide(user, action='read', resource=subject, attributes=...):
          403 on deny
  4. Execute through trino_client and apply masking to the result.
  5. Audit log with the parsed table list in mart_scope.

The per-table OPA check is the gate: there is no separate coarse "may this
role use SQL" check, so a role that can read at least one catalogued mart can
issue SELECTs against it, and is refused on the first table it cannot read.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

import sqlglot
from sqlglot import exp
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import UserCtx, get_user
from .. import mart_attrs, masking, ops_db, opa, permissions
from ..mart_attrs import MartAttrsMissing
from ..settings import settings

log = logging.getLogger("gateway.sql_lab")
router = APIRouter(prefix="/api/sql", tags=["sql"])

DEFAULT_MAX_ROWS = 500
HARD_MAX_ROWS   = 5000
TIMEOUT_MS      = 6000
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|"
    r"truncate|copy|merge|call|do|vacuum|comment|reindex)\b", re.I)


class ExecuteRequest(BaseModel):
    sql:   str = Field(min_length=8, max_length=8000)
    limit: int = Field(default=DEFAULT_MAX_ROWS, ge=1, le=HARD_MAX_ROWS)


def _extract_tables(sql: str) -> list[tuple[str, str]]:
    """Parse the SQL with sqlglot and return every distinct
    (schema, table) referenced. Catalog (if present) is dropped because
    every queryable table lives in the `marts` Trino catalog (postgres-vault);
    subjects are identified by (schema, table).

    Raises ValueError on a parse error or on a table reference without an
    explicit schema: unqualified names are refused so the authorisation check
    is deterministic."""
    parsed = sqlglot.parse_one(sql, read="trino")
    out: set[tuple[str, str]] = set()
    for t in parsed.find_all(exp.Table):
        # `t.this`     = table name
        # `t.db`       = schema (if qualified)
        # `t.catalog`  = catalog (if qualified)
        table_name = t.name
        schema_name = t.db
        if not schema_name:
            raise ValueError(
                f"unqualified table reference {table_name!r}; "
                f"use <schema>.<table> or <catalog>.<schema>.<table>")
        out.add((schema_name, table_name))
    return sorted(out)


@router.post("/execute")
async def execute(body: ExecuteRequest = Body(...),
                  user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    # 0. Direct-data guard: raw SQL cannot be generically row-filtered, so it
    # is limited to callers without a row scope. A scoped regional or
    # programme user (or their PAT) gets 403 and uses the governed dashboards
    # and reports, which apply row_filter.
    if await permissions.user_is_row_scoped(await ops_db.ensure_pool(), user):
        raise HTTPException(
            403, "ad-hoc SQL is restricted to unscoped roles; use the governed "
                 "dashboards/reports, which enforce your data scope.")

    # 1. Strict syntax guard
    q = body.sql.strip().rstrip(";").strip()
    if not q:
        raise HTTPException(400, "empty query")
    if not re.match(r"^(select|with|show|describe|explain)\b", q, re.I):
        raise HTTPException(400,
            "only SELECT/WITH/SHOW/DESCRIBE/EXPLAIN queries are allowed")
    if ";" in q:
        raise HTTPException(400, "only a single statement is allowed")
    if FORBIDDEN.search(q):
        raise HTTPException(400, "only read-only queries are allowed")
    if (re.match(r"^(select|with)\b", q, re.I)
            and not re.search(r"\blimit\b", q, re.I)):
        q = f"{q} LIMIT {body.limit}"

    # 2. Parse and extract referenced tables for per-table OPA enforcement.
    # SHOW/DESCRIBE/EXPLAIN introspect the catalog rather than read mart rows,
    # so they skip the per-table OPA check; only the row-scope guard above
    # applies to them.
    referenced: list[tuple[str, str]] = []
    masked_classes: dict[str, Any] = {}    # masked column name -> ColumnClass
    if re.match(r"^(select|with)\b", q, re.I):
        try:
            referenced = _extract_tables(q)
            parsed = sqlglot.parse_one(q, read="trino")
        except (sqlglot.ParseError, ValueError) as exc:
            raise HTTPException(400, f"sql parse error: {exc}")
        if not referenced:
            raise HTTPException(400, "no tables referenced in the query")
        for schema, table in referenced:
            try:
                attrs = await mart_attrs.lookup(schema, table)
            except MartAttrsMissing as miss:
                raise HTTPException(
                    403, f"mart not cataloged: {schema}.{table}; {miss}")
            colclass = await mart_attrs.columns_for_mart(schema, table)
            decision = await opa.decide(
                user, action="read", resource=attrs.subject,
                attributes=attrs.opa_attributes,
                columns={n: c.sensitivity for n, c in colclass.items()})
            if not decision.allow:
                raise HTTPException(
                    403, f"OPA denied {schema}.{table}: "
                         + (decision.reason or "no reason"))
            for n in decision.masked_columns:
                masked_classes[n] = colclass[n] if n in colclass else None
        # Ad-hoc SQL can alias or derive a masked column (SELECT lat AS y,
        # round(lat, 6)) and defeat name-based result masking, so any explicit
        # reference to a masked column is refused (fail-closed). Columns
        # arriving through SELECT * are masked on the result below.
        if masked_classes:
            explicit = sorted({c.name for c in parsed.find_all(exp.Column)
                               if c.name in masked_classes})
            if explicit:
                raise HTTPException(
                    403, "query references masked column(s) for your role: "
                         + ", ".join(explicit)
                         + "; remove them (SELECT * returns them masked)")

    # 3. Execute
    t0 = time.perf_counter()
    http_status = 200
    err: str | None = None
    rows: list[dict[str, Any]] = []
    columns: list[str] = []
    from .. import trino_client                              # type: ignore
    try:
        result = await trino_client.query(q)
        columns = result.get("columns") or []
        rows    = (result.get("rows") or [])[:body.limit]
        # masked columns arriving through SELECT *: transform per mask_class
        if masked_classes:
            present = [n for n in masked_classes if n in columns]
            if present:
                rows, _dropped = masking.apply_masking(
                    rows, {n: cc for n, cc in masked_classes.items() if cc},
                    present)
                columns = [c for c in columns if c not in set(_dropped)]
    except Exception as exc:                                # noqa: BLE001
        http_status = 400
        err = str(exc)[:600]
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    # 4. Audit
    table_scope = [f"{s}.{t}" for s, t in referenced] or ["sql_lab"]
    await ops_db.log_gateway_query(
        user=user, method="POST", path="/api/sql/execute",
        mart_scope=table_scope, filters={"sql_preview": q[:200]},
        row_count=len(rows), byte_count=None,
        latency_ms=elapsed_ms, http_status=http_status,
        policy_decision="allow" if http_status == 200 else "error",
        denied_reason=err)

    if err is not None:
        raise HTTPException(400, err)

    return {
        "columns":     columns,
        "rows":        rows,
        "row_count":   len(rows),
        "elapsed_ms":  elapsed_ms,
        "tables_used": table_scope if referenced else [],
    }
