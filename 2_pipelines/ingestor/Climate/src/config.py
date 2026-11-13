"""Environment-driven config for the Climate ingestor.

Pulls weekly meteorological CSV from the external Climate API replica
(0_external/sources/Climate) into the data lake (SeaweedFS) as partitioned Parquet.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Config:
    # Climate API source
    climate_api_base_url: str
    climate_page_size: int
    climate_timeout: int
    # ISO-date floor on week_start for the first run (empty = full history).
    climate_initial_updated_since: str
    # Dev bound: stop after N rows. 0 = unbounded.
    climate_dev_max_rows: int

    # Data lake (SeaweedFS S3 API; MINIO_* names are historical)
    minio_endpoint: str
    minio_region: str
    minio_access_key: str
    minio_secret_key: str
    minio_raw_bucket: str

    dlt_pipeline_name: str

    @staticmethod
    def from_env() -> "Config":
        return Config(
            climate_api_base_url=_env("CLIMATE_API_BASE_URL", "http://host.k3d.internal:8095"),
            climate_page_size=int(_env("CLIMATE_PAGE_SIZE", "10000")),
            climate_timeout=int(_env("CLIMATE_TIMEOUT", "60")),
            climate_initial_updated_since=_env("CLIMATE_INITIAL_UPDATED_SINCE", ""),
            climate_dev_max_rows=int(_env("CLIMATE_DEV_MAX_ROWS", "0")),
            minio_endpoint=_env("MINIO_ENDPOINT", "http://seaweedfs-s3.nhdw.svc.cluster.local:8333"),
            minio_region=_env("MINIO_REGION", "et-addis-1"),
            minio_access_key=_env("MINIO_ACCESS_KEY", ""),
            minio_secret_key=_env("MINIO_SECRET_KEY", ""),
            minio_raw_bucket=_env("MINIO_RAW_BUCKET", "nhdw-raw"),
            dlt_pipeline_name=_env("DLT_PIPELINE_NAME", "climate"),
        )
