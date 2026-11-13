"""Shared run harness for the OpenLMIS pipelines: run dlt resources, then
write the _manifest.json sidecars for every table dlt actually produced."""
from __future__ import annotations

import logging
import time

import dlt

from ..config import Config
from ..destination import build_destination
from ..manifest import build_manifest, measure_partition, write_manifest
from ..lineage import emit_source_lineage

log = logging.getLogger(__name__)


def run_resources(cfg: Config, pipeline_suffix: str, resources: list, source: str = "openlmis") -> dict:
    destination, placeholders, started_at, run_id = build_destination(cfg, source=source)

    pipeline = dlt.pipeline(
        pipeline_name=f"{cfg.dlt_pipeline_name}_{pipeline_suffix}",
        destination=destination,
        dataset_name=placeholders["source"],
        progress="log",
    )

    t0 = time.time()
    load_info = pipeline.run(resources, loader_file_format="parquet")
    elapsed = time.time() - t0
    log.info("pipeline %s complete in %.1fs", pipeline_suffix, elapsed)

    tables: set[str] = set()
    for pkg in load_info.load_packages:
        for job in pkg.jobs.get("completed_jobs", []):
            tn = job.job_file_info.table_name
            if tn.startswith("_dlt"):
                continue
            tables.add(tn)

    completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest_paths: list[str] = []
    for table_name in sorted(tables):
        file_count, bytes_written = measure_partition(cfg, table_name=table_name, placeholders=placeholders)
        payload = build_manifest(
            run_id=run_id,
            source=placeholders["source"],
            object_name=table_name,
            started_at=started_at,
            completed_at=completed_at,
            row_count=0,
            file_count=file_count,
            bytes_written=bytes_written,
        )
        manifest_paths.append(write_manifest(cfg, table_name=table_name, placeholders=placeholders, payload=payload))
        emit_source_lineage(source=placeholders["source"], table=table_name)   # best-effort catalog hop (no-op unless OM_LINEAGE_* set)

    return {
        "run_id": run_id,
        "tables": sorted(tables),
        "manifests": manifest_paths,
        "elapsed_seconds": elapsed,
    }
