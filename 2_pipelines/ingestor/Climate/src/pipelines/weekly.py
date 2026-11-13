"""Climate weekly pipeline: pull weekly meteorological CSV from the external
Climate API into the lake as Parquet.

INCREMENTAL: dlt tracks `week_start` as the cursor and persists it; each run sends
the last watermark to the API as `?updated_since=` so only newer weeks are pulled.
Full-refresh fallback: unset the state (or CLIMATE_INITIAL_UPDATED_SINCE empty).
"""
from __future__ import annotations

import csv
import io
import logging
import time
import urllib.parse
import urllib.request

import dlt

from ..config import Config
from ..destination import build_destination
from ..manifest import build_manifest, measure_partition, write_manifest
from ..lineage import emit_source_lineage

log = logging.getLogger("climate-ingestor.weekly")

_NUMERIC = ("rainfall_mm", "temp_celsius", "humidity_pct")


def _fetch_csv(cfg: Config, updated_since: str | None, offset: int, limit: int) -> str:
    params = {"offset": offset, "limit": limit}
    if updated_since:
        params["updated_since"] = updated_since
    url = cfg.climate_api_base_url.rstrip("/") + "/api/v1/climate/weekly?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "text/csv"})
    with urllib.request.urlopen(req, timeout=cfg.climate_timeout) as r:
        return r.read().decode("utf-8")


@dlt.resource(
    name="weekly",
    write_disposition="append",
    primary_key=("region_id", "iso_week"),
    columns={
        "rainfall_mm": {"data_type": "double"},
        "temp_celsius": {"data_type": "double"},
        "humidity_pct": {"data_type": "double"},
    },
)
def weekly(cfg: Config,
           cursor=dlt.sources.incremental("week_start", initial_value=None)):
    """Page the Climate API, yielding one dict per weekly observation."""
    since = cursor.last_value or (cfg.climate_initial_updated_since or None)
    offset, total = 0, 0
    while True:
        text = _fetch_csv(cfg, since, offset, cfg.climate_page_size)
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            break
        for row in rows:
            for k in _NUMERIC:
                v = row.get(k)
                row[k] = float(v) if v not in (None, "") else None
            yield row
            total += 1
            if cfg.climate_dev_max_rows and total >= cfg.climate_dev_max_rows:
                log.info("dev cap %d reached", cfg.climate_dev_max_rows)
                return
        if len(rows) < cfg.climate_page_size:
            break
        offset += cfg.climate_page_size


def run(cfg: Config) -> dict:
    destination, placeholders, started_at, run_id = build_destination(cfg)
    pipeline = dlt.pipeline(
        pipeline_name=f"{cfg.dlt_pipeline_name}_weekly",
        destination=destination,
        dataset_name=placeholders["source"],
        progress="log",
    )
    t0 = time.time()
    load_info = pipeline.run(weekly(cfg), loader_file_format="parquet")
    log.info("pipeline complete in %.1fs", time.time() - t0)

    tables: set[str] = set()
    for pkg in load_info.load_packages:
        for job in pkg.jobs.get("completed_jobs", []):
            tables.add(job.job_file_info.table_name)

    completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest_paths: list[str] = []
    for table_name in sorted(tables):
        if table_name.startswith("_dlt"):
            continue
        file_count, bytes_written = measure_partition(cfg, table_name=table_name, placeholders=placeholders)
        payload = build_manifest(
            run_id=run_id, source=placeholders["source"], object_name=table_name,
            started_at=started_at, completed_at=completed_at,
            row_count=0, file_count=file_count, bytes_written=bytes_written,
        )
        manifest_paths.append(write_manifest(cfg, table_name=table_name, placeholders=placeholders, payload=payload))
        emit_source_lineage(source=placeholders["source"], table=table_name)   # best-effort catalog hop (no-op unless OM_LINEAGE_* set)

    return {"run_id": run_id, "tables": sorted(tables), "manifests": manifest_paths}
