"""_manifest.json sidecar written next to each run's Parquet parts."""
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
    prefix = (
        f"{cfg.minio_raw_bucket}/{placeholders['source']}/{table_name}/"
        f"year={placeholders['year']}/month={placeholders['month']}/day={placeholders['day']}/"
    )
    fs = _fs(cfg)
    try:
        entries = fs.ls(prefix, detail=True)
    except FileNotFoundError:
        return 0, 0
    file_count, bytes_written = 0, 0
    for e in entries:
        name = e.get("Key") or e.get("name") or ""
        if name.endswith(".parquet"):
            file_count += 1
            bytes_written += int(e.get("Size") or e.get("size") or 0)
    return file_count, bytes_written


def write_manifest(cfg: Config, *, table_name: str, placeholders: Mapping[str, str], payload: Mapping[str, Any]) -> str:
    path = (
        f"s3://{cfg.minio_raw_bucket}/{placeholders['source']}/{table_name}/"
        f"year={placeholders['year']}/month={placeholders['month']}/day={placeholders['day']}/"
        f"_manifest.json"
    )
    body = json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8")
    with _fs(cfg).open(path, "wb") as f:
        f.write(body)
    return path


def build_manifest(*, run_id, source, object_name, started_at, completed_at, row_count, file_count, bytes_written, watermark_used=None, watermark_advanced_to=None) -> dict:
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
