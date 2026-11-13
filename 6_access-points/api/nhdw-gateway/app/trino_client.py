"""Trino client used by every mart-read route.

The `trino` driver is synchronous, so calls run in a thread executor.

A statement allowlist admits only statements that start with SELECT, SHOW,
DESCRIBE, EXPLAIN or WITH. Free-form input from the Workshop SQL editor goes
through the same check.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from trino import dbapi as trino_dbapi
from trino.auth import BasicAuthentication

from .settings import settings

log = logging.getLogger("gateway.trino")

_ALLOWED_RE = re.compile(r"^\s*(SELECT|SHOW|DESCRIBE|EXPLAIN|WITH)\b", re.IGNORECASE)


class TrinoRejected(Exception):
    """Statement disallowed by the gateway-side SQL allowlist."""


def _connect() -> Any:
    return trino_dbapi.connect(
        host=settings.TRINO_HOST,
        port=settings.TRINO_PORT,
        user=settings.TRINO_USER,
        catalog="marts",
        schema="information_schema",
        request_timeout=30,
        session_properties={
            # dbt materialises aggregate mart columns as unparameterised NUMERIC,
            # and the PostgreSQL connector's default decimal-mapping=STRICT
            # omits such columns from table metadata (amc, mos, coverage_pct,
            # ...). Map them to decimal(38,4) so every mart read can see them.
            # The `ops` catalog needs the same mapping: ops_ml.ml_predictions'
            # pred_cases_* and alarm_* columns are unparameterised NUMERIC, and
            # the ML-fed dz-* fetchers join marts and ops across catalogs.
            "marts.decimal_mapping": "ALLOW_OVERFLOW",
            "marts.decimal_default_scale": "4",
            "marts.decimal_rounding_mode": "HALF_UP",
            "ops.decimal_mapping": "ALLOW_OVERFLOW",
            "ops.decimal_default_scale": "4",
            "ops.decimal_rounding_mode": "HALF_UP",
        },
    )


def _check_allowed(sql: str) -> None:
    if not _ALLOWED_RE.match(sql or ""):
        raise TrinoRejected("only SELECT / SHOW / DESCRIBE / EXPLAIN / WITH are allowed")


async def query(sql: str, params: list[Any] | None = None,
                catalog: str | None = None, schema: str | None = None) -> dict[str, Any]:
    """Run a query through Trino; return {columns, rows, row_count}.

    `params` are positional `?` substitutions (Trino DBAPI style)."""
    _check_allowed(sql)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _run_blocking, sql, params, catalog, schema)


def _run_blocking(sql: str, params: list[Any] | None,
                  catalog: str | None, schema: str | None) -> dict[str, Any]:
    conn = _connect()
    try:
        cur = conn.cursor()
        if catalog or schema:
            cur.execute(
                f"USE {catalog or 'marts'}.{schema or 'information_schema'}")
            cur.fetchall()
        cur.execute(sql, params or None)
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        return {
            "columns": cols,
            "rows":    [dict(zip(cols, r)) for r in rows],
            "row_count": len(rows),
        }
    finally:
        conn.close()


async def fetch_mart(*, catalog: str = "marts", schema: str, table: str,
                    columns: list[str] | None = None,
                    where: dict[str, Any] | None = None,
                    order_by: list[str] | None = None,
                    limit: int | None = 500) -> dict[str, Any]:
    """Build and run the parameterised SELECT used by /api/marts/<name>.

    Filter values are bound as parameters. `catalog`, `schema`, `columns` and
    `order_by` are interpolated, so callers must pass validated identifiers."""
    cols = ", ".join(columns) if columns else "*"
    sql = f'SELECT {cols} FROM {catalog}.{schema}."{table}"'
    params: list[Any] = []
    if where:
        parts: list[str] = []
        for k, v in where.items():
            if isinstance(v, list):
                parts.append(f'"{k}" IN ({", ".join(["?"]*len(v))})')
                params.extend(v)
            else:
                parts.append(f'"{k}" = ?')
                params.append(v)
        if parts:
            sql += " WHERE " + " AND ".join(parts)
    if order_by:
        sql += " ORDER BY " + ", ".join(order_by)
    if limit:
        sql += f" LIMIT {int(limit)}"
    return await query(sql, params)
