"""CH->PG bridge: copy every `nhdw_staging.stg_*` view into `nhdw_landing.stg_*`.

Pattern: Persistent Staging Area (PSA). Each batch:
  1. Lists every stg_* view in nhdw_staging on ClickHouse.
  2. For each one:
       a. Pulls the column metadata (name + CH type) from system.columns.
       b. Maps CH types -> PG types.
       c. Issues CREATE TABLE IF NOT EXISTS in nhdw_landing.
       d. TRUNCATEs the PG table (idempotent reload).
       e. Streams the rows via clickhouse-driver -> psycopg2 execute_values
          in batches of 50k.
  3. Logs row counts per table.

Why TRUNCATE+COPY (and not append): the vault is hash-diff-based: it inserts
into hubs/links/sats only when the natural key or attribute hash changes vs
what's already there. A fresh full landing snapshot every run is the simplest
contract; the vault loaders decide what to materialise from it.

Run from inside the bridge container:
    python sync.py
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from typing import Iterable

import psycopg2
from clickhouse_driver import Client
from psycopg2.extras import execute_values

from lineage import emit_bridge_lineage

log = logging.getLogger("bridge")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

# ClickHouse -> Postgres type mapping
# CH parquet-derived types we actually see in stg_*. Anything not listed
# falls back to TEXT: safe but loses type info.
CH_TO_PG = {
    "String":            "TEXT",
    "FixedString":       "TEXT",
    "UUID":              "TEXT",
    "Int8":              "SMALLINT",
    "Int16":             "SMALLINT",
    "Int32":             "INTEGER",
    "Int64":             "BIGINT",
    "UInt8":             "SMALLINT",
    "UInt16":            "INTEGER",
    "UInt32":            "BIGINT",
    "UInt64":            "NUMERIC(20)",
    "Float32":           "REAL",
    "Float64":           "DOUBLE PRECISION",
    "Bool":              "BOOLEAN",
    "Date":              "DATE",
    "Date32":            "DATE",
    "DateTime":          "TIMESTAMPTZ",
    "DateTime64":        "TIMESTAMPTZ",
    "Decimal":           "NUMERIC",
}

NULLABLE_RE = re.compile(r"^Nullable\((.+)\)$")
DT64_RE = re.compile(r"^DateTime64\(.+\)$")
DT_RE = re.compile(r"^DateTime(\(.+\))?$")
DEC_RE = re.compile(r"^Decimal\(\d+,\s*\d+\)$")


def _strip_nullable(ch_type: str) -> tuple[str, bool]:
    m = NULLABLE_RE.match(ch_type)
    if m:
        return m.group(1), True
    return ch_type, False


def _ch_to_pg(ch_type: str) -> str:
    inner, _nullable = _strip_nullable(ch_type)
    # CH's DateTime64(6, 'UTC') etc.: collapse to base
    if DT64_RE.match(inner):
        return "TIMESTAMPTZ"
    if DT_RE.match(inner):
        return "TIMESTAMPTZ"
    if DEC_RE.match(inner):
        return inner.replace("Decimal", "NUMERIC")
    base = inner.split("(")[0]
    return CH_TO_PG.get(base, "TEXT")


def list_stg_views(ch: Client) -> list[str]:
    rows = ch.execute(
        "SELECT name FROM system.tables WHERE database='nhdw_staging' "
        "AND name LIKE 'stg_%' ORDER BY name"
    )
    return [r[0] for r in rows]


def columns_for(ch: Client, view: str) -> list[tuple[str, str]]:
    rows = ch.execute(
        "SELECT name, type FROM system.columns "
        "WHERE database='nhdw_staging' AND table=%(t)s ORDER BY position",
        {"t": view},
    )
    return [(r[0], r[1]) for r in rows]


def ensure_table(pg, schema: str, table: str, cols: list[tuple[str, str]]) -> None:
    """DROP + CREATE: the bridge owns its landing tables.

    Rebuilding each table from the current ClickHouse staging shape makes every
    sync a self-contained full snapshot with the correct types, whatever ran
    before. CREATE TABLE IF NOT EXISTS would keep a leftover table's old column
    types and coerce new data into them, breaking downstream vault casts.
    Non-bridge tables (stg_mfr__* and the program-demo tables) are never in the
    CH stg_* list, so this DROP never touches them."""
    col_defs = []
    for name, ch_type in cols:
        col_defs.append(f'  "{name}" {_ch_to_pg(ch_type)}')
    ddl = (
        f'DROP TABLE IF EXISTS "{schema}"."{table}" CASCADE;\n'
        f'CREATE TABLE "{schema}"."{table}" (\n'
        + ",\n".join(col_defs)
        + "\n);"
    )
    with pg.cursor() as cur:
        cur.execute(ddl)
    pg.commit()


def reset_and_copy(
    ch: Client, pg, schema: str, table: str, cols: list[tuple[str, str]], batch: int
) -> int:
    col_names = [c[0] for c in cols]
    col_list = ", ".join(f'"{c}"' for c in col_names)
    quoted_table = f'"{schema}"."{table}"'

    with pg.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {quoted_table}")
    pg.commit()

    total = 0
    settings = {"max_block_size": batch}
    # stream_blocks yields per-block lists; we feed them straight into PG.
    stream = ch.execute_iter(
        f"SELECT {col_list} FROM nhdw_staging.{table}",
        settings=settings,
    )

    buffer: list[tuple] = []
    flush_size = batch
    insert_sql = f"INSERT INTO {quoted_table} ({col_list}) VALUES %s"
    with pg.cursor() as cur:
        for row in stream:
            buffer.append(tuple(row))
            if len(buffer) >= flush_size:
                execute_values(cur, insert_sql, buffer, page_size=flush_size)
                total += len(buffer)
                buffer.clear()
        if buffer:
            execute_values(cur, insert_sql, buffer, page_size=flush_size)
            total += len(buffer)
    pg.commit()
    return total


def main() -> int:
    ch = Client(
        host=os.environ["CH_HOST"],
        port=int(os.environ.get("CH_PORT", "9000")),
        user=os.environ["CH_USER"],
        password=os.environ["CH_PASSWORD"],
        database="nhdw_staging",
        connect_timeout=10,
        send_receive_timeout=1800,
    )
    pg = psycopg2.connect(
        host=os.environ["PG_HOST"],
        port=int(os.environ.get("PG_PORT", "5432")),
        user=os.environ["PG_USER"],
        password=os.environ["PG_PASSWORD"],
        dbname=os.environ["PG_DATABASE"],
        connect_timeout=10,
    )

    schema = os.environ.get("PG_SCHEMA", "nhdw_landing")
    batch = int(os.environ.get("BATCH_SIZE", "50000"))

    views = list_stg_views(ch)
    log.info("found %d stg_* views to sync", len(views))

    summary: list[tuple[str, int, float]] = []
    for v in views:
        t0 = time.time()
        cols = columns_for(ch, v)
        ensure_table(pg, schema, v, cols)
        n = reset_and_copy(ch, pg, schema, v, cols, batch)
        dt = time.time() - t0
        summary.append((v, n, dt))
        log.info("  %-46s %8d rows  %.1fs", v, n, dt)
        emit_bridge_lineage(table=v, schema=schema)   # best-effort catalog hop (no-op unless OM_LINEAGE_* set)

    total_rows = sum(s[1] for s in summary)
    total_dt = sum(s[2] for s in summary)
    log.info("sync done; %d tables, %d rows total, %.1fs wall", len(summary), total_rows, total_dt)

    # ANALYZE what we just loaded: these tables are DROP+CREATEd every sync so
    # they arrive stats-less, and the downstream dbt-vault build then plans
    # nested loops over them, which makes the spine build very slow. The bridge
    # owns its tables, so it owns their stats.
    t0 = time.time()
    with pg.cursor() as cur:
        cur.execute("ANALYZE")
    pg.commit()
    log.info("landing statistics refreshed (ANALYZE, %.1fs)", time.time() - t0)

    ch.disconnect()
    pg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
