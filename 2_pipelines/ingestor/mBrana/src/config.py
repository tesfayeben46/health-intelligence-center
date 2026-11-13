"""Environment-driven config for the mBrana ingestor."""
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
    # SQL Server source
    mbrana_host: str
    mbrana_port: int
    mbrana_database: str
    mbrana_user: str
    mbrana_password: str
    mbrana_driver: str
    mbrana_trust_server_certificate: str
    mbrana_timeout: int

    # Data lake (SeaweedFS S3 API; MINIO_* names are historical)
    minio_endpoint: str
    minio_region: str
    minio_access_key: str
    minio_secret_key: str
    minio_raw_bucket: str

    dlt_pipeline_name: str

    def sqlalchemy_url(self) -> str:
        """SQLAlchemy connection URL for pyodbc + ODBC Driver 18."""
        odbc = (
            f"DRIVER={{{self.mbrana_driver}}};"
            f"SERVER={self.mbrana_host},{self.mbrana_port};"
            f"DATABASE={self.mbrana_database};"
            f"UID={self.mbrana_user};"
            f"PWD={self.mbrana_password};"
            f"TrustServerCertificate={self.mbrana_trust_server_certificate};"
            f"Encrypt=yes;"
            f"Connection Timeout={self.mbrana_timeout};"
        )
        return f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc)}"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            mbrana_host=_require("MBRANA_HOST"),
            mbrana_port=int(os.environ.get("MBRANA_PORT", "1433")),
            mbrana_database=_require("MBRANA_DATABASE"),
            mbrana_user=_require("MBRANA_USER"),
            mbrana_password=_require("MBRANA_PASSWORD"),
            mbrana_driver=os.environ.get("MBRANA_DRIVER", "ODBC Driver 18 for SQL Server"),
            mbrana_trust_server_certificate=os.environ.get("MBRANA_TRUST_SERVER_CERTIFICATE", "yes"),
            mbrana_timeout=int(os.environ.get("MBRANA_TIMEOUT", "120")),
            minio_endpoint=_require("MINIO_ENDPOINT"),
            minio_region=os.environ.get("MINIO_REGION", "et-addis-1"),
            minio_access_key=_require("MINIO_ACCESS_KEY"),
            minio_secret_key=_require("MINIO_SECRET_KEY"),
            minio_raw_bucket=os.environ.get("MINIO_RAW_BUCKET", "nhdw-raw"),
            dlt_pipeline_name=os.environ.get("DLT_PIPELINE_NAME", "mbrana"),
        )
