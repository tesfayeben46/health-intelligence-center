#!/usr/bin/env python3
"""Contract test for upsert_column_classification (load_security_seed.py).

Needs a disposable Postgres (TEST_DSN env). Creates ops_perm.column_classification
from the init DDL itself (the block is extracted from 17_perm_schemas.sql, so
drift between the DDL and the loader shows up here) and asserts the loader's
provenance contract:

  1. seed rows load with managed_by='seed'
  2. re-run is idempotent
  3. a steward-authored row survives the seed reconciliation
  4. a seed row dropped from the YAML is deleted; steward rows stay
  5. an empty YAML clears only seed rows
  6. a steward row for a key the seed also carries is never overwritten

It also pins the psycopg3-safe NOT-IN delete-sync: a psycopg2-only tuple idiom
fails under psycopg3 and rolls back the whole app/security seed.

Run:  TEST_DSN=postgresql://... REPO_ROOT=<repo> python3 test_column_classification_loader.py
"""
from __future__ import annotations

import os
import sys

import psycopg

sys.path.insert(0, os.path.dirname(__file__))
from load_security_seed import upsert_column_classification  # noqa: E402

ROWS = [
    {"schema_name": "bi_supply", "table_name": "mart_ct_nodes", "column_name": "lat",
     "sensitivity": "restricted", "mask_class": "generalize", "pii": False},
    {"schema_name": "bi_supply", "table_name": "mart_ct_nodes", "column_name": "lng",
     "sensitivity": "restricted", "mask_class": "generalize", "pii": False},
    {"schema_name": "bi_staging", "table_name": "obs", "column_name": "patient_id",
     "sensitivity": "privileged", "mask_class": "tokenize", "pii": True},
]

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def ddl_block(repo: str) -> str:
    """The column_classification portion of the real init DDL."""
    src = open(os.path.join(repo, "3_storage/postgres/init/17_perm_schemas.sql")).read()
    start = src.index("CREATE TABLE IF NOT EXISTS ops_perm.column_classification")
    # through the index that follows the table (grants need live roles, so skipped)
    end = src.index(";", src.index("ix_column_classification", start)) + 1
    return src[start:end]


def counts(cur) -> dict[str, int]:
    cur.execute("SELECT managed_by, count(*) FROM ops_perm.column_classification GROUP BY 1")
    return dict(cur.fetchall())


def main() -> int:
    repo = os.environ.get("REPO_ROOT") or os.path.join(os.path.dirname(__file__), "..", "..")
    conn = psycopg.connect(os.environ["TEST_DSN"])
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS ops_perm CASCADE; CREATE SCHEMA ops_perm;")
    cur.execute(ddl_block(repo))
    conn.commit()

    n = upsert_column_classification(cur, ROWS)
    conn.commit()
    check("initial load", n == 3 and counts(cur) == {"seed": 3}, str(counts(cur)))

    upsert_column_classification(cur, ROWS)
    conn.commit()
    check("idempotent re-run", counts(cur) == {"seed": 3}, str(counts(cur)))

    cur.execute("""INSERT INTO ops_perm.column_classification
                     (schema_name, table_name, column_name, sensitivity, mask_class, managed_by)
                   VALUES ('bi_x', 't', 'c', 'restricted', 'hash', 'steward')""")
    conn.commit()
    upsert_column_classification(cur, ROWS)
    conn.commit()
    check("steward row survives reconciliation",
          counts(cur) == {"seed": 3, "steward": 1}, str(counts(cur)))

    upsert_column_classification(cur, ROWS[:-1])
    conn.commit()
    check("dropped seed row deleted, steward kept",
          counts(cur) == {"seed": 2, "steward": 1}, str(counts(cur)))

    upsert_column_classification(cur, [])
    conn.commit()
    check("empty YAML clears only seed rows", counts(cur) == {"steward": 1}, str(counts(cur)))

    cur.execute("""UPDATE ops_perm.column_classification
                     SET sensitivity='privileged' WHERE managed_by='steward'""")
    conn.commit()
    upsert_column_classification(
        cur, [{"schema_name": "bi_x", "table_name": "t", "column_name": "c",
               "sensitivity": "restricted", "mask_class": "drop", "pii": False}])
    conn.commit()
    cur.execute("SELECT sensitivity, managed_by FROM ops_perm.column_classification "
                "WHERE schema_name='bi_x'")
    row = cur.fetchone()
    check("seed never clobbers a steward-owned key",
          row == ("privileged", "steward"), str(row))

    print("PASS" if not FAILS else f"FAIL: {FAILS}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
