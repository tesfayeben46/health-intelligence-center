"""mBrana extraction pipeline: pulls every table listed in tables.TABLES.

dlt's `sql_database` helper introspects the source schema via SQLAlchemy and
streams rows into the destination. We drive it explicitly with the table list
instead of auto-discovery (see tables.py for why).
"""
from __future__ import annotations

import logging
import time
from typing import Iterable

import dlt
from dlt.sources.sql_database import sql_table

from ..config import Config
from ..destination import build_destination
from ..manifest import build_manifest, measure_partition, write_manifest
from ..lineage import emit_source_lineage
from .tables import TABLES, TableSpec

log = logging.getLogger(__name__)


def _resources(cfg: Config, specs: Iterable[TableSpec]):
    """Build one dlt resource per table spec."""
    url = cfg.sqlalchemy_url()
    for spec in specs:
        # Full-replace each run (see HCMISWDM extract.py for the rationale): `replace`
        # truncates this table's lake prefix and rewrites it, so runs are idempotent,
        # duplicate-free and current. No merge/incremental: merge silently degrades to
        # append on the parquet filesystem destination, and replace+incremental would
        # drop old rows. Bound a large table with append + primary_key + incremental.
        res = sql_table(
            credentials=url,
            schema=spec.schema,
            table=spec.table,
        )
        res.apply_hints(table_name=spec.dlt_name, write_disposition="replace")
        yield res


def run(cfg: Config, *, only: list[str] | None = None) -> dict:
    """Execute the pipeline. `only` filters TABLES by dlt_name; None = all."""
    specs = [t for t in TABLES if not only or t.dlt_name in only]
    if not specs:
        raise RuntimeError(f"No tables matched `only`={only!r}")

    destination, placeholders, started_at, run_id = build_destination(cfg)
    pipeline = dlt.pipeline(
        pipeline_name=cfg.dlt_pipeline_name,
        destination=destination,
        dataset_name=placeholders["source"],
        progress="log",
    )

    t0 = time.time()
    load_info = pipeline.run(list(_resources(cfg, specs)), loader_file_format="parquet")
    elapsed = time.time() - t0

    tables: set[str] = set()
    for pkg in load_info.load_packages:
        for job in pkg.jobs.get("completed_jobs", []):
            tn = job.job_file_info.table_name
            if not tn.startswith("_dlt"):
                tables.add(tn)

    completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest_paths = []
    for table_name in sorted(tables):
        file_count, bytes_written = measure_partition(cfg, table_name=table_name, placeholders=placeholders)
        manifest_paths.append(write_manifest(
            cfg,
            table_name=table_name,
            placeholders=placeholders,
            payload=build_manifest(
                run_id=run_id,
                source=placeholders["source"],
                object_name=table_name,
                started_at=started_at,
                completed_at=completed_at,
                row_count=0,
                file_count=file_count,
                bytes_written=bytes_written,
            ),
        ))
        emit_source_lineage(source=placeholders["source"], table=table_name)   # best-effort catalog hop (no-op unless OM_LINEAGE_* set)
    return {
        "run_id": run_id,
        "tables": sorted(tables),
        "manifests": manifest_paths,
        "elapsed_seconds": elapsed,
    }
