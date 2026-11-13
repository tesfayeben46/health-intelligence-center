"""Environment-driven config for the HCMIS WDM ingestor."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote_plus


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Required env var not set: {name}")
    return val


@dataclass(frozen=True)
class Config:
    hcmiswdm_host: str
    hcmiswdm_port: int
    hcmiswdm_database: str
    hcmiswdm_user: str
    hcmiswdm_password: str
    hcmiswdm_driver: str
    hcmiswdm_trust_server_certificate: str
    hcmiswdm_timeout: int

    minio_endpoint: str
    minio_region: str
    minio_access_key: str
    minio_secret_key: str
    minio_raw_bucket: str

    dlt_pipeline_name: str

    def sqlalchemy_url(self) -> str:
        odbc = (
            f"DRIVER={{{self.hcmiswdm_driver}}};"
            f"SERVER={self.hcmiswdm_host},{self.hcmiswdm_port};"
            f"DATABASE={self.hcmiswdm_database};"
            f"UID={self.hcmiswdm_user};"
            f"PWD={self.hcmiswdm_password};"
            f"TrustServerCertificate={self.hcmiswdm_trust_server_certificate};"
            f"Encrypt=yes;"
            f"Connection Timeout={self.hcmiswdm_timeout};"
        )
        return f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc)}"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            hcmiswdm_host=_require("HCMISWDM_HOST"),
            hcmiswdm_port=int(os.environ.get("HCMISWDM_PORT", "1433")),
            hcmiswdm_database=_require("HCMISWDM_DATABASE"),
            hcmiswdm_user=_require("HCMISWDM_USER"),
            hcmiswdm_password=_require("HCMISWDM_PASSWORD"),
            hcmiswdm_driver=os.environ.get("HCMISWDM_DRIVER", "ODBC Driver 18 for SQL Server"),
            hcmiswdm_trust_server_certificate=os.environ.get("HCMISWDM_TRUST_SERVER_CERTIFICATE", "yes"),
            hcmiswdm_timeout=int(os.environ.get("HCMISWDM_TIMEOUT", "120")),
            minio_endpoint=_require("MINIO_ENDPOINT"),
            minio_region=os.environ.get("MINIO_REGION", "et-addis-1"),
            minio_access_key=_require("MINIO_ACCESS_KEY"),
            minio_secret_key=_require("MINIO_SECRET_KEY"),
            minio_raw_bucket=os.environ.get("MINIO_RAW_BUCKET", "nhdw-raw"),
            dlt_pipeline_name=os.environ.get("DLT_PIPELINE_NAME", "hcmiswdm"),
        )
