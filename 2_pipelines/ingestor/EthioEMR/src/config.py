"""Environment-driven config for the EthioEMR ingestor.

The ingestor reads the facility OpenMRS/Bahmni EMR **directly over MySQL**
(person/patient/patient_identifier/encounter/visit/obs) and reshapes each row
into the same FHIR-shaped columns the downstream staging + vault already expect.
The env surface is therefore ``ETHIOEMR_MYSQL_*`` (host/port/db/user/password);
the OpenMRS web application is not run.
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
    # MySQL source (facility OpenMRS/Bahmni EMR; direct DB / CDC surface).
    mysql_host: str
    mysql_port: int
    mysql_database: str
    mysql_user: str
    mysql_password: str
    mysql_timeout: int
    mysql_batch_size: int
    # Which FHIR resource types to emit. Each maps to one reader + one table.
    mysql_resources: tuple[str, ...]
    # Incremental cursor on OpenMRS `date_created`, not date_changed: synthetic
    # rows leave date_changed NULL. On the first run anything created before
    # this date is skipped.
    mysql_initial_cursor: str

    # Data lake (SeaweedFS S3 API; MINIO_* names are historical)
    minio_endpoint: str
    minio_region: str
    minio_access_key: str
    minio_secret_key: str
    minio_raw_bucket: str

    dlt_pipeline_name: str

    @classmethod
    def from_env(cls) -> "Config":
        resources = tuple(
            r.strip()
            for r in os.environ.get("ETHIOEMR_MYSQL_RESOURCES", "Patient,Encounter,Observation").split(",")
            if r.strip()
        )
        return cls(
            mysql_host=_require("ETHIOEMR_MYSQL_HOST"),
            mysql_port=int(os.environ.get("ETHIOEMR_MYSQL_PORT", "3306")),
            mysql_database=os.environ.get("ETHIOEMR_MYSQL_DB", "openmrs"),
            mysql_user=_require("ETHIOEMR_MYSQL_USER"),
            mysql_password=os.environ.get("ETHIOEMR_MYSQL_PASSWORD", ""),
            mysql_timeout=int(os.environ.get("ETHIOEMR_MYSQL_TIMEOUT", "120")),
            mysql_batch_size=int(os.environ.get("ETHIOEMR_MYSQL_BATCH_SIZE", "2000")),
            mysql_resources=resources,
            mysql_initial_cursor=os.environ.get("ETHIOEMR_MYSQL_INITIAL_CURSOR", "2020-01-01"),
            minio_endpoint=_require("MINIO_ENDPOINT"),
            minio_region=os.environ.get("MINIO_REGION", "et-addis-1"),
            minio_access_key=_require("MINIO_ACCESS_KEY"),
            minio_secret_key=_require("MINIO_SECRET_KEY"),
            minio_raw_bucket=os.environ.get("MINIO_RAW_BUCKET", "nhdw-raw"),
            dlt_pipeline_name=os.environ.get("DLT_PIPELINE_NAME", "ethioemr"),
        )
