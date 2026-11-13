"""EthioEMR extraction pipeline: direct MySQL -> FHIR-shaped Parquet.

Reads the facility OpenMRS/Bahmni EMR straight from MySQL (person/patient/
patient_identifier/encounter/visit/obs) and reshapes each row into the FHIR R4
resource shape the downstream staging + vault expect. dlt flattens the nested
dicts into the exact `meta__last_updated` / `value_quantity__value` / ...
columns the models parse.

Tables produced, one per FHIR resource type in cfg.mysql_resources:
    s3://nhdw-raw/ethioemr/<resource>/year=YYYY/month=MM/day=DD/...
      patient / encounter / observation
"""
from __future__ import annotations

import logging
import time

import dlt

from ..config import Config
from ..destination import build_destination
from ..manifest import build_manifest, measure_partition, write_manifest
from ..lineage import emit_source_lineage
from ._mysql import OpenMRSReader

log = logging.getLogger(__name__)

# FHIR resource type -> (dlt table name, reader method name). The table name is
# the source of truth for the lake path and the raw ClickHouse view.
_RESOURCE_READERS = {
    "Patient": ("patient", "iter_patients"),
    "Encounter": ("encounter", "iter_encounters"),
    "Observation": ("observation", "iter_observations"),
}


def _resources(reader: OpenMRSReader, cfg: Config):
    """One dlt resource per configured FHIR type, backed by a MySQL stream."""
    for resource_type in cfg.mysql_resources:
        mapping = _RESOURCE_READERS.get(resource_type)
        if mapping is None:
            log.warning("skipping unsupported resource type %s", resource_type)
            continue
        table, method_name = mapping
        method = getattr(reader, method_name)

        # Incremental on the FHIR `meta.lastUpdated` (= coalesce(date_changed,
        # date_created)); dlt restores this watermark from the lake on each run, so
        # a re-run appends only rows strictly newer than the last one seen. A
        # constant floor would re-append every row on each run and the ClickHouse
        # s3() view would double-count.
        #
        # `primary_key=()` disables dlt's boundary-row hash dedup: the
        # synthetic EMR bulk-inserts share a handful of timestamps, so a large share
        # of a table sits at the cursor boundary and dlt would hash every one of
        # those rows into its dedup state, far slower than plain streaming.
        # Duplicate safety comes from the SQL side instead: the readers compare with
        # a strict `>` on re-runs, so boundary rows are never re-read. New rows are
        # inserted with NOW(), strictly newer than any stored boundary, so nothing
        # is missed.
        @dlt.resource(name=table, write_disposition="append")
        def _gen(_method=method, _rt=resource_type,
                 updated=dlt.sources.incremental("meta.lastUpdated",
                                                 initial_value=cfg.mysql_initial_cursor,
                                                 primary_key=())):
            since = updated.last_value or cfg.mysql_initial_cursor
            first_run = since == cfg.mysql_initial_cursor
            # last_value is the FHIR instant we emitted ("...T...Z"); normalise it to a
            # MySQL-comparable datetime literal before it hits the WHERE clause.
            since_sql = since.replace("T", " ").rstrip("Z") if "T" in since else since
            log.info("reading %s from MySQL with cursor %s '%s' (incremental meta.lastUpdated)",
                     _rt, ">=" if first_run else ">", since_sql)
            n = 0
            # First run: >= the configured floor (inclusive: catch everything).
            # Re-runs: strictly > the stored boundary (exclusive: no re-reads).
            for row in _method(since_sql, strict=not first_run):
                n += 1
                yield row
            log.info("  emitted %d %s rows", n, _rt)

        yield _gen()


def run(cfg: Config) -> dict:
    reader = OpenMRSReader(cfg)
    destination, placeholders, started_at, run_id = build_destination(cfg)
    pipeline = dlt.pipeline(
        pipeline_name=cfg.dlt_pipeline_name,
        destination=destination,
        dataset_name=placeholders["source"],
        progress="log",
    )

    t0 = time.time()
    load_info = pipeline.run(list(_resources(reader, cfg)), loader_file_format="parquet")
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
                watermark_used=cfg.mysql_initial_cursor,
            ),
        ))
        emit_source_lineage(source=placeholders["source"], table=table_name)   # best-effort catalog hop (no-op unless OM_LINEAGE_* set)
    return {
        "run_id": run_id,
        "tables": sorted(tables),
        "manifests": manifest_paths,
        "elapsed_seconds": elapsed,
    }
