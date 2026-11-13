"""Environment-driven config for the MFR ingestor.

All env-var coupling lives here. The rest of the codebase reads from a single
`Config` instance.

MFR (Master Facility Registry) is exposed as a FHIR R4 server. The richest
path is the `/Location` resource (one per facility) plus `/Organization`
(facility owners). See README.md for the field mapping.
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
    # MFR FHIR R4 source
    mfr_fhir_base_url: str
    mfr_fhir_user: str
    mfr_fhir_password: str
    mfr_fhir_token: str
    mfr_fhir_page_size: int
    mfr_fhir_timeout: int
    # 0 = unbounded: follow Bundle `next` links until the registry is exhausted.
    # A positive value caps pages (dev / smoke runs).
    mfr_fhir_max_pages: int
    # ISO 8601 floor on FHIR `_lastUpdated`. Empty (default) = full registry
    # pull, the right default for a slow-moving facility dimension.
    mfr_fhir_since: str
    # Dev bound: stop after N rows per resource. 0 (default) = unbounded.
    mfr_dev_max_rows: int

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
            mfr_fhir_base_url=_require("MFR_FHIR_BASE_URL").rstrip("/"),
            mfr_fhir_user=os.environ.get("MFR_FHIR_USER", ""),
            mfr_fhir_password=os.environ.get("MFR_FHIR_PASSWORD", ""),
            mfr_fhir_token=os.environ.get("MFR_FHIR_TOKEN", ""),
            mfr_fhir_page_size=int(os.environ.get("MFR_FHIR_PAGE_SIZE", "200")),
            mfr_fhir_timeout=int(os.environ.get("MFR_FHIR_TIMEOUT", "120")),
            mfr_fhir_max_pages=int(os.environ.get("MFR_FHIR_MAX_PAGES", "0")),
            mfr_fhir_since=os.environ.get("MFR_FHIR_SINCE", ""),
            mfr_dev_max_rows=int(os.environ.get("MFR_DEV_MAX_ROWS", "0")),
            minio_endpoint=_require("MINIO_ENDPOINT"),
            minio_region=os.environ.get("MINIO_REGION", "et-addis-1"),
            minio_access_key=_require("MINIO_ACCESS_KEY"),
            minio_secret_key=_require("MINIO_SECRET_KEY"),
            minio_raw_bucket=os.environ.get("MINIO_RAW_BUCKET", "nhdw-raw"),
            dlt_pipeline_name=os.environ.get("DLT_PIPELINE_NAME", "mfr"),
        )
