"""Write _manifest.json sidecars next to the Parquet parts.

A manifest sits at:
    s3://nhdw-raw/openlmis/<table>/year=YYYY/month=MM/day=DD/_manifest.json

Downstream layers (dbt, data-quality jobs, observability) read this shape;
keep it stable. Same code as the DHIS2 ingestor's manifest.py; each ingestor
carries its own copy instead of sharing a module.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

import dlt
import s3fs

from .config import Config


def _fs(cfg: Config) -> s3fs.S3FileSystem:
    return s3fs.S3FileSystem(
        key=cfg.minio_access_key,
        secret=cfg.minio_secret_key,
        client_kwargs={"endpoint_url": cfg.minio_endpoint, "region_name": cfg.minio_region},
    )


def measure_partition(cfg: Config, *, table_name: str, placeholders: Mapping[str, str]) -> tuple[int, int]:
    """List the freshly-written Parquet parts and return (file_count, bytes_written)."""
    prefix = (
        f"{cfg.minio_raw_bucket}/{placeholders['source']}/{table_name}/"
        f"year={placeholders['year']}/month={placeholders['month']}/day={placeholders['day']}/"
    )
    fs = _fs(cfg)
    try:
        entries = fs.ls(prefix, detail=True)
    except FileNotFoundError:
        return 0, 0
    file_count = 0
    bytes_written = 0
    for e in entries:
        name = e.get("Key") or e.get("name") or ""
        if name.endswith(".parquet"):
            file_count += 1
            bytes_written += int(e.get("Size") or e.get("size") or 0)
    return file_count, bytes_written


def write_manifest(
    cfg: Config,
    *,
    table_name: str,
    placeholders: Mapping[str, str],
    payload: Mapping[str, Any],
) -> str:
    """Write a single _manifest.json sidecar; return the S3 path."""
    path = (
        f"s3://{cfg.minio_raw_bucket}/{placeholders['source']}/{table_name}/"
        f"year={placeholders['year']}/month={placeholders['month']}/day={placeholders['day']}/"
        f"_manifest.json"
    )
    body = json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8")
    with _fs(cfg).open(path, "wb") as f:
        f.write(body)
    return path


def build_manifest(
    *,
    run_id: str,
    source: str,
    object_name: str,
    started_at: str,
    completed_at: str,
    row_count: int,
    file_count: int,
    bytes_written: int,
    watermark_used: str | None = None,
    watermark_advanced_to: str | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "source": source,
        "object": object_name,
        "watermark_used": watermark_used,
        "watermark_advanced_to": watermark_advanced_to,
        "row_count": row_count,
        "file_count": file_count,
        "bytes_written": bytes_written,
        "dlt_pipeline_version": dlt.version.__version__,
        "started_at": started_at,
        "completed_at": completed_at,
    }
