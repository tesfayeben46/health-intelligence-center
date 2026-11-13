# Airflow (FAB) webserver config: native Keycloak OIDC login instead of the
# built-in DB-form login. Baked into nhdw/airflow at
# /opt/airflow/webserver_config.py; only the webserver reads it.
#
# Split URLs: the public host keycloak.127.0.0.1.nip.io resolves to 127.0.0.1,
# which inside the pod is the pod itself, not Keycloak. The browser therefore
# authorizes on the public host while the pod exchanges the code, fetches JWKS
# and reads userinfo on the internal service DNS. `issuer` is pinned to the
# public value so the id_token `iss` (stamped from KC_HOSTNAME, the public host)
# validates, and `jwks_uri` points internal so signature verification is
# reachable server-side. The gateway and oauth2-proxy use the same pattern.
#
# Roles: Keycloak realm roles from the access token map to FAB roles
#   R-SUPER, R-OPS-ADM -> Admin;  R-OPS-ENG -> Op;  R-OPS-SEC -> Viewer
# and are re-synced at every login. A user with none of these lands in the
# empty Public role (no DAG access). This client is seeded only in the
# privileged-ops realm.
#
# Config from env (1_infrastructure/k8s/base/airflow.yaml, webserver container):
#   AIRFLOW_OIDC_CLIENT_ID / AIRFLOW_OIDC_CLIENT_SECRET
#   AIRFLOW_OIDC_ISSUER_PUBLIC    https://keycloak.127.0.0.1.nip.io/realms/privileged-ops
#   AIRFLOW_OIDC_ISSUER_INTERNAL  http://keycloak.nhdw.svc.cluster.local:8080/realms/privileged-ops
# If any is absent the config falls back to AUTH_DB (the break-glass admin
# user), so a misconfigured secret degrades to password login instead of
# locking Airflow out.
import logging
import os

from flask_appbuilder.security.manager import AUTH_DB, AUTH_OAUTH

log = logging.getLogger("nhdw.airflow.oidc")

WTF_CSRF_ENABLED = True

_CID = os.environ.get("AIRFLOW_OIDC_CLIENT_ID", "").strip()
_SECRET = os.environ.get("AIRFLOW_OIDC_CLIENT_SECRET", "").strip()
_PUB = os.environ.get("AIRFLOW_OIDC_ISSUER_PUBLIC", "").strip().rstrip("/")
_INT = os.environ.get("AIRFLOW_OIDC_ISSUER_INTERNAL", "").strip().rstrip("/")

if _CID and _SECRET and _PUB and _INT:
    from airflow.providers.fab.auth_manager.security_manager.override import (
        FabAirflowSecurityManagerOverride,
    )

    AUTH_TYPE = AUTH_OAUTH
    AUTH_USER_REGISTRATION = True          # auto-create the Keycloak user on first login
    AUTH_USER_REGISTRATION_ROLE = "Public"  # fallback only; real role from the mapping below
    AUTH_ROLES_SYNC_AT_LOGIN = True         # re-read realm roles on every login
    AUTH_ROLES_MAPPING = {
        "R-SUPER": ["Admin"],   # platform superuser (wildcard access everywhere)
        "R-OPS-ADM": ["Admin"],
        "R-OPS-ENG": ["Op"],
        "R-OPS-SEC": ["Viewer"],
    }
    # Realm roles translated to Airflow roles; all others are ignored, so a user
    # with none lands in the empty Public role (no DAG access).
    _NHDW_ROLE_PREFIXES = ("R-OPS-", "R-SUPER")

    OAUTH_PROVIDERS = [
        {
            "name": "keycloak",
            "icon": "fa-key",
            "token_key": "access_token",
            "remote_app": {
                "client_id": _CID,
                "client_secret": _SECRET,
                # userinfo is fetched relative to api_base_url -> .../protocol/openid-connect/userinfo
                "api_base_url": f"{_INT}/protocol/",
                "access_token_url": f"{_INT}/protocol/openid-connect/token",
                "authorize_url": f"{_PUB}/protocol/openid-connect/auth",
                "jwks_uri": f"{_INT}/protocol/openid-connect/certs",
                "issuer": _PUB,
                "client_kwargs": {"scope": "openid email profile"},
            },
        }
    ]

    class NHDWKeycloakSecurityManager(FabAirflowSecurityManagerOverride):
        """Map NHDW Keycloak realm roles to FAB role_keys.

        FAB's stock `keycloak` provider reads role_keys from a `groups` claim,
        which NHDW does not emit. The realm has no standard `roles` client
        scope; realm roles are flattened into a top-level `roles` claim by the
        `nhdw-flat-roles` default client scope (the claim the gateway reads).
        Roles are read from the access token, accepting either that top-level
        `roles` claim or the standard `realm_access.roles`."""

        @staticmethod
        def _roles_from(claims):
            return ((claims.get("realm_access") or {}).get("roles")
                    or claims.get("roles") or [])

        def get_oauth_user_info(self, provider, resp=None):
            if provider != "keycloak":
                return {}
            remote = self.appbuilder.sm.oauth_remotes[provider]
            me = remote.get("openid-connect/userinfo")
            me.raise_for_status()
            data = me.json()

            roles = []
            token = (resp or {}).get("access_token")
            if token:
                try:
                    import jwt  # Airflow ships PyJWT

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
                "role_keys": [r for r in roles if r.startswith(_NHDW_ROLE_PREFIXES)],
            }

    SECURITY_MANAGER_CLASS = NHDWKeycloakSecurityManager
else:
    log.warning(
        "AIRFLOW OIDC env incomplete (client id/secret/issuers); "
        "falling back to AUTH_DB (break-glass admin login)."
    )
    AUTH_TYPE = AUTH_DB
