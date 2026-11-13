"""Environment-driven config for the OpenLMIS ingestor.

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
    # OpenLMIS source (OAuth2 password grant against /api/oauth/token)
    openlmis_base_url: str
    openlmis_user: str
    openlmis_password: str
    openlmis_oauth_client: str
    openlmis_oauth_secret: str
    openlmis_page_size: int
    openlmis_timeout: int

    # Legacy eLMIS v2 historical DB (hist-db): only the `history` pipeline
    # needs these; empty values are tolerated for the API pipelines.
    hist_host: str
    hist_port: int
    hist_db: str
    hist_user: str
    hist_password: str

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
            # API-lane credentials are only required by the API pipelines; the
            # `history` verb (legacy DB lane) must run without them (the live API lane is optional).
            openlmis_base_url=os.environ.get("OPENLMIS_BASE_URL", "").rstrip("/"),
            openlmis_user=os.environ.get("OPENLMIS_USER", ""),
            openlmis_password=os.environ.get("OPENLMIS_PASSWORD", ""),
            openlmis_oauth_client=os.environ.get("OPENLMIS_OAUTH_CLIENT", "user-client"),
            openlmis_oauth_secret=os.environ.get("OPENLMIS_OAUTH_SECRET", "changeme"),
            openlmis_page_size=int(os.environ.get("OPENLMIS_PAGE_SIZE", "200")),
            openlmis_timeout=int(os.environ.get("OPENLMIS_TIMEOUT", "120")),
            hist_host=os.environ.get("OPENLMIS_HIST_HOST", ""),
            hist_port=int(os.environ.get("OPENLMIS_HIST_PORT", "5434")),
            hist_db=os.environ.get("OPENLMIS_HIST_DB", "elmis_historical"),
            hist_user=os.environ.get("OPENLMIS_HIST_USER", "elmis_reader"),
            hist_password=os.environ.get("OPENLMIS_HIST_PASSWORD", ""),
            minio_endpoint=_require("MINIO_ENDPOINT"),
            minio_region=os.environ.get("MINIO_REGION", "et-addis-1"),
            minio_access_key=_require("MINIO_ACCESS_KEY"),
            minio_secret_key=_require("MINIO_SECRET_KEY"),
            minio_raw_bucket=os.environ.get("MINIO_RAW_BUCKET", "nhdw-raw"),
            dlt_pipeline_name=os.environ.get("DLT_PIPELINE_NAME", "openlmis"),
        )

    def require_api(self) -> None:
        for name, val in (("OPENLMIS_BASE_URL", self.openlmis_base_url), ("OPENLMIS_USER", self.openlmis_user), ("OPENLMIS_PASSWORD", self.openlmis_password)):
            if not val:
                raise RuntimeError(f"Required env var not set for the API lane: {name}")

    def hist_sqlalchemy_url(self) -> str:
        if not (self.hist_host and self.hist_password):
            raise RuntimeError("OPENLMIS_HIST_HOST / OPENLMIS_HIST_PASSWORD not set; required for the history pipeline")
        return (f"postgresql+psycopg2://{self.hist_user}:{self.hist_password}"
                f"@{self.hist_host}:{self.hist_port}/{self.hist_db}")
