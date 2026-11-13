"""Environment-driven settings for the NHDW gateway."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # Ops DB (nhdw_ops on postgres-vault)
    OPS_PG_HOST: str = "postgres-vault"
    OPS_PG_PORT: int = 5432
    OPS_PG_DATABASE: str = "nhdw_ops"
    OPS_PG_USER: str = "ops_gateway"
    OPS_PG_PASSWORD: str = ""

    # Object store (SeaweedFS S3 API; MINIO_* names are historical)
    MINIO_ENDPOINT: str = "http://seaweedfs-s3:8333"   # SeaweedFS S3 gateway (env var name kept for compatibility)
    MINIO_PUBLIC_ENDPOINT: str = "http://127.0.0.1:9100"
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    EXPORTS_BUCKET: str = "nhdw-exports"
    ARTIFACTS_BUCKET: str = "nhdw-artifacts"

    # Trino
    TRINO_HOST: str = "nhdw-trino"
    TRINO_PORT: int = 8080
    TRINO_USER: str = "nhdw-gateway"

    # OPA
    OPA_URL: str = "http://opa:8181"
    OPA_ENFORCE: bool = False          # true: OPA denials become 403; false: denials are advisory
    OPA_FAIL_OPEN: bool = False         # when OPA is unreachable: false = fail closed (deny),
                                        # true = fail open. Independent of OPA_ENFORCE.
    OPA_TIMEOUT_S: float = 2.5

    # Keycloak
    # Accepted `iss` claims, comma-separated, one per realm. They follow
    # Keycloak's KC_HOSTNAME (the URL the browser uses for PKCE), so they name
    # the external host, not the in-cluster service.
    KEYCLOAK_ALLOWED_ISSUERS: str = (
        "http://127.0.0.1:8090/realms/moh-staff,"
        "http://127.0.0.1:8090/realms/privileged-ops")
    # The gateway cannot reach the external issuer host from inside the
    # cluster, so JWKS fetches rewrite the issuer's base URL from the public
    # prefix to the internal one.
    KEYCLOAK_PUBLIC_BASE: str = "http://127.0.0.1:8090"
    KEYCLOAK_INTERNAL_BASE: str = "http://keycloak:8080"
    KEYCLOAK_AUDIENCE: str = "nhdw-gateway"
    # Keycloak Admin API for access-request provisioning: the bootstrap admin
    # (keycloak-secrets) creates the applicant's account and sends the
    # set-password email. PROVISION_REALM is the realm for MoH staff users.
    KEYCLOAK_ADMIN_USER: str = "keycloak_admin"
    KEYCLOAK_ADMIN_PASSWORD: str = ""
    KEYCLOAK_PROVISION_REALM: str = "moh-staff"
    # Additional accepted audiences (comma-separated). Dashboard clients
    # (`admin`, `workshop`, `internal-bi`, ...) issue tokens with their own
    # clientId as `aud`; these are accepted alongside KEYCLOAK_AUDIENCE.
    KEYCLOAK_EXTRA_AUDIENCES: str = (
        "admin,workshop,internal-bi,submission-portal,account")

    # Dev mode: with DEV_AUTH=true the gateway takes identity from the
    # `X-Mock-User: <kc_sub>` header with primary role R-DEV (overridable per
    # request with `X-Mock-Role`). Never set DEV_AUTH=true in production.
    DEV_AUTH: bool = False

    # Impersonation channel: when true the gateway also accepts `X-Mock-User`
    # and `X-Mock-Role` with Keycloak configured. Used by the dashboard view-as
    # controls and the boot seed scripts (seed-graduation, populate-demo-plugin).
    # Off by default.
    GATEWAY_DEMO_IMPERSONATION: bool = False
    # Shared secret an impersonation caller must present as `X-Mock-Secret`.
    # The gateway ingress is reachable, so without it any caller could send
    # `X-Mock-Role: R-SUPER`. Empty disables the channel (fail-secure) even when
    # GATEWAY_DEMO_IMPERSONATION=true. DEV_AUTH, a local no-Keycloak mode, does
    # not use this secret.
    GATEWAY_IMPERSONATION_SECRET: str = ""

    # Public anonymous tier: when true, a request with no credentials (no
    # Bearer, no X-Mock-*) maps to R-PUB-ANON. Unlike DEV_AUTH it grants only
    # R-PUB-ANON, which widget grants and OPA restrict to the public widgets and
    # aggregate (k>=5) data, and it does not enable the X-Mock channel. This is
    # how a deployed gateway serves the anonymous public portal.
    GATEWAY_PUBLIC_ANON: bool = False

    # Presigned-URL TTL for export downloads (seconds).
    EXPORT_PRESIGN_TTL_S: int = 900

    # Disease-surveillance alert evaluator and email.
    # The evaluator matches each active ops_workspace.subscription against the
    # latest forecast's alarm probability (the signal the dz-* widgets render)
    # and raises owner-scoped notifications plus best-effort email over SMTP.
    # SMTP_USER/SMTP_PASS come from the smtp-secrets Secret; when absent, a send
    # fails with an error instead of being silently dropped.
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_FROM: str = "alerts@nhdw.moh.gov.et"
    SMTP_TLS: bool = True
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    ALERT_EVAL_MODEL: str = "ears"       # DZ forecast model whose alarm_prob drives alerts
    ALERT_EVAL_TOKEN: str = ""           # shared secret for Airflow's POST /evaluate call
    DQ_DETECT_TOKEN: str = ""   # shared secret for Airflow's POST /api/dq/detect call
    # Shared secret the demo console presents to swap profiles and trigger demo
    # actions. Empty disables every mutating /api/demo route.
    DEMO_CONTROL_TOKEN: str = ""


settings = Settings()
