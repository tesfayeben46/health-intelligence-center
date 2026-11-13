"""dlt filesystem destination producing the canonical lake path:

    s3://nhdw-raw/mfr/<table>/year=YYYY/month=MM/day=DD/mfr_<table>_<ts>_run-<id>_part-<hash>.parquet

A single timestamp is taken when the run starts so every Parquet part for one
run lands in the same date partition even if the run crosses midnight.
"""
from __future__ import annotations

import datetime as dt
from typing import Tuple
from uuid import uuid4

import dlt
from dlt.common.configuration.specs import AwsCredentials

from .config import Config


def build_destination(cfg: Config) -> Tuple[object, dict, str, str]:
    """Return (destination, extra_placeholders, run_timestamp, run_id)."""
    run_ts = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    run_id_full = str(uuid4())
    run_id_short = run_id_full.replace("-", "")[:8]

    # dataset_name="mfr" gives the top-level "mfr/" key prefix; custom
    # placeholders slot in after {table_name} (dlt rule).
    extra_placeholders = {
        "source": "mfr",
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
