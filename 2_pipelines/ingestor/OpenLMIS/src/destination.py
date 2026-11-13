"""Build the dlt filesystem destination pointed at the data lake (SeaweedFS S3 API).

The layout below produces the canonical path:
    s3://nhdw-raw/openlmis/<table>/year=YYYY/month=MM/day=DD/openlmis_<table>_<ts>_run-<load_id>_part-<file_id>.parquet

Same code as the DHIS2 ingestor's destination.py, with source="openlmis"; each
ingestor carries its own copy instead of sharing a module.
"""
from __future__ import annotations

import datetime as dt
from typing import Tuple
from uuid import uuid4

import dlt
from dlt.common.configuration.specs import AwsCredentials

from .config import Config


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def build_destination(cfg: Config, source: str = "openlmis") -> Tuple[object, dict, str, str]:
    """Return (destination, extra_placeholders, run_timestamp, run_id)."""
    run_ts = _utc_now()
    run_id_full = str(uuid4())
    run_id_short = run_id_full.replace("-", "")[:8]

    extra_placeholders = {
        "source": source,
        "year": run_ts.strftime("%Y"),
        "month": run_ts.strftime("%m"),
        "day": run_ts.strftime("%d"),
        "ts_compact": run_ts.strftime("%Y%m%dT%H%M%SZ"),
        "run_id_short": run_id_short,
    }

    layout = (
        "{table_name}/year={year}/month={month}/day={day}/"
        "{source}_{table_name}_{ts_compact}_run-{run_id_short}_part-{file_id}.{ext}"
    )

    destination = dlt.destinations.filesystem(
        bucket_url=f"s3://{cfg.minio_raw_bucket}",
        credentials=AwsCredentials(
            aws_access_key_id=cfg.minio_access_key,
            aws_secret_access_key=cfg.minio_secret_key,
            endpoint_url=cfg.minio_endpoint,
            region_name=cfg.minio_region,
        ),
        layout=layout,
        extra_placeholders=extra_placeholders,
    )
    return destination, extra_placeholders, run_ts.strftime("%Y-%m-%dT%H:%M:%SZ"), run_id_full
