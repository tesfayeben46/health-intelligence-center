"""dlt filesystem destination, producing:
    s3://nhdw-raw/ethioemr/<resource>/year=YYYY/month=MM/day=DD/ethioemr_<resource>_<ts>_run-<id>_part-<hash>.parquet
"""
from __future__ import annotations

import datetime as dt
from typing import Tuple
from uuid import uuid4

import dlt
from dlt.common.configuration.specs import AwsCredentials

from .config import Config


def build_destination(cfg: Config) -> Tuple[object, dict, str, str]:
    run_ts = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    run_id_full = str(uuid4())
    run_id_short = run_id_full.replace("-", "")[:8]

    extra_placeholders = {
        "source": "ethioemr",
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
