# superset_config.py: Apache Superset for NHDW self-service SQL and BI.
# Baked into nhdw/superset at /app/pythonpath/superset_config.py.
#
# Audience and governance: Superset queries the marts directly through Trino
# (as JupyterHub does), which bypasses the nhdw-gateway's OPA row filters and
# masking; OPA governance lives only in the gateway. Superset is therefore
# limited to the same trusted analyst set as Jupyter (R-CONS-ANA, R-CONS-SCI,
# R-CONS-RES, R-DEC, R-STEW, R-SUPER); every other role lands in the empty
# Public role. Superset RLS rules are not used as a substitute for OPA because
# they would drift from the policy.
#
# Split URLs (as in Airflow's webserver_config.py): k3d resolves the public host
# keycloak.127.0.0.1.nip.io to 127.0.0.1, which inside the pod is the pod
# itself. The browser authorizes on the public host; the pod exchanges the code,
# fetches JWKS and reads userinfo on the internal service DNS. `issuer` is the
# public URL so the id_token `iss` validates; `jwks_uri` is internal so
# signature verification works server-side.
import logging
import os

from flask_appbuilder.security.manager import AUTH_DB, AUTH_OAUTH

log = logging.getLogger("nhdw.superset.oidc")

# metadata DB (postgres-vault nhdw_superset, same layout as nhdw_airflow)
_DB_PW = os.environ.get("SUPERSET_DB_PASSWORD", "")
_DB_HOST = os.environ.get("SUPERSET_DB_HOST", "postgres-vault.nhdw.svc.cluster.local")
_DB_NAME = os.environ.get("SUPERSET_DB_NAME", "nhdw_superset")
SQLALCHEMY_DATABASE_URI = (
    f"postgresql+psycopg2://superset_writer:{_DB_PW}@{_DB_HOST}:5432/{_DB_NAME}"
)

# Flask session signing key; must be stable across restarts.
SECRET_KEY = os.environ.get("SUPERSET_SECRET_KEY", "changeme-superset-secret-key")

# Single process: synchronous SQL Lab, no Celery or Redis.
SUPERSET_WEBSERVER_TIMEOUT = 120
SQLLAB_TIMEOUT = 90
SUPERSET_WEBSERVER_PROTOCOL = "https"
# Behind Traefik TLS: trust the proxy's X-Forwarded-* so redirect_uri is https.
ENABLE_PROXY_FIX = True
WTF_CSRF_ENABLED = True
# Dashboard-level RBAC so an owner can scope a dashboard to specific roles.
FEATURE_FLAGS = {"DASHBOARD_RBAC": True}

# Keycloak OIDC (FAB OAuth): moh-staff realm, the analyst audience
_CID = os.environ.get("SUPERSET_OIDC_CLIENT_ID", "").strip()
_SECRET = os.environ.get("SUPERSET_OIDC_CLIENT_SECRET", "").strip()
_PUB = os.environ.get("SUPERSET_OIDC_ISSUER_PUBLIC", "").strip().rstrip("/")
_INT = os.environ.get("SUPERSET_OIDC_ISSUER_INTERNAL", "").strip().rstrip("/")

if _CID and _SECRET and _PUB and _INT:
    from superset.security import SupersetSecurityManager

    AUTH_TYPE = AUTH_OAUTH
    AUTH_USER_REGISTRATION = True            # auto-create the KC user on first login
    AUTH_USER_REGISTRATION_ROLE = "Public"   # fallback only; real roles from mapping
    AUTH_ROLES_SYNC_AT_LOGIN = True          # re-read realm roles every login
    # NHDW realm role to Superset roles. Alpha is full self-service (datasets,
    # charts, SQL Lab); Gamma plus sql_lab reads and queries without creating
    # datasets. An unlisted role yields no role_keys and so the empty Public role.
    AUTH_ROLES_MAPPING = {
        "R-SUPER":    ["Admin"],
        "R-CONS-ANA": ["Alpha", "sql_lab"],
        "R-CONS-SCI": ["Alpha", "sql_lab"],
        "R-CONS-RES": ["Alpha", "sql_lab"],
        "R-DEC":      ["Alpha", "sql_lab"],
        "R-STEW":     ["Gamma", "sql_lab"],
    }
    _ALLOWED = set(AUTH_ROLES_MAPPING)

    OAUTH_PROVIDERS = [
        {
            "name": "keycloak",
            "icon": "fa-key",
            "token_key": "access_token",
            "remote_app": {
                "client_id": _CID,
                "client_secret": _SECRET,
                # userinfo is fetched relative to api_base_url (.../openid-connect/userinfo)
                "api_base_url": f"{_INT}/protocol/",
                "access_token_url": f"{_INT}/protocol/openid-connect/token",
                "authorize_url": f"{_PUB}/protocol/openid-connect/auth",
                "jwks_uri": f"{_INT}/protocol/openid-connect/certs",
                "issuer": _PUB,
                "client_kwargs": {"scope": "openid email profile"},
            },
        }
    ]

    class NHDWSupersetSecurityManager(SupersetSecurityManager):
        """Map NHDW Keycloak realm roles to Superset FAB role_keys.

        The realm has no standard `roles` or `groups` client scope; the
        `nhdw-flat-roles` default client scope flattens realm roles into a
        top-level `roles` claim (the same claim the gateway and Airflow read).
        Roles are read from the access token, accepting the standard
        `realm_access.roles` as well."""

        @staticmethod
        def _roles_from(claims):
            return ((claims.get("realm_access") or {}).get("roles")
                    or claims.get("roles") or [])

        def get_oauth_user_info(self, provider, response=None):
            if provider != "keycloak":
                return {}
            remote = self.appbuilder.sm.oauth_remotes[provider]
            me = remote.get("openid-connect/userinfo")
            me.raise_for_status()
            data = me.json()

            roles = []
            token = (response or {}).get("access_token")
            if token:
                try:
                    import jwt  # Superset ships PyJWT

                    roles = self._roles_from(
                        jwt.decode(token, options={"verify_signature": False}))
                except Exception as exc:  # pragma: no cover
                    log.warning("access-token role decode failed: %s", exc)
            if not roles:
                roles = self._roles_from(data)

            return {
                "username": data.get("preferred_username") or data.get("sub", ""),
                "first_name": data.get("given_name", ""),
                "last_name": data.get("family_name", ""),
                "email": data.get("email", ""),
                "role_keys": [r for r in roles if r in _ALLOWED],
            }

    CUSTOM_SECURITY_MANAGER = NHDWSupersetSecurityManager
else:
    log.warning(
        "SUPERSET OIDC env incomplete (client id/secret/issuers): falling back "
        "to AUTH_DB (break-glass admin login)."
    )
    AUTH_TYPE = AUTH_DB
