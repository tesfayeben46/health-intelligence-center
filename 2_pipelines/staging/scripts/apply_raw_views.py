#!/usr/bin/env python3
"""apply_raw_views.py: the ch-raw-views Job, fully in-cluster.

Owns the whole raw-view surface with no host-side orchestration:

  1. Ensures the ClickHouse databases exist (nhdw_staging, nhdw_marts).
  2. Drops every existing raw_*/stg_* view in nhdw_staging. Model removal is
     first-class (neither CREATE OR REPLACE nor dbt drops a view whose model
     was retired; a stale view over an empty s3 path aborts the bridge).
  3. Probes the lake through ClickHouse itself (`SELECT _path FROM
     s3(nhdw_minio, ..., format='One')`, which lists matched files without reading
     data) to learn which source tables actually hold Parquet.
  4. DESCRIBEs each populated table to learn its real columns (a real dlt
     ingestor drops a column that is all-NULL across the source, e.g. DHIS2
     org-units' closed_date).
  5. Applies raw_views/raw_views.sql resiliently:
       - populated table            -> the canonical s3(nhdw_minio, ...) view,
         with any staging-expected-but-dropped column NULL-patched in;
       - absent/empty table         -> an empty typed view (columns parsed from
         the staging model's src CTE) so the pipeline flows to 0-row marts
         instead of aborting on CANNOT_EXTRACT_TABLE_STRUCTURE.

Credentials: none handled here. The s3() calls resolve the server-side
`nhdw_minio` named collection (mounted into ClickHouse from a Secret), so this
job needs only the ClickHouse login (CH_* env from clickhouse-secrets).

Runs inside the nhdw/staging-dbt image (dbt-clickhouse ships clickhouse_connect;
the staging models this script parses are baked in at /dbt/models).
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

import clickhouse_connect

HERE = pathlib.Path(__file__).resolve().parent
RAW_SQL = HERE.parent / "raw_views" / "raw_views.sql"
MODELS_DIR = pathlib.Path(os.environ.get("STAGING_MODELS_DIR", "/dbt/models"))

CH_HOST = os.environ.get("CH_HOST", "clickhouse.nhdw.svc.cluster.local")
CH_PORT = int(os.environ.get("CH_PORT", "8123"))
CH_USER = os.environ.get("CH_USER", "nhdw")
CH_PASSWORD = os.environ["CH_PASSWORD"]

_PATH = re.compile(r"nhdw-raw/([a-z0-9_]+)/([a-z0-9_]+)/")
_VIEW = re.compile(r"CREATE OR REPLACE VIEW\s+(\w+)")
_URL = re.compile(r"url\s*=\s*'([^']+)'")


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def staging_cols(source: str, table: str) -> list[str] | None:
    """Columns the staging model selects from source('raw', '<source>__<table>')."""
    f = MODELS_DIR / source / f"stg_{source}__{table}.sql"
    if not f.exists():
        return None
    m = re.search(r"SELECT\s+(.*?)\s+FROM\s*\{\{\s*source\('raw'", f.read_text(), re.S | re.I)
    if not m:
        return None
    cols = []
    for part in m.group(1).split(","):
        tok = part.strip().split()[0] if part.strip() else ""
        tok = tok.split(".")[-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tok):
            cols.append(tok)
    return cols or None


def main() -> int:
    raw_text = RAW_SQL.read_text()

    # Derive the lake endpoint from the canonical SQL itself (single source of truth).
    first_url = _URL.search(raw_text)
    if not first_url:
        print("FATAL: no s3 url found in raw_views.sql", file=sys.stderr)
        return 2
    lake_base = first_url.group(1).split("/nhdw-raw/")[0] + "/nhdw-raw"

    admin = clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT, username=CH_USER, password=CH_PASSWORD)

    # 1. databases
    admin.command("CREATE DATABASE IF NOT EXISTS nhdw_staging "
                  "COMMENT 'Ephemeral staging layer: views over object-store Parquet. "
                  "Managed by 2_pipelines/staging/.'")
    admin.command("CREATE DATABASE IF NOT EXISTS nhdw_marts "
                  "COMMENT 'Denormalised aggregates fed from the Postgres vault.'")
    log("databases ensured (nhdw_staging, nhdw_marts)")

    ch = clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT, username=CH_USER, password=CH_PASSWORD,
        database="nhdw_staging")

    # CH 24.3 + named-collection s3(): the server-wide schema-inference cache
    # can return table A's schema for table B (key omits the full URL; see
    # build_processed_zone.py). Unpoison it before
    # the views (which re-infer at every SELECT) and bypass it on DESCRIBEs.
    try:
        ch.command("SYSTEM DROP SCHEMA CACHE FOR S3")
        log("s3 schema-inference cache dropped")
    except Exception as exc:
        log(f"[resilient] could not drop s3 schema cache ({exc})")

    # 2. drop stale views: retired models must not linger
    stale = [r[0] for r in ch.query(
        "SELECT name FROM system.tables WHERE database='nhdw_staging' "
        "AND (name LIKE 'raw_%' OR name LIKE 'stg_%')").result_rows]
    for v in stale:
        ch.command(f"DROP VIEW IF EXISTS nhdw_staging.`{v}`")
    log(f"stale raw_/stg_ views dropped: {len(stale)}")

    # 3. probe the lake through CH (format='One' lists files without reading data)
    rows = ch.query(
        f"SELECT DISTINCT _path FROM s3(nhdw_minio, url = '{lake_base}/**/*.parquet', "
        f"format = 'One')").result_rows
    populated: set[str] = set()
    for (path,) in rows:
        m = _PATH.search(path)
        if m and not m.group(2).startswith("_dlt") and m.group(2) != "init":
            populated.add(f"{m.group(1)}/{m.group(2)}")
    log(f"lake has {len(populated)} populated tables")

    # 4+5. apply each view statement resiliently.
    # Extract complete statements instead of splitting on ';': the file's
    # section banners are comments that may themselves contain ';' (e.g.
    # "-- ╭─ Climate (weekly aggregates; region by ISO-week)"), and a naive
    # split would start a chunk mid-comment -> SYNTAX_ERROR.
    statements = re.findall(r"CREATE OR REPLACE VIEW\s+\w+\s+AS\s.*?;", raw_text, re.S)
    n_ok = n_patched = n_empty = 0
    for stmt in statements:
        vm = _VIEW.search(stmt)
        um = _URL.search(stmt)
        pm = _PATH.search(stmt)
        if not (vm and um and pm):
            continue
        view, source, table = vm.group(1), pm.group(1), pm.group(2)
        key = f"{source}/{table}"

        if key in populated:
            want = staging_cols(source, table) or []
            actual = [r[0] for r in ch.query(
                f"DESCRIBE s3(nhdw_minio, url = '{um.group(1)}', format = 'Parquet') "
                f"SETTINGS schema_inference_use_cache_for_s3 = 0"
            ).result_rows]
            missing = [c for c in want if c not in actual]
            if missing:
                add = ", ".join(f"CAST(NULL AS Nullable(String)) AS {c}" for c in missing)
                stmt = re.sub(r"SELECT\s+\*\s+FROM\s+s3\(", f"SELECT *, {add} FROM s3(",
                              stmt, count=1)
                log(f"[resilient] {view}: PATCHED {missing} (ingestor dropped all-NULL)")
                n_patched += 1
            else:
                n_ok += 1
            ch.command(stmt)
        else:
            cols = staging_cols(source, table)
            if cols:
                sel = ", ".join(f"CAST(NULL AS Nullable(String)) AS {c}" for c in cols)
            else:
                sel = "CAST(NULL AS Nullable(String)) AS _empty"
            ch.command(f"CREATE OR REPLACE VIEW {view} AS SELECT {sel} WHERE 1=0")
            log(f"[resilient] {view}: EMPTY typed view ({len(cols or [1])} cols); source table absent")
            n_empty += 1

    log(f"raw views applied: {n_ok} direct · {n_patched} patched · {n_empty} empty-typed")
    print("ch-raw-views complete", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
