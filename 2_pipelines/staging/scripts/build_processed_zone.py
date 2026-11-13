#!/usr/bin/env python3
"""build_processed_zone.py: the processed-zone Job, fully in-cluster.

Produces the PII-stripped copy of the landing zone: every populated
nhdw-raw/<source>/<table> is rewritten to nhdw-processed/<source>/<table>/
data.parquet with the columns named in scripts/pii_policy.yaml removed.
This is the ML raw-access surface: models/analysts that need pre-vault data
read the processed zone, never nhdw-raw.

Design (mirrors apply_raw_views.py; the staging tier owns the lake surface):

  1. Probe the lake through ClickHouse (`format='One'` lists files without
     reading data) to enumerate populated source/tables. New sources are picked
     up automatically once classified.
  2. For each table, load its drop-list from pii_policy.yaml:
       - listed table  -> DESCRIBE actual columns, drop the policy columns
         (+ every _dlt_* bookkeeping column), copy the rest server-side:
         INSERT INTO FUNCTION s3(nhdw_minio, <processed url>)
         SELECT <kept cols> FROM s3(nhdw_minio, <raw url>)
         Data never leaves ClickHouse and the object store; this pod streams nothing.
       - unclassified table -> skipped, loudly. Fail-safe: data nobody has
         classified never reaches the ML-access zone. The summary prints a
         action-required list; add the table to pii_policy.yaml to admit it.
  3. A policy column that no longer exists in the parquet is a WARN (ingestor
     schema drift), never an error; dropping a ghost column is a no-op.

The zone is regenerable by contract (never packed; the bucket wipe happens in
the Job's initContainer so retired tables cannot linger). Writes use
s3_truncate_on_insert so re-runs are idempotent even without the wipe.

Credentials: none handled here. Reads and writes resolve the server-side
`nhdw_minio` named collection (creds only; the bucket lives in the URL), so
this job needs only the ClickHouse login (CH_* env from clickhouse-secrets).

Schema-inference cache hazard: on CH 24.3, s3() calls through a named
collection poison the server-wide schema-inference cache, so a DESCRIBE for
table B can return table A's cached schema (the cache key does not include
the full URL). Unguarded, this job writes all-NULL parquet shaped like
another table (the parquet reader tolerates missing columns). Defenses, all
three deliberate:
  · every schema-sensitive query runs SETTINGS schema_inference_use_cache_for_s3=0
  · the job DROPs the S3 schema cache up front (unpoisons view readers too)
  · after each write, the output parquet's real footer schema is asserted
    equal to the intended keep-list: schema corruption is a hard failure,
    never a silent success.

Runs inside the nhdw/staging-dbt image (clickhouse_connect + PyYAML ship with
dbt-clickhouse/dbt-core; this script and pii_policy.yaml are baked at
/dbt/scripts/ by the image's `COPY . /dbt/`).
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

import clickhouse_connect
import yaml

HERE = pathlib.Path(__file__).resolve().parent
POLICY_FILE = HERE / "pii_policy.yaml"

CH_HOST = os.environ.get("CH_HOST", "clickhouse.nhdw.svc.cluster.local")
CH_PORT = int(os.environ.get("CH_PORT", "8123"))
CH_USER = os.environ.get("CH_USER", "nhdw")
CH_PASSWORD = os.environ["CH_PASSWORD"]
# Same endpoint the raw views use (see raw_views.sql): in-cluster service DNS.
LAKE_BASE = os.environ.get("LAKE_BASE", "http://seaweedfs-s3.nhdw.svc.cluster.local:8333")

_PATH = re.compile(r"nhdw-raw/([a-z0-9_]+)/([a-z0-9_]+)/")


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def main() -> int:
    policy_doc = yaml.safe_load(POLICY_FILE.read_text())
    policy: dict[str, list[str]] = {
        key: (spec or {}).get("drop", []) if isinstance(spec, dict) else (spec or [])
        for key, spec in (policy_doc.get("tables") or {}).items()
    }

    ch = clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT, username=CH_USER, password=CH_PASSWORD)

    # 0. unpoison the server-wide inference cache (see module docstring);
    #    best-effort: the per-query setting below is the real defense.
    try:
        ch.command("SYSTEM DROP SCHEMA CACHE FOR S3")
        log("s3 schema-inference cache dropped")
    except Exception as exc:  # privilege-dependent; queries are immune anyway
        log(f"WARNING: could not drop s3 schema cache ({exc}); relying on per-query bypass")

    NOCACHE = " SETTINGS schema_inference_use_cache_for_s3 = 0"

    # 1. enumerate populated landing-zone tables
    rows = ch.query(
        f"SELECT DISTINCT _path FROM s3(nhdw_minio, url = '{LAKE_BASE}/nhdw-raw/**/*.parquet', "
        f"format = 'One')").result_rows
    populated: set[str] = set()
    for (path,) in rows:
        m = _PATH.search(path)
        if m and not m.group(2).startswith("_dlt") and m.group(2) != "init":
            populated.add(f"{m.group(1)}/{m.group(2)}")
    log(f"landing zone has {len(populated)} populated tables")

    n_copied = n_skipped = 0
    ghost_warns: list[str] = []
    skipped: list[str] = []
    total_rows = 0

    for key in sorted(populated):
        source, table = key.split("/", 1)
        if key not in policy:
            log(f"WARNING: skipped {key}: not in pii_policy.yaml (unclassified data "
                f"never reaches the processed zone)")
            skipped.append(key)
            n_skipped += 1
            continue

        raw_url = f"{LAKE_BASE}/nhdw-raw/{source}/{table}/**/*.parquet"
        out_url = f"{LAKE_BASE}/nhdw-processed/{source}/{table}/data.parquet"

        actual = [r[0] for r in ch.query(
            f"DESCRIBE s3(nhdw_minio, url = '{raw_url}', format = 'Parquet'){NOCACHE}"
        ).result_rows]
        drops = set(policy[key]) | {c for c in actual if c.startswith("_dlt")}
        ghosts = [c for c in policy[key] if c not in actual]
        for g in ghosts:
            ghost_warns.append(f"{key}.{g}")
            log(f"WARNING: {key}: policy drops '{g}' but the column no longer exists "
                f"(ingestor schema drift); no-op")
        keep = [c for c in actual if c not in drops]
        if not keep:
            log(f"WARNING: skipped {key}: policy drops every column")
            skipped.append(key)
            n_skipped += 1
            continue

        cols = ", ".join(f"`{c}`" for c in keep)
        ch.command(
            f"INSERT INTO FUNCTION s3(nhdw_minio, url = '{out_url}', format = 'Parquet') "
            f"SELECT {cols} FROM s3(nhdw_minio, url = '{raw_url}', format = 'Parquet') "
            f"SETTINGS s3_truncate_on_insert = 1, schema_inference_use_cache_for_s3 = 0"
        )
        # hard assertion: the written parquet's real footer schema must be
        # exactly the keep-list (catches any inference-cache regression).
        written = [r[0] for r in ch.query(
            f"DESCRIBE s3(nhdw_minio, url = '{out_url}', format = 'Parquet'){NOCACHE}"
        ).result_rows]
        if set(written) != set(keep):
            print(f"FATAL: {key} written schema != intended keep-list\n"
                  f"  written: {sorted(written)}\n  intended: {sorted(keep)}",
                  file=sys.stderr, flush=True)
            return 2
        n = ch.query(
            f"SELECT count() FROM s3(nhdw_minio, url = '{out_url}', format = 'Parquet'){NOCACHE}"
        ).result_rows[0][0]
        total_rows += n
        dropped_real = sorted(drops & set(actual))
        log(f"{key}: {n} rows · kept {len(keep)} cols · dropped "
            f"{dropped_real if dropped_real else 'nothing'}")
        n_copied += 1

    log(f"processed zone built: {n_copied} tables · {total_rows} rows · "
        f"{n_skipped} skipped · {len(ghost_warns)} ghost-column warns")
    if skipped:
        log("ACTION REQUIRED: add these tables to scripts/pii_policy.yaml (they are "
            "EXCLUDED from the ML-access zone until classified): "
            + ", ".join(skipped))
    print("processed-zone complete", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
