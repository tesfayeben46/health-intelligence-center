"""Build the dlt filesystem destination pointed at the data lake (SeaweedFS S3 API).

The layout below produces the canonical path:
    s3://nhdw-raw/dhis2/<table>/year=YYYY/month=MM/day=DD/dhis2_<table>_<ts>_run-<load_id>_part-<file_id>.parquet

We use dlt 1.x's `extra_placeholders` and `current_datetime` features to inject
year/month/day from a single timestamp taken when the pipeline starts. This
keeps every Parquet part for one run in the same date partition even if the
run crosses midnight.
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


def build_destination(cfg: Config) -> Tuple[object, dict, str, str]:
    """Return (destination, extra_placeholders, run_timestamp, run_id)."""
    run_ts = _utc_now()
    run_id_full = str(uuid4())
    run_id_short = run_id_full.replace("-", "")[:8]

    # dlt auto-prefixes the bucket with the pipeline's dataset_name. Setting
    # dataset_name="dhis2" produces a top-level "dhis2/" key prefix without
    # needing {schema_name} in the layout, which dlt does not accept in this
    # position. Custom placeholders slot in after {table_name}.
    extra_placeholders = {
        "source": "dhis2",
        "year": run_ts.strftime("%Y"),
        "month": run_ts.strftime("%m"),
        "day": run_ts.strftime("%d"),
        "ts_compact": run_ts.strftime("%Y%m%dT%H%M%SZ"),
        "run_id_short": run_id_short,
    }

    # Final S3 key shape:
    #   <bucket>/<dataset_name=source>/<table>/year=YYYY/month=MM/day=DD/<source>_<table>_<ts>_run-<id>_part-<file_id>.parquet
    # `{file_id}` is a 10-hex content hash produced by dlt: sortable and dedup-friendly.
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
