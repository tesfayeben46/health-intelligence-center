#!/usr/bin/env python3
"""Keycloak setup for NHDW: realm roles, users, clients and identity brokers.

Idempotent. Drives Keycloak's Admin REST API to:

  1. Create missing realm roles (R-SUPER, R-PD-HIV, R-RHB-OROMIA, R-OPS-NONE,
     plus any role the loaded users.yaml references).
  2. Upsert one Keycloak user per row in users.yaml with the exact kc_sub
     UUID from the YAML, so nhdw_ops lookups by kc_sub stay consistent. Sets
     the seed-user password from the `SEED_PASSWORD` env var.
  3. Assign each user's realm roles.
  4. Create or convert the dashboard clients as public PKCE clients
     (moh-staff/internal-bi, moh-staff/workshop, privileged-ops/admin), the
     confidential tool clients and the tool-gate clients.
  5. Configure the Google and EHIA (SAML) identity brokers, their mappers,
     client scopes and realm SMTP.

In Kubernetes it runs as the keycloak-seed Job. Locally, after Keycloak is up:
    cd 7_security/keycloak
    python3 scripts/setup-keycloak.py

Env vars used (with defaults):
    KEYCLOAK_BASE                http://127.0.0.1:8090
    KC_BOOTSTRAP_ADMIN_USERNAME  (from .env)
    KC_BOOTSTRAP_ADMIN_PASSWORD  (from .env)
    SEED_PASSWORD                (required)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

# Repo root is only used to derive the default users.yaml path. When the
# script is mounted shallow (e.g. /scripts/setup-keycloak.py in a k8s Job),
# parents[3] does not exist; fall back to "/" since NHDW_USERS_YAML sets the
# path in that case.
_parents = Path(__file__).resolve().parents
REPO_ROOT = _parents[3] if len(_parents) > 3 else Path("/")
# Env override so the script works from a host shell and from a k8s Job that
# mounts users.yaml at a fixed path.
USERS_YAML = Path(os.environ.get(
    "NHDW_USERS_YAML",
    str(REPO_ROOT / "bundle/seed-pack/app/security/users.yaml")))
KC_BASE = os.environ.get("KEYCLOAK_BASE", "http://127.0.0.1:8090")
ADMIN_USERNAME = os.environ["KC_BOOTSTRAP_ADMIN_USERNAME"]
ADMIN_PASSWORD = os.environ["KC_BOOTSTRAP_ADMIN_PASSWORD"]
SEED_PASSWORD = os.environ["SEED_PASSWORD"]  # demo-user-secrets/SEED_PASSWORD


# Realm roles that need to exist for users.yaml's assignments to succeed.
# Keycloak's role catalog must be a superset of what the YAML references.
MISSING_REALM_ROLES = {
    # R-SUPER exists in both realms so the cross-realm superadmin test account can
    # hold it on the moh-staff side (internal-bi/workshop) as well as privileged-ops.
    "moh-staff":      ["R-PD-HIV", "R-RHB-OROMIA", "R-SUPER"],
    # R-OPS-NONE is the zero-grant landing role for anyone who reaches the
    # privileged-ops realm through a brokered IdP (Google/EHIA). It is not in the
    # OPA decision router (common.rego) and holds no permissions, so OPA
    # default-denies it every action ("unknown role" -> allow:false). The
    # Google/EHIA buttons on the admin login page authenticate, but the identity
    # lands with nothing: a brokered user can never become an operator or admin
    # (see IDENTITY_PROVIDERS).
    "privileged-ops": ["R-SUPER", "R-OPS-NONE"],
}


# Dashboard clients we want present as public PKCE clients.
# `convert_from_confidential`: if True, downgrade an existing confidential
# client to public (used for the pre-existing internal-bi client).
# IdP brokers. Every protected dashboard's login page offers the same three
# options: username/password, Google (social OIDC, Keycloak's built-in `google`
# provider, credentials from keycloak-idp-secrets) and the ehia-saml-mock realm
# (SAML federation, imported at Keycloak startup). Both staff realms are
# brokered: moh-staff (internal-bi and workshop) and privileged-ops (admin).
# If GOOGLE_CLIENT_ID is unset or a placeholder, the Google broker is skipped on
# both realms (see _google_configured / main); EHIA stays available.
#
# Security invariant: a public social or federated login must never become an
# operator or admin. The admin realm (privileged-ops) shows the Google and EHIA
# buttons and lets them authenticate, but lands every brokered identity on
# `R-OPS-NONE`, a zero-grant realm role that is not in the OPA decision router,
# so OPA default-denies every action ("unknown role" -> allow:false) and the
# admin dashboard returns SPEC_DENIED. Google also carries idp_trust=low, so the
# OPA trust guard blocks every sensitive action even if a role were
# mis-assigned. The moh-staff brokers land on read-only roles; a steward
# elevates a user through the access-request flow.
IDENTITY_PROVIDERS = [
    {
        "into_realm":      "moh-staff",
        "alias":           "google",
        "display":         "Google",
        "kind":            "google",        # Keycloak built-in social provider (real Google)
        "default_role":    "R-CONS-ANA",   # least-privilege read-only consumer
        # Trust tier: anyone can register a Gmail account, so Google is low
        # trust. Email is not auto-verified and the OPA trust guard denies its
        # identities every sensitive action (writes and raw PHI).
        "trust_email":     False,
        "trust_tier":      "low",
    },
    {
        "into_realm":      "moh-staff",
        "alias":           "ehia-saml",
        "display":         "EHIA",
        "kind":            "saml",
        "source_realm":    "ehia-saml-mock",
        "default_role":    "R-CONS-RES",   # trusted-partner researcher (non-PHI)
        # Trusted government identity partner: high trust (email verified, no
        # trust-guard restriction beyond its role).
        "trust_email":     True,
        "trust_tier":      "high",
    },
    # privileged-ops (admin dashboard): buttons visible, authz denied
    # Same two brokers, but they land on R-OPS-NONE (zero grants), so a brokered
    # identity authenticates yet gets no operator or admin access (OPA
    # unknown-role deny). Do not give these a real operator role.
    {
        "into_realm":      "privileged-ops",
        "alias":           "google",
        "display":         "Google",
        "kind":            "google",        # real Google, same credentials as moh-staff
        "default_role":    "R-OPS-NONE",   # zero-grant landing role -> OPA denies all
        "trust_email":     False,
        "trust_tier":      "low",           # OPA trust guard also blocks sensitive actions
    },
    {
        "into_realm":      "privileged-ops",
        "alias":           "ehia-saml",
        "display":         "EHIA",
        "kind":            "saml",
        "source_realm":    "ehia-saml-mock",
        "default_role":    "R-OPS-NONE",   # zero-grant landing role -> OPA denies all
        # Federated partner identity; in the admin realm it still lands with no
        # grants, because no external login may yield operator access. It stays
        # high trust as in moh-staff; the denial comes from the empty role, not
        # the trust tier.
        "trust_email":     True,
        "trust_tier":      "high",
    },
]


# Browser-facing Keycloak base (issuer / IdP-broker URLs). Defaults to the
# dev-compose localhost port; override via env for ingress/hostname deploys.
KC_PUBLIC_BASE = os.environ.get("KC_PUBLIC_BASE", "http://127.0.0.1:8090")

# Google OAuth client (from the keycloak-idp-secrets Secret) for the "Sign in
# with Google" broker on both staff realms. There is no mock: empty or a
# placeholder skips the Google broker entirely (login stays password plus EHIA
# federation, and the boot continues).
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
# Confidential OIDC secret for the dbt-docs tool client (both staff realms).
DBT_DOCS_OIDC_SECRET = os.environ.get("DBT_DOCS_OIDC_SECRET", "").strip()
# Confidential OIDC secret for the Airflow webserver's native Keycloak login
# (privileged-ops).
AIRFLOW_OIDC_SECRET = os.environ.get("AIRFLOW_OIDC_SECRET", "").strip()
# Confidential OIDC secret for the in-cluster JupyterHub (moh-staff).
JUPYTERHUB_OIDC_SECRET = os.environ.get("JUPYTERHUB_OIDC_SECRET", "").strip()
# Confidential OIDC secret for Superset's native FAB Keycloak login (moh-staff,
# the analyst audience). Same split-URL native-OIDC pattern as Airflow.
SUPERSET_OIDC_SECRET = os.environ.get("SUPERSET_OIDC_SECRET", "").strip()
# OpenMetadata server-side OIDC (moh-staff): the OM server exchanges the code
# at /callback and hands the browser a session cookie.
OPENMETADATA_OIDC_SECRET = os.environ.get("OPENMETADATA_OIDC_SECRET", "").strip()
# Confidential OIDC secrets for the nhdw/tool-gate reverse proxies, one per tool
# without its own role-aware OIDC (MLflow, Prometheus, Alertmanager, Headlamp,
# SeaweedFS). Each gate runs its own OIDC handshake (/oauth2gate/callback),
# admitted silently off the shared SSO cookie, then enforces per-role access
# itself. Seeded in both staff realms because an ops role (e.g. R-OPS-ENG) can
# live in moh-staff while R-OPS-ADM/R-SUPER live in privileged-ops; the gate
# accepts a session from either and ALLOWED_ROLES does the gating. Each secret
# matches the gate's k8s Secret.
MLFLOW_GATE_OIDC_SECRET       = os.environ.get("MLFLOW_GATE_OIDC_SECRET", "").strip()
PROMETHEUS_GATE_OIDC_SECRET   = os.environ.get("PROMETHEUS_GATE_OIDC_SECRET", "").strip()
ALERTMANAGER_GATE_OIDC_SECRET = os.environ.get("ALERTMANAGER_GATE_OIDC_SECRET", "").strip()
HEADLAMP_GATE_OIDC_SECRET     = os.environ.get("HEADLAMP_GATE_OIDC_SECRET", "")
SEAWEEDFS_GATE_OIDC_SECRET = os.environ.get("SEAWEEDFS_GATE_OIDC_SECRET", "").strip()


def _google_configured() -> bool:
    cid = GOOGLE_CLIENT_ID.lower()
    return bool(GOOGLE_CLIENT_ID) and "changeme" not in cid and "todo" not in cid

# Per-dashboard browser-facing base URLs. Default to the dev-compose localhost
# ports; override via env (e.g. nip.io hostnames behind an ingress).
_DASH_BASE = {
    "internal-bi": os.environ.get("DASH_BASE_INTERNAL_BI", "http://127.0.0.1:8087"),
    "workshop":    os.environ.get("DASH_BASE_WORKSHOP",    "http://127.0.0.1:8085"),
    "admin":       os.environ.get("DASH_BASE_ADMIN",       "http://127.0.0.1:8086"),
}

# The single public host base (e.g. 127.0.0.1.nip.io for dev, <public-ip>.nip.io on a hosted
# node). Every tool and gate client's redirect base derives from it, so one value (the Helm
# hostBase, passed as PUBLIC_HOST_BASE by the seed job) moves the whole OIDC surface, matching
# the ingress and SPA settings. Reseeding without it rewrites redirect URIs to the default.
_HOST_BASE = os.environ.get("PUBLIC_HOST_BASE", "127.0.0.1.nip.io")


def _client(realm, client_id, base, convert):
    return {
        "realm":        realm,
        "client_id":    client_id,
        "redirect_uri": base.rstrip("/") + "/*",
        "web_origin":   base.rstrip("/"),
        "post_logout_redirect_uri": base.rstrip("/"),
        "convert_from_confidential": convert,
    }


DASHBOARD_CLIENTS = [
    _client("moh-staff",      "internal-bi", _DASH_BASE["internal-bi"], True),
    _client("moh-staff",      "workshop",    _DASH_BASE["workshop"],    False),
    _client("privileged-ops", "admin",       _DASH_BASE["admin"],       False),
]

# Confidential tool clients (dbt-docs, Airflow, JupyterHub, Superset, OpenMetadata).
# Each runs its own OIDC handshake and is admitted silently off the shared
# Keycloak SSO cookie. dbt-docs is seeded in both staff realms so the redirect
# works whichever realm the user's session is in; Airflow is operator-only
# (privileged-ops). Secret comes from env (matches the tool's k8s Secret).
_DBT_DOCS_BASE = f"https://dbt-docs.{_HOST_BASE}"
TOOL_CLIENTS = [
    {"realm": "privileged-ops", "client_id": "dbt-docs", "base": _DBT_DOCS_BASE, "secret": DBT_DOCS_OIDC_SECRET},
    {"realm": "moh-staff",      "client_id": "dbt-docs", "base": _DBT_DOCS_BASE, "secret": DBT_DOCS_OIDC_SECRET},
    # Airflow native OIDC (privileged-ops). FAB's callback is
    # /oauth-authorized/keycloak (covered by base + /*).
    {"realm": "privileged-ops", "client_id": "airflow",
     "base": f"https://airflow.{_HOST_BASE}", "secret": AIRFLOW_OIDC_SECRET},
    # JupyterHub (moh-staff, workshop users). Callback is /hub/oauth_callback
    # (covered by base + /*). Overrides the :8089 redirect in the realm-config
    # JSON.
    {"realm": "moh-staff", "client_id": "jupyterhub",
     "base": f"https://jupyter.{_HOST_BASE}", "secret": JUPYTERHUB_OIDC_SECRET},
    # Superset native FAB OIDC (moh-staff, analyst audience). FAB's callback is
    # /oauth-authorized/keycloak (covered by base + /*). Same pattern as Airflow.
    {"realm": "moh-staff", "client_id": "superset",
     "base": f"https://superset.{_HOST_BASE}", "secret": SUPERSET_OIDC_SECRET},
    # OpenMetadata (moh-staff), confidential: the OM server runs the exchange at
    # /callback, then forwards the browser to the UI's /auth/callback with a
    # session cookie (both covered by base + /*). OM needs OIDC_SERVER_URL set to
    # this same base or its login rejects the UI's redirect ("must exactly match
    # a trusted redirect URI"). No PKCE on this client: Keycloak enforces the
    # attribute against the server-side flow, which does not send a challenge.
    {"realm": "moh-staff", "client_id": "openmetadata",
     "base": f"https://openmetadata.{_HOST_BASE}", "secret": OPENMETADATA_OIDC_SECRET},
]

# Tool-gate clients for the nhdw/tool-gate reverse proxies (main.py). Same
# confidential-client shape as TOOL_CLIENTS, but the gate's callback is
# /oauth2gate/callback (covered by base + /*). Seeded in both staff realms so
# the shared SSO cookie admits whichever realm the operator's session is in
# (an ops role can be in either realm; see the *_GATE_OIDC_SECRET note above).
# The gate's OIDC_CLIENT_ID (tool-gates.yaml) must equal client_id here.
_GATE_BASES = {
    "mlflow":       f"https://mlflow.{_HOST_BASE}",
    "prometheus":   f"https://prometheus.{_HOST_BASE}",
    "alertmanager": f"https://alertmanager.{_HOST_BASE}",
    # Headlamp's gate lives at /oauth2gate + /k8s on the admin host (path-routed
    # beside the admin SPA), so its redirect base is the admin host.
    "headlamp":     f"https://admin.{_HOST_BASE}",
    "seaweedfs":    f"https://seaweedfs.{_HOST_BASE}",
}
_GATE_SECRETS = {
    "mlflow":       MLFLOW_GATE_OIDC_SECRET,
    "prometheus":   PROMETHEUS_GATE_OIDC_SECRET,
    "alertmanager": ALERTMANAGER_GATE_OIDC_SECRET,
    "headlamp":     HEADLAMP_GATE_OIDC_SECRET,
    "seaweedfs":    SEAWEEDFS_GATE_OIDC_SECRET,
}
GATE_CLIENTS = [
    {"realm": realm, "client_id": cid, "base": _GATE_BASES[cid], "secret": _GATE_SECRETS[cid]}
    for cid in ("mlflow", "prometheus", "alertmanager", "headlamp", "seaweedfs")
    for realm in ("moh-staff", "privileged-ops")
]


def login() -> str:
    """Get an admin access token via the master realm."""
    body = urllib.parse.urlencode({
        "grant_type": "password",
        "client_id":  "admin-cli",
        "username":   ADMIN_USERNAME,
        "password":   ADMIN_PASSWORD,
    }).encode()
    req = urllib.request.Request(
        f"{KC_BASE}/realms/master/protocol/openid-connect/token",
        method="POST", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["access_token"]


def api(token: str, method: str, path: str, body: dict | list | None = None) -> tuple[int, dict | list | None]:
    """Call the admin API. Returns (status_code, parsed_json_or_None)."""
    data = None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{KC_BASE}/admin{path}",
                                  method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw) if raw else None
        except Exception:                                       # noqa: BLE001
            payload = {"raw": raw.decode(errors="replace")}
        return e.code, payload


# realm roles
def ensure_realm_role(token: str, realm: str, role_name: str) -> None:
    code, _ = api(token, "GET", f"/realms/{realm}/roles/{role_name}")
    if code == 200:
        print(f"   , realm-role exists: {realm}/{role_name}")
        return
    code, payload = api(token, "POST", f"/realms/{realm}/roles",
                         {"name": role_name})
    if code in (201, 204):
        print(f"    ok created realm-role: {realm}/{role_name}")
    else:
        print(f"    FAIL failed to create {realm}/{role_name}: {code} {payload}")
        sys.exit(1)


def realm_role_obj(token: str, realm: str, role_name: str) -> dict:
    code, payload = api(token, "GET", f"/realms/{realm}/roles/{role_name}")
    if code != 200:
        raise RuntimeError(f"missing realm role {realm}/{role_name}")
    assert isinstance(payload, dict)
    return payload


# users
# Plain POST /admin/realms/{realm}/users ignores a posted `id`; Keycloak
# assigns a UUID. To pin the user's UUID to the kc_sub in users.yaml (so
# nhdw_ops lookups by kc_sub stay consistent) this uses partialImport, which
# honours explicit ids.

def import_users_for_realm(token: str, realm: str, users: list[dict]) -> None:
    """Bulk-upsert users via partialImport with skip-on-conflict; followed
    by a per-user password reset (partialImport doesn't take credentials)."""
    if not users:
        return
    # If any user in this realm carries `mfa: true`, the CONFIGURE_TOTP
    # required-action provider must be registered in the realm first. These
    # realms are minimal (webauthn only); assigning CONFIGURE_TOTP to a user is a
    # silent no-op unless the provider exists, so register and enable it
    # (idempotent; a 409 is fine) before the user import references it.
    if any(u.get("mfa") for u in users):
        api(token, "POST", f"/realms/{realm}/authentication/register-required-action",
            {"providerId": "CONFIGURE_TOTP", "name": "Configure OTP"})
        api(token, "PUT", f"/realms/{realm}/authentication/required-actions/CONFIGURE_TOTP",
            {"alias": "CONFIGURE_TOTP", "name": "Configure OTP", "providerId": "CONFIGURE_TOTP",
             "enabled": True, "defaultAction": False, "priority": 10, "config": {}})
        print(f"    ok CONFIGURE_TOTP required-action ensured in {realm} (MFA)")
    # Build the partialImport payload.
    payload = {
        "ifResourceExists": "OVERWRITE",
        "users": [{
            "id":             u["kc_sub"],
            "username":       u["username"],
            "email":          u.get("email", f"{u['username']}@nhdw.local"),
            "firstName":      (u.get("full_name") or u["username"]).split(" ")[0] or u["username"],
            "lastName":       " ".join((u.get("full_name") or u["username"]).split(" ")[1:]) or "Dev",
            "emailVerified":  True,
            "enabled":        True,
            # Clear required actions so seeded users are never forced into the
            # privileged-ops realm's default passkey registration
            # (webauthn-register-passwordless) on first login. Exception: a user
            # with `mfa: true` in users.yaml must enrol TOTP at next login.
            "requiredActions": (["CONFIGURE_TOTP"] if u.get("mfa") else []),
            "realmRoles":     u.get("roles", []),
        } for u in users],
    }
    code, resp = api(token, "POST", f"/realms/{realm}/partialImport", payload)
    if code not in (200, 204):
        print(f"    FAIL partialImport {realm}: {code} {resp}")
        sys.exit(1)
    print(f"    ok imported {len(users)} users into {realm}")

    # partialImport does not carry passwords or finalise role assignments
    # against existing realm roles, so set the password and reapply roles.
    for u in users:
        kc_sub, username = u["kc_sub"], u["username"]
        code, payload = api(token, "PUT",
                            f"/realms/{realm}/users/{kc_sub}/reset-password",
                            {"type": "password", "value": u.get("password") or SEED_PASSWORD,
                             "temporary": False})
        if code not in (204, 200):
            print(f"      FAIL password {realm}/{username}: {code} {payload}")
            sys.exit(1)

        # Refresh role mappings idempotently.
        roles = [realm_role_obj(token, realm, r) for r in u.get("roles", [])]
        if roles:
            code, payload = api(token, "POST",
                                f"/realms/{realm}/users/{kc_sub}/role-mappings/realm",
                                roles)
            if code not in (204, 200):
                print(f"      FAIL roles {realm}/{username}: {code} {payload}")
                sys.exit(1)
        print(f"      ok {realm}/{username} ({kc_sub[:8]}...) roles={u.get('roles', [])}")


# identity-provider brokers
def configure_idp(token: str, spec: dict) -> None:
    """Idempotently configure a broker on `into_realm`:
      - kind='google' -> Keycloak's built-in Google social provider (real Google);
      - kind='oidc'/'saml' -> a broker to a mocked source realm in this instance."""
    into = spec["into_realm"]
    alias = spec["alias"]
    source = spec.get("source_realm")   # absent for the real-Google broker

    # Check current state.
    code, payload = api(token, "GET",
                        f"/realms/{into}/identity-provider/instances/{alias}")
    exists = code == 200
    existing_pid = payload.get("providerId") if (exists and isinstance(payload, dict)) else None

    # Build provider config.
    if spec["kind"] == "google":
        # Built-in Google social provider: Keycloak presets the Google endpoints
        # and this supplies the client credentials. Redirect URI to register in Google:
        #   {KC_PUBLIC_BASE}/realms/{into}/broker/google/endpoint
        cfg = {
            "clientId":     GOOGLE_CLIENT_ID,
            "clientSecret": GOOGLE_CLIENT_SECRET,
            "defaultScope": "openid profile email",
            "syncMode":     "IMPORT",
        }
        provider_id = "google"
    elif spec["kind"] == "oidc":
        # The `issuer` must match the `iss` claim Keycloak puts in the token,
        # which is set from KC_HOSTNAME (the external URL). Token URLs the
        # broker calls server-to-server can use the internal URL.
        cfg = {
            "issuer":                f"{KC_PUBLIC_BASE}/realms/{source}",
            "authorizationUrl":      f"{KC_PUBLIC_BASE}/realms/{source}/protocol/openid-connect/auth",
            "tokenUrl":              f"http://keycloak:8080/realms/{source}/protocol/openid-connect/token",
            "userInfoUrl":           f"http://keycloak:8080/realms/{source}/protocol/openid-connect/userinfo",
            "jwksUrl":               f"http://keycloak:8080/realms/{source}/protocol/openid-connect/certs",
            "logoutUrl":             f"{KC_PUBLIC_BASE}/realms/{source}/protocol/openid-connect/logout",
            "clientId":              spec["client_id"],
            "clientSecret":          spec["client_secret"],
            "defaultScope":          "openid profile email",
            "validateSignature":     "false",
            "useJwksUrl":            "true",
            "syncMode":              "FORCE",
            "backchannelSupported":  "false",
        }
        provider_id = "oidc"
    else:  # saml
        cfg = {
            "singleSignOnServiceUrl":  f"{KC_PUBLIC_BASE}/realms/{source}/protocol/saml",
            "singleLogoutServiceUrl":  f"{KC_PUBLIC_BASE}/realms/{source}/protocol/saml",
            "entityId":                f"{KC_PUBLIC_BASE}/realms/{into}",
            "nameIDPolicyFormat":      "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            "postBindingResponse":     "true",
            "postBindingAuthnRequest": "true",
            "postBindingLogout":       "true",
            "wantAssertionsSigned":    "false",
            "wantAuthnRequestsSigned": "false",
            "validateSignature":       "false",
            "syncMode":                "FORCE",
        }
        provider_id = "saml"

    body = {
        "alias":        alias,
        "displayName":  spec["display"],
        "providerId":   provider_id,
        "enabled":      True,
        # Only trusted IdPs mark email verified. A low-trust broker (public
        # Google) must not have its email auto-verified.
        "trustEmail":   bool(spec.get("trust_email", False)),
        "storeToken":   False,
        "addReadTokenRoleOnCreate": False,
        "firstBrokerLoginFlowAlias": "first broker login",
        "config":       cfg,
    }

    # Keycloak will not change a broker's providerId (type) in place. If the
    # type changed (e.g. an `oidc` broker replaced by the built-in `google`
    # provider), the instance must be recreated or the new config is
    # silently dropped.
    if exists and existing_pid != provider_id:
        api(token, "DELETE", f"/realms/{into}/identity-provider/instances/{alias}")
        exists = False

    dst = source or provider_id   # mock source realm, or the provider type (real Google)
    if exists:
        code, payload = api(token, "PUT",
                            f"/realms/{into}/identity-provider/instances/{alias}",
                            body)
        if code not in (200, 204):
            print(f"    FAIL update broker {into}/{alias}: {code} {payload}")
            sys.exit(1)
        print(f"    ok updated broker {into}/{alias} -> {dst}")
    else:
        code, payload = api(token, "POST",
                            f"/realms/{into}/identity-provider/instances",
                            body)
        if code not in (201, 204):
            print(f"    FAIL create broker {into}/{alias}: {code} {payload}")
            sys.exit(1)
        print(f"    ok created broker {into}/{alias} -> {dst}")

    # Default-role mapper: every brokered user gets `default_role` on first
    # login. Provider-aware (SAML vs OIDC); see ensure_default_role_mapper.
    drole = spec.get("default_role")
    if drole:
        ensure_default_role_mapper(token, into, alias, drole, spec["kind"])

    # Stamp the broker's trust tier onto every user it federates.
    ensure_idp_trust_mapper(token, into, alias, spec.get("trust_tier", "low"))


# SAML brokers have no hardcoded-role IdP mapper in Keycloak 26; only
# `oidc-hardcoded-role-idp-mapper` exists (OIDC/social). The generic
# `hardcoded-role-idp-mapper` id is unregistered, so a broker carrying it throws
# `NullPointerException ... preprocessFederatedIdentity ... target is null` and
# federation fails outright. To hardcode a role for a SAML-federated identity,
# the mock IdP's SP client emits a constant SAML attribute
# (saml-hardcode-attribute-mapper, added in configure_broker_source_clients) and
# `saml-role-idp-mapper` grants the role off that attribute. The marker name and
# value must match on both sides.
SAML_ROLE_MARKER_ATTR  = "nhdw-broker-role-marker"
SAML_ROLE_MARKER_VALUE = "ehia-federated"


def ensure_default_role_mapper(token: str, realm: str, alias: str, role: str, kind: str) -> None:
    """Grant `role` to every user federated through `alias` on first login.

    Provider-aware, because Keycloak 26 registers different role mappers per
    broker type (there is no generic hardcoded-role mapper):
      - OIDC/social (Google) -> `oidc-hardcoded-role-idp-mapper` (unconditional).
      - SAML (EHIA) -> `saml-role-idp-mapper`, keyed on the constant SAML marker
        attribute the SP client emits.
    Self-healing: if a same-named mapper already exists with the WRONG type
    (e.g. the unregistered `hardcoded-role-idp-mapper` left by a prior seed), it
    is deleted and recreated with the correct provider mapper."""
    mapper_name = f"default-{role}"
    if kind == "saml":
        idp_mapper = "saml-role-idp-mapper"
        cfg = {
            "attribute.name":  SAML_ROLE_MARKER_ATTR,
            "attribute.value": SAML_ROLE_MARKER_VALUE,
            "role":            role,
            "syncMode":        "INHERIT",
        }
    else:  # google / oidc social
        idp_mapper = "oidc-hardcoded-role-idp-mapper"
        cfg = {
            "role":     role,
            "syncMode": "INHERIT",
        }
    code, mappers = api(token, "GET",
                        f"/realms/{realm}/identity-provider/instances/{alias}/mappers")
    if code == 200 and isinstance(mappers, list):
        for m in mappers:
            if m.get("name") != mapper_name:
                continue
            if m.get("identityProviderMapper") == idp_mapper:
                return  # correct mapper already present
            # Wrong type: delete so it can be recreated with the registered mapper.
            api(token, "DELETE",
                f"/realms/{realm}/identity-provider/instances/{alias}/mappers/{m.get('id')}")
            break
    body = {
        "name":                   mapper_name,
        "identityProviderAlias":  alias,
        "identityProviderMapper": idp_mapper,
        "config":                 cfg,
    }
    code, payload = api(token, "POST",
                        f"/realms/{realm}/identity-provider/instances/{alias}/mappers",
                        body)
    if code not in (201, 204):
        print(f"    FAIL default-role mapper {realm}/{alias}/{role}: {code} {payload}")
        sys.exit(1)
    print(f"     , default-role mapper -> {role} ({idp_mapper})")


def ensure_unmanaged_attributes(token: str, realm: str) -> None:
    """Allow unmanaged user attributes in the realm. Keycloak 26's
    declarative user profile drops any attribute not explicitly declared, so
    the `idp_trust` attribute set by the IdP mapper (and the protocol mapper that
    lifts it into the token) is silently discarded unless the realm's
    unmanagedAttributePolicy is enabled. Idempotent GET-set-PUT."""
    code, cfg = api(token, "GET", f"/realms/{realm}/users/profile")
    if code != 200 or not isinstance(cfg, dict):
        print(f"    ! user-profile GET failed for {realm} ({code}); skipping unmanaged-attrs")
        return
    if cfg.get("unmanagedAttributePolicy") == "ENABLED":
        return
    cfg["unmanagedAttributePolicy"] = "ENABLED"
    code, payload = api(token, "PUT", f"/realms/{realm}/users/profile", cfg)
    print(f"    ok unmanaged attributes ENABLED -> {realm}"
          if code in (200, 204) else f"    FAIL unmanaged attrs {realm}: {code} {payload}")


def ensure_idp_trust_mapper(token: str, realm: str, alias: str, tier: str) -> None:
    """Stamp a hardcoded `idp_trust` user attribute (= tier) on every user
    brokered through `alias`. A protocol mapper on the nhdw-flat-roles
    client scope then lifts it into the token, where the OPA trust guard reads
    it. The generic hardcoded-attribute mapper works for OIDC and SAML brokers."""
    mapper_name = "idp-trust"
    code, mappers = api(token, "GET",
                        f"/realms/{realm}/identity-provider/instances/{alias}/mappers")
    if code == 200 and isinstance(mappers, list):
        for m in mappers:
            if m.get("name") == mapper_name:
                return
    body = {
        "name":                   mapper_name,
        "identityProviderAlias":  alias,
        "identityProviderMapper": "hardcoded-attribute-idp-mapper",
        "config": {
            "attribute":       "idp_trust",
            "attribute.value": tier,
            "syncMode":        "INHERIT",
        },
    }
    code, payload = api(token, "POST",
                        f"/realms/{realm}/identity-provider/instances/{alias}/mappers",
                        body)
    print(f"     , idp-trust mapper -> {tier}"
          if code in (201, 204) else f"    FAIL idp-trust mapper {realm}/{alias}: {code} {payload}")


def ensure_idp_trust_claim_mapper(token: str, realm: str) -> None:
    """Add a protocol mapper to the realm's `nhdw-flat-roles` client scope that
    lifts the `idp_trust` user attribute into the token as an `idp_trust` claim
    (access token + userinfo + id token). Password-only users have no such
    attribute -> no claim -> the gateway defaults them to "high"."""
    _, scopes = api(token, "GET", f"/realms/{realm}/client-scopes")
    scope = next((s for s in (scopes or []) if s.get("name") == "nhdw-flat-roles"), None)
    if not scope:
        print(f"    ! nhdw-flat-roles scope absent in {realm}; idp_trust claim skipped")
        return
    existing = {m.get("name") for m in (scope.get("protocolMappers") or [])}
    if "idp_trust" in existing:
        return
    body = {
        "name":           "idp_trust",
        "protocol":       "openid-connect",
        "protocolMapper": "oidc-usermodel-attribute-mapper",
        "config": {
            "user.attribute":      "idp_trust",
            "claim.name":          "idp_trust",
            "jsonType.label":      "String",
            "id.token.claim":      "true",
            "access.token.claim":  "true",
            "userinfo.token.claim": "true",
        },
    }
    code, payload = api(token, "POST",
                        f"/realms/{realm}/client-scopes/{scope['id']}/protocol-mappers/models",
                        body)
    print(f"    ok idp_trust claim mapper -> {realm}/nhdw-flat-roles"
          if code in (201, 204) else f"    FAIL idp_trust claim mapper {realm}: {code} {payload}")


# clients
def configure_client(token: str, spec: dict) -> None:
    realm = spec["realm"]
    cid = spec["client_id"]
    code, payload = api(token, "GET", f"/realms/{realm}/clients?clientId={cid}")
    if code != 200:
        print(f"    FAIL lookup client {realm}/{cid}: {code} {payload}")
        sys.exit(1)
    assert isinstance(payload, list)
    desired = {
        "clientId":           cid,
        "enabled":            True,
        "publicClient":       True,
        "standardFlowEnabled": True,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "redirectUris":       [spec["redirect_uri"]],
        "webOrigins":         [spec["web_origin"]],
        "attributes": {
            "pkce.code.challenge.method": "S256",
            "post.logout.redirect.uris":  spec["post_logout_redirect_uri"],
        },
    }
    if not payload:
        code, p2 = api(token, "POST", f"/realms/{realm}/clients", desired)
        if code not in (201, 204):
            print(f"    FAIL create client {realm}/{cid}: {code} {p2}")
            sys.exit(1)
        print(f"    ok created public client {realm}/{cid}")
        return

    # Update in place.
    existing = payload[0]
    merged = {**existing, **desired}
    # Preserve sub-collections we don't want to wipe (none here, but be explicit).
    code, p2 = api(token, "PUT",
                   f"/realms/{realm}/clients/{existing['id']}", merged)
    if code not in (204, 200):
        print(f"    FAIL update client {realm}/{cid}: {code} {p2}")
        sys.exit(1)
    note = " (converted public)" if existing.get("publicClient") is False else ""
    print(f"    ok updated client {realm}/{cid}{note}")


def configure_confidential_client(token: str, spec: dict) -> None:
    """Create/update a confidential OIDC client (has a client secret) for a tool
    like dbt-docs. The secret must match the tool's k8s Secret env."""
    realm, cid = spec["realm"], spec["client_id"]
    base, secret = spec["base"].rstrip("/"), spec["secret"]
    if not secret:
        print(f"    WARN {realm}/{cid}: no confidential secret in env; skipped")
        return
    code, payload = api(token, "GET", f"/realms/{realm}/clients?clientId={cid}")
    desired = {
        "clientId": cid, "enabled": True, "protocol": "openid-connect",
        "publicClient": False, "secret": secret,
        "standardFlowEnabled": True, "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "redirectUris": [base + "/*"], "webOrigins": [base],
        "attributes": {"post.logout.redirect.uris": base},
    }
    if isinstance(payload, list) and payload:
        internal_id = payload[0]["id"]
        api(token, "PUT", f"/realms/{realm}/clients/{internal_id}", {**payload[0], **desired})
        print(f"    ok updated confidential client {realm}/{cid}")
    else:
        api(token, "POST", f"/realms/{realm}/clients", desired)
        gc, got = api(token, "GET", f"/realms/{realm}/clients?clientId={cid}")
        internal_id = got[0]["id"] if isinstance(got, list) and got else None
        print(f"    ok created confidential client {realm}/{cid}")
    # Standard OIDC login needs the profile/email/basic scopes assigned as
    # default client scopes. A new client only inherits the realm's
    # `nhdw-flat-roles` default, so requesting `openid profile email` would fail
    # with `invalid_scope`. Match the dashboard clients: basic, profile, email,
    # nhdw-flat-roles.
    if internal_id:
        ensure_default_client_scopes(token, realm, internal_id,
                                     ["basic", "profile", "email", "nhdw-flat-roles"])


def ensure_default_client_scopes(token: str, realm: str, client_internal_id: str,
                                 scope_names: list[str]) -> None:
    """Assign the named realm client-scopes as DEFAULT scopes of a client
    (idempotent; skips any scope the realm doesn't define)."""
    code, scopes = api(token, "GET", f"/realms/{realm}/client-scopes")
    by_name = {s["name"]: s["id"] for s in scopes} if isinstance(scopes, list) else {}
    for name in scope_names:
        sid = by_name.get(name)
        if not sid:
            continue
        api(token, "PUT",
            f"/realms/{realm}/clients/{client_internal_id}/default-client-scopes/{sid}")


# broker source-realm client hostnames
def _broker_intos(alias: str) -> list[str]:
    """The realms an IdP `alias` is brokered into (from IDENTITY_PROVIDERS)."""
    return [p["into_realm"] for p in IDENTITY_PROVIDERS if p["alias"] == alias]


def configure_broker_source_clients(token: str) -> None:
    """Rewrite the mock ehia-saml-mock realm's SAML SP clients to the external
    KC_PUBLIC_BASE hostname.

    The mock realm JSON ships `http://127.0.0.1:8090/...` client registrations,
    fixed at import time. With an external KC_PUBLIC_BASE (e.g.
    https://keycloak.127.0.0.1.nip.io) the source realm must accept the broker
    callback `{KC_PUBLIC_BASE}/realms/{into}/broker/ehia-saml/endpoint`; without
    this rewrite, EHIA SAML federation fails the SP entityID/ACS match. Mirrors
    configure_client: the JSON owns client structure, this script owns the
    hostname (from env). Idempotent. Google has no mock source client."""
    kb = KC_PUBLIC_BASE.rstrip("/")

    # ehia-saml-mock: one SAML SP client per brokered realm; the client's
    # clientId is the SP entityID and must equal the broker's `entityId`
    # ({kb}/realms/{into}); the POST-binding ACS is the broker endpoint.
    e_intos = _broker_intos("ehia-saml")
    if e_intos:
        code, all_clients = api(token, "GET", "/realms/ehia-saml-mock/clients")
        clients = all_clients if isinstance(all_clients, list) else []
        for into in e_intos:
            new_entity = f"{kb}/realms/{into}"
            acs = f"{kb}/realms/{into}/broker/ehia-saml/endpoint"
            legacy = f"http://127.0.0.1:8090/realms/{into}"
            match = next((c for c in clients if c.get("clientId") in (new_entity, legacy)), None)
            if not match:
                print(f"    ! ehia-saml-mock SP for {into} not found; skipping"); continue
            match["clientId"] = new_entity
            attrs = match.setdefault("attributes", {})
            attrs["saml_assertion_consumer_url_post"] = acs
            attrs["saml_single_logout_service_url_post"] = acs
            match["redirectUris"] = [acs, f"http://keycloak:8080/realms/{into}/broker/ehia-saml/endpoint"]
            # Emit a constant SAML attribute in the assertion so the broker's
            # saml-role-idp-mapper can hardcode the federated user's default role
            # (Keycloak 26 has no hardcoded-role IdP mapper for SAML). Idempotent.
            pms = match.setdefault("protocolMappers", [])
            if not any(pm.get("name") == SAML_ROLE_MARKER_ATTR for pm in pms):
                pms.append({
                    "name":           SAML_ROLE_MARKER_ATTR,
                    "protocol":       "saml",
                    "protocolMapper": "saml-hardcode-attribute-mapper",
                    "config": {
                        "attribute.name":       SAML_ROLE_MARKER_ATTR,
                        "attribute.nameformat": "Basic",
                        "attribute.value":      SAML_ROLE_MARKER_VALUE,
                        "friendly.name":        SAML_ROLE_MARKER_ATTR,
                    },
                })
            code, p = api(token, "PUT", f"/realms/ehia-saml-mock/clients/{match['id']}", match)
            if code not in (200, 204):
                print(f"    FAIL ehia-saml-mock SP {into}: {code} {p}"); sys.exit(1)
            print(f"    ok ehia-saml-mock SP {into} entityID/ACS -> {kb}")


def prune_unexpected_brokers(token: str, realm: str) -> None:
    """Delete any brokered IdP on `realm` that is not configured from
    IDENTITY_PROVIDERS, so a stray IdP left by an earlier seed or a realm import
    cannot add an unintended login. The expected brokers (google, ehia-saml) are
    kept: on privileged-ops they land every brokered user on R-OPS-NONE (zero
    grants, OPA default-deny), so the admin login page can show the Google/EHIA
    buttons without granting operator or admin access.

    When Google credentials are absent or a placeholder, the google broker is not
    expected (main skips it), so a stale google instance is pruned too: no
    credentials means no Google button."""
    expected = {p["alias"] for p in IDENTITY_PROVIDERS if p["into_realm"] == realm}
    if not _google_configured():
        expected.discard("google")
    code, idps = api(token, "GET", f"/realms/{realm}/identity-provider/instances")
    if code != 200 or not isinstance(idps, list):
        return
    for idp in idps:
        alias = idp.get("alias")
        if alias in expected:
            continue
        code2, _ = api(token, "DELETE", f"/realms/{realm}/identity-provider/instances/{alias}")
        if code2 in (200, 204):
            print(f"    ok removed UNEXPECTED broker {realm}/{alias} (not in IDENTITY_PROVIDERS)")
        else:
            print(f"    ! could not remove broker {realm}/{alias}: {code2}")


# main
# Friendly names shown in the login header (below the FMoH logo the nhdw theme
# injects). Only the two staff realms exist; Fayda is a verification-only
# integration on the public access-request form, not a login realm.
REALM_DISPLAY = {
    "moh-staff":      "Palladium",
    "privileged-ops": "Palladium",
}


def configure_realm_theme(token: str, realm: str) -> None:
    """Brand the realm's hosted login page: set loginTheme=nhdw + a friendly
    displayName. GET-merge-PUT so no other realm settings are clobbered."""
    code, rep = api(token, "GET", f"/realms/{realm}")
    if code != 200 or not isinstance(rep, dict):
        print(f"    FAIL get realm {realm}: {code}")
        return
    rep["loginTheme"] = "nhdw"
    disp = REALM_DISPLAY.get(realm)
    if disp:
        rep["displayName"] = disp
    code, _ = api(token, "PUT", f"/realms/{realm}", rep)
    if code in (200, 204):
        print(f"    ok realm {realm}: loginTheme=nhdw"
              + (f", displayName={disp!r}" if disp else ""))
    else:
        print(f"    FAIL update realm {realm}: {code}")


# Point each realm's SMTP at the shared relay (Google SMTP) so Keycloak can
# deliver execute-actions-email (set-password and reset links) for
# access-request provisioning. Same env contract and smtp-secrets Secret as the
# gateway mailer: one relay for every application and realm email.
def _realm_smtp() -> dict:
    """Keycloak smtpServer config from the SMTP_* env (smtp-secrets Secret):
    Google SMTP (starttls + auth)."""
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = os.environ.get("SMTP_PORT", "587")
    user = os.environ.get("SMTP_USER", "").strip()
    pw   = os.environ.get("SMTP_PASS", "")
    tls  = os.environ.get("SMTP_TLS", "true").strip().lower() in ("1", "true", "yes")
    s = {
        "host": host, "port": port,
        # Gmail requires From == the authenticated account.
        "from": user or "noreply@nhdw.moh.gov.et",
        "fromDisplayName": "Ethiopia NHDW",
        "ssl": "false",
        "starttls": "true" if tls else "false",
        "auth": "true" if user else "false",
    }
    if user:
        s["user"] = user
        s["password"] = pw
    return s


def configure_realm_smtp(token: str, realm: str) -> None:
    """Point the realm's SMTP at the external Google relay (smtp-secrets) so
    execute-actions-email (password-set / reset links) delivers. GET-merge-PUT so
    no other realm settings are clobbered (idempotent)."""
    smtp = _realm_smtp()
    code, rep = api(token, "GET", f"/realms/{realm}")
    if code != 200 or not isinstance(rep, dict):
        print(f"    FAIL get realm {realm}: {code}")
        return
    rep["smtpServer"] = smtp
    code, _ = api(token, "PUT", f"/realms/{realm}", rep)
    print((f"    ok realm {realm}: SMTP -> {smtp['host']}:{smtp['port']} (auth={smtp['auth']})"
           if code in (200, 204) else f"    FAIL set SMTP {realm}: {code}"))


# The dashboard SPAs request `openid profile email` plus the custom
# `nhdw-flat-roles` scope (which carries the flat `roles` claim the gateway
# reads). The minimal privileged-ops realm import ships only the `basic` scope,
# so without this its `admin` client login fails with
# `invalid_scope: profile email` and the admin dashboard is unreachable. Copy
# the missing scope definitions (with their protocol mappers) from the
# moh-staff realm and assign them as client defaults. Idempotent GET-copy-PUT:
# it repairs on every re-seed and works on a cold boot (moh-staff's scopes exist
# from its own realm import by the time keycloak-seed runs).
SCOPE_REALM_NEEDS = {
    "privileged-ops": ["nhdw-flat-roles", "profile", "email"],
}
SCOPE_REALM_CLIENTS = ("admin", "ops-hub", "cloudbeaver", "openbao")


def configure_realm_scopes(token: str, realm: str, src: str = "moh-staff") -> None:
    names = SCOPE_REALM_NEEDS.get(realm, [])
    if not names:
        return
    _, dst = api(token, "GET", f"/realms/{realm}/client-scopes")
    _, srcs = api(token, "GET", f"/realms/{src}/client-scopes")
    have = {c["name"]: c["id"] for c in (dst or [])}
    src_by = {c["name"]: c for c in (srcs or [])}
    for name in names:
        if name in have:
            continue
        rep = dict(src_by.get(name) or {})
        if not rep:
            print(f"    FAIL scope {name} missing in {src}; cannot copy")
            continue
        rep.pop("id", None)
        for m in rep.get("protocolMappers", []) or []:
            m.pop("id", None)
        code, _ = api(token, "POST", f"/realms/{realm}/client-scopes", rep)
        print(f"    {'ok' if code in (201, 204) else 'FAIL'} scope {name} -> {realm} ({code})")
    _, dst = api(token, "GET", f"/realms/{realm}/client-scopes")
    have = {c["name"]: c["id"] for c in (dst or [])}
    _, clients = api(token, "GET", f"/realms/{realm}/clients")
    for c in (clients or []):
        if c.get("clientId") in SCOPE_REALM_CLIENTS:
            for name in names:
                sid = have.get(name)
                if sid:
                    api(token, "PUT",
                        f"/realms/{realm}/clients/{c['id']}/default-client-scopes/{sid}")
    print(f"    ok realm {realm}: default scopes {names} assigned to dashboard clients")


def main() -> int:
    if not USERS_YAML.exists():
        print(f"missing users.yaml: {USERS_YAML}")
        return 2

    users = yaml.safe_load(USERS_YAML.read_text())
    token = login()
    print(f"connected to Keycloak at {KC_BASE} as {ADMIN_USERNAME}")

    print("\nrealm roles")
    # MISSING_REALM_ROLES is the floor, not the list. Every role the loaded cast
    # actually references must exist in its realm or the assignment silently
    # fails, and which roles those are depends on the country: zm-v1's cast holds
    # R-PHO-LUSAKA where et-v3's holds R-RHB-OROMIA. Deriving the extras from the
    # users we just read means a new profile never needs this file edited.
    needed: dict[str, set[str]] = {r: set(v) for r, v in MISSING_REALM_ROLES.items()}
    for u in users:
        realm = u.get("realm")
        if not realm:
            continue
        needed.setdefault(realm, set()).update(u.get("roles") or [])
    for realm, roles in needed.items():
        for r in sorted(roles):
            ensure_realm_role(token, realm, r)

    print("\nunmanaged user attributes (allow idp_trust to persist)")
    for realm in ("moh-staff", "privileged-ops"):
        ensure_unmanaged_attributes(token, realm)

    print(f"\nusers ({len(users)} from users.yaml)")
    by_realm: dict[str, list[dict]] = {}
    for u in users:
        by_realm.setdefault(u["realm"], []).append(u)
    for realm, group in by_realm.items():
        print(f"  {realm} ({len(group)} users)")
        import_users_for_realm(token, realm, group)

    print(f"\ndashboard clients ({len(DASHBOARD_CLIENTS)})")
    for c in DASHBOARD_CLIENTS:
        configure_client(token, c)

    # privileged-ops has no profile/email/nhdw-flat-roles client scopes of its
    # own; they are copied from moh-staff here. This must run before the tool and
    # gate confidential clients below: each of those calls
    # ensure_default_client_scopes([basic, profile, email, nhdw-flat-roles]) and
    # can only assign scopes that already exist in the realm. Otherwise every
    # privileged-ops tool client keeps only `basic`, and a login requesting
    # `openid profile email` is rejected with invalid_scope ("missing code/state").
    # Dashboard clients already exist by now, so configure_realm_scopes' own
    # client-assignment step still lands.
    print("\nrealm client scopes (privileged-ops: copy profile/email/roles from moh-staff)")
    for realm in ("privileged-ops",):
        configure_realm_scopes(token, realm)

    print(f"\ntool clients ({len(TOOL_CLIENTS)})")
    for c in TOOL_CLIENTS:
        configure_confidential_client(token, c)

    print(f"\ntool-gate clients ({len(GATE_CLIENTS)})")
    for c in GATE_CLIENTS:
        configure_confidential_client(token, c)

    # Skip the Google broker if no real credentials were provided (no mock;
    # social login is absent and the rest of auth still works).
    providers = list(IDENTITY_PROVIDERS)
    if not _google_configured():
        providers = [p for p in providers if p["alias"] != "google"]
        print("  ! GOOGLE_CLIENT_ID unset/placeholder; skipping the Google social "
              "broker (no mock fallback).")
    print(f"\nidentity-provider brokers ({len(providers)})")
    for idp in providers:
        configure_idp(token, idp)

    print("\nbroker source-realm client hostnames (mock IdPs -> KC_PUBLIC_BASE)")
    configure_broker_source_clients(token)

    # privileged-ops also brokers google and ehia-saml (buttons visible on the
    # admin login page), but every brokered identity lands on R-OPS-NONE (zero
    # grants, so OPA denies). Prune only unexpected brokers, keeping the ones
    # just configured while removing any stray IdP an earlier seed left behind.
    print("\nadmin realm brokers (keep google+ehia-saml, prune any unexpected)")
    prune_unexpected_brokers(token, "privileged-ops")

    print("\nidp_trust claim (trust guard), nhdw-flat-roles scope, both realms")
    for realm in ("moh-staff", "privileged-ops"):
        ensure_idp_trust_claim_mapper(token, realm)

    print("\nrealm branding (nhdw login theme)")
    for realm in ("moh-staff", "privileged-ops"):
        configure_realm_theme(token, realm)

    print("\nrealm SMTP (external Google relay for provisioning and reset emails)")
    for realm in ("moh-staff", "privileged-ops"):
        configure_realm_smtp(token, realm)

    print("\nok Keycloak setup complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
