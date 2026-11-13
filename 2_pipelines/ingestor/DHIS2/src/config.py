"""Environment-driven config for the DHIS2 ingestor.

All env-var coupling lives here. The rest of the codebase reads from a single
`Config` instance.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Required env var not set: {name}")
    return val


@dataclass(frozen=True)
class Config:
    # DHIS2 source
    dhis2_base_url: str
    dhis2_user: str
    dhis2_password: str
    dhis2_page_size: int
    dhis2_initial_last_updated: str
    dhis2_timeout: int
    # Dev bound: stop yielding dataValues after N rows total across all dataSets.
    # 0 (default) = unbounded; full runs leave this off. CI/dev sets a cap.
    dhis2_dev_max_rows: int
    # Dev period bracket (ISO yyyy-mm-dd). When both set, dvs ignores the
    # `lastUpdated` watermark and uses DHIS2's startDate/endDate period filter.
    # Workaround for a v40 dump where `lastUpdated` returns empty for any date.
    dhis2_dev_start_date: str
    dhis2_dev_end_date: str
    # Dev scope: pin the dvs query to a specific orgUnit subtree (e.g. one
    # region) instead of the country root. Empty = use the level-1 root.
    dhis2_dev_root_ou: str

    # Data lake (SeaweedFS S3 API; MINIO_* names are historical)
    minio_endpoint: str
    minio_region: str
    minio_access_key: str
    minio_secret_key: str
    minio_raw_bucket: str

    # dlt
    dlt_pipeline_name: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            dhis2_base_url=_require("DHIS2_BASE_URL").rstrip("/"),
            dhis2_user=_require("DHIS2_USER"),
            dhis2_password=_require("DHIS2_PASSWORD"),
            dhis2_page_size=int(os.environ.get("DHIS2_PAGE_SIZE", "200")),
            dhis2_initial_last_updated=os.environ.get("DHIS2_INITIAL_LAST_UPDATED", "2020-01-01"),
            dhis2_timeout=int(os.environ.get("DHIS2_TIMEOUT", "120")),
            dhis2_dev_max_rows=int(os.environ.get("DHIS2_DEV_MAX_ROWS", "0")),
            dhis2_dev_start_date=os.environ.get("DHIS2_DEV_START_DATE", ""),
            dhis2_dev_end_date=os.environ.get("DHIS2_DEV_END_DATE", ""),
            dhis2_dev_root_ou=os.environ.get("DHIS2_DEV_ROOT_OU", ""),
            minio_endpoint=_require("MINIO_ENDPOINT"),
            minio_region=os.environ.get("MINIO_REGION", "et-addis-1"),
            minio_access_key=_require("MINIO_ACCESS_KEY"),
            minio_secret_key=_require("MINIO_SECRET_KEY"),
            minio_raw_bucket=os.environ.get("MINIO_RAW_BUCKET", "nhdw-raw"),
            dlt_pipeline_name=os.environ.get("DLT_PIPELINE_NAME", "dhis2"),
        )
