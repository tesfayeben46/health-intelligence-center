"""NHDW JupyterHub: the in-cluster Hub (Kubernetes, KubeSpawner).

Auth:    GenericOAuthenticator against Keycloak's `moh-staff` realm
         (confidential client `jupyterhub`; the secret comes from
         jupyterhub-secrets, seeded by setup-keycloak.py like the other tool
         clients).
Spawner: KubeSpawner. The profile picker reads the catalogue at
         /srv/jupyterhub/profiles.yaml (the `workspace-profiles` configMap,
         the same file the gateway's /api/workspace reads), filtered per role
         by each profile's `self_serve_roles`.
Projects: the gateway's POST /api/workspace/spawn passes user_options
         {project_id, project_slug}; the pre-spawn hook turns them into
         NHDW_PROJECT_* env, and the singleuser entrypoint syncs the project's
         files/ prefix in the object store (SeaweedFS, S3 API) into the
         workdir: pull on start, then a background push loop.
Creds:   static read-only warehouse access (bi_reader, Trino) passed through
         from the hub's own environment. Short-lived OpenBao credentials are
         not used.
"""
from __future__ import annotations

import os
import sys
from typing import Any

import yaml
from oauthenticator.generic import GenericOAuthenticator

# Hub network identity
# One pod runs hub and proxy: proxy-public on :8000 (ingress target), hub API
# on :8081. The in-pod configurable-http-proxy and the singleuser pods reach
# the Hub API by the Hub's pod IP, not the `jupyterhub` Service ClusterIP: a
# pod connecting to its own Service ClusterIP needs hairpin NAT, which k3d
# refuses, and the proxy's `/hub/` route would return 502. The pod IP is
# routable from the same pod and pod-to-pod. (zero-to-jupyterhub avoids this
# by running the proxy in its own pod; here one pod plus hub_connect_ip.)
c.JupyterHub.bind_url = "http://:8000"
c.JupyterHub.hub_bind_url = "http://:8081"
c.JupyterHub.hub_connect_ip = os.environ.get("POD_IP") or None
c.JupyterHub.cleanup_servers = False
# One lab per project: every lab is a named server whose name is the project id
# (the gateway spawns servers/<project_id>). Distinct projects get distinct
# pods and PVCs and never share a workdir, so "Open in JupyterLab" on project X
# always lands in X's files. No cap: a researcher may keep a lab open per
# project.
c.JupyterHub.allow_named_servers = True
c.JupyterHub.named_server_limit_per_user = 0

# OAuth (Keycloak, moh-staff)
KEYCLOAK_BASE        = os.environ.get(
    "KEYCLOAK_BASE", "http://keycloak.nhdw.svc.cluster.local:8080")
KEYCLOAK_PUBLIC_BASE = os.environ.get(
    "KEYCLOAK_PUBLIC_BASE", "https://keycloak.127.0.0.1.nip.io")
JUPYTER_PUBLIC_BASE  = os.environ.get(
    "JUPYTER_PUBLIC_BASE", "https://jupyter.127.0.0.1.nip.io")
REALM         = os.environ.get("OIDC_REALM", "moh-staff")
CLIENT_ID     = os.environ.get("OIDC_CLIENT_ID", "jupyterhub")
CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")

c.JupyterHub.authenticator_class = GenericOAuthenticator
c.GenericOAuthenticator.client_id = CLIENT_ID
c.GenericOAuthenticator.client_secret = CLIENT_SECRET
# The browser-facing authorize URL goes through the public ingress; the token
# and userinfo calls go pod-to-pod.
c.GenericOAuthenticator.oauth_callback_url = f"{JUPYTER_PUBLIC_BASE}/hub/oauth_callback"
c.GenericOAuthenticator.authorize_url = f"{KEYCLOAK_PUBLIC_BASE}/realms/{REALM}/protocol/openid-connect/auth"
c.GenericOAuthenticator.token_url     = f"{KEYCLOAK_BASE}/realms/{REALM}/protocol/openid-connect/token"
c.GenericOAuthenticator.userdata_url  = f"{KEYCLOAK_BASE}/realms/{REALM}/protocol/openid-connect/userinfo"
c.GenericOAuthenticator.scope = ["openid", "profile", "email"]
c.GenericOAuthenticator.username_claim = "preferred_username"
c.GenericOAuthenticator.manage_groups = True


def _roles_from_auth_state(auth_state) -> list:
    """Return the caller's realm roles from the tokens in auth_state.

    NHDW realm roles are in the access token (a JWT), not the userinfo
    response. The seeded realm has no built-in `roles` scope; the
    `nhdw-flat-roles` client scope flattens realm roles into a top-level
    `roles` claim (the gateway reads realm_access.roles or roles the same way).
    Both the access token and the id token are decoded and their roles
    unioned. Signatures are not checked here: the tokens come straight from
    the token endpoint."""
    import base64
    import json as _json
    if not auth_state:
        return []

    def _claims(tok: str) -> dict:
        try:
            payload = tok.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return _json.loads(base64.urlsafe_b64decode(payload.encode()))
        except Exception:                                   # noqa: BLE001
            return {}

    roles: set[str] = set()
    for key in ("access_token", "id_token"):
        c = _claims(auth_state.get(key) or "")
        roles |= set((c.get("realm_access") or {}).get("roles") or [])
        roles |= set(c.get("roles") or [])
    return list(roles)


# Groups are the caller's realm roles.
c.GenericOAuthenticator.auth_state_groups_key = _roles_from_auth_state
# Skip the hub's login page and go straight to Keycloak: the user already has a
# Keycloak session from the workshop dashboard, so "Open in JupyterLab" lands
# in the lab via silent SSO.
c.Authenticator.auto_login = True
# Opening the lab URL with no running server starts it, so "Open in JupyterLab"
# lands in a lab whether or not the gateway pre-spawned it.
c.JupyterHub.implicit_spawn_seconds = 2

# Workshop-class roles may sign in (mirrors the workshop dashboard grants).
c.GenericOAuthenticator.allowed_groups = {
    "R-CONS-ANA", "R-CONS-SCI", "R-CONS-RES", "R-DEC", "R-STEW", "R-SUPER"}
c.GenericOAuthenticator.admin_groups = {"R-SUPER"}
c.Authenticator.enable_auth_state = True

# Gateway service account (POST /api/workspace/* drives the hub API)
GATEWAY_TOKEN = os.environ.get("GATEWAY_API_TOKEN", "")
if GATEWAY_TOKEN:
    c.JupyterHub.services = [{"name": "nhdw-gateway", "api_token": GATEWAY_TOKEN}]
    c.JupyterHub.load_roles = [{
        "name": "gateway-admin",
        "scopes": ["admin:users", "admin:servers"],
        "services": ["nhdw-gateway"],
    }]

# Spawner (KubeSpawner)
c.JupyterHub.spawner_class = "kubespawner.KubeSpawner"
c.KubeSpawner.namespace = os.environ.get("POD_NAMESPACE", "nhdw")
c.KubeSpawner.image = os.environ.get(
    "SINGLEUSER_IMAGE", "nhdw-registry:5500/nhdw/jupyterlab")
c.KubeSpawner.image_pull_policy = "IfNotPresent"
c.KubeSpawner.start_timeout = 300
c.KubeSpawner.http_timeout = 120
c.KubeSpawner.cmd = ["/usr/local/bin/start-nhdw.sh"]
c.KubeSpawner.default_url = "/lab"
c.KubeSpawner.notebook_dir = "/home/jovyan/work"
# Per-user, per-project PVC that survives lab restarts; sized per profile below.
# {servername} is the project id (see allow_named_servers above), so each
# project has its own claim `dsw-<username>-<project_id>`. KubeSpawner expands
# {servername} to the escaped server name; project ids are UUIDs and so
# DNS-safe. The default unnamed server, which the NHDW flow never spawns, would
# map to a claim name with a trailing dash.
c.KubeSpawner.storage_pvc_ensure = True
c.KubeSpawner.pvc_name_template = "dsw-{username}-{servername}"
c.KubeSpawner.storage_capacity = "5Gi"
c.KubeSpawner.volumes = [{
    "name": "work",
    "persistentVolumeClaim": {"claimName": "dsw-{username}-{servername}"},
}]
c.KubeSpawner.volume_mounts = [{"name": "work", "mountPath": "/home/jovyan/work"}]

# Profiles: from the catalogue, filtered by role
PROFILES_PATH = os.environ.get("WORKSPACE_PROFILES_PATH",
                               "/srv/jupyterhub/profiles.yaml")


def _load_catalogue() -> dict[str, Any]:
    try:
        with open(PROFILES_PATH) as f:
            return yaml.safe_load(f) or {}
    except OSError as exc:
        print(f"profiles catalogue unreadable ({exc}); empty profile list",
              file=sys.stderr)
        return {"default_profile": "Small", "profiles": []}


def _cpu(v: Any, fallback: float) -> float:
    s = str(v or "").strip()
    if not s:
        return fallback
    if s.endswith("m"):
        return float(s[:-1]) / 1000
    try:
        return float(s)
    except ValueError:
        return fallback


def _mem(v: Any, fallback: str) -> str:
    """Convert k8s memory notation (1Gi) to KubeSpawner's K/M/G/T form.

    KubeSpawner's mem_guarantee and mem_limit traits reject the binary 'i'
    suffix, so it is stripped here and profiles.yaml keeps k8s notation."""
    s = str(v or "").strip() or fallback
    if len(s) >= 2 and s[-1] == "i" and s[-2] in "KMGT":
        return s[:-1]
    return s


def _as_kubespawner_profile(p: dict[str, Any], default_slug: str) -> dict[str, Any]:
    res = p.get("resources") or {}
    return {
        "display_name": p.get("display_name", p["slug"]),
        "slug": p["slug"],
        "default": p["slug"] == default_slug,
        "description": p.get("description", ""),
        "kubespawner_override": {
            "cpu_guarantee":    _cpu(res.get("cpu_request"), 0.5),
            "cpu_limit":        _cpu(res.get("cpu_limit"), 1.0),
            "mem_guarantee":    _mem(res.get("memory_request"), "1G"),
            "mem_limit":        _mem(res.get("memory_limit"), "2G"),
            "storage_capacity": p.get("pvc_size", "5Gi"),
        },
    }


async def profile_list(spawner) -> list[dict[str, Any]]:
    """Return only the profiles the user's roles may self-serve.

    API-driven spawns through the gateway service carry no auth_state; the
    gateway has already enforced eligibility and OPA, so those see the full
    catalogue and user_options picks the slug."""
    auth_state = await spawner.user.get_auth_state() or {}
    roles = set(_roles_from_auth_state(auth_state))
    cat = _load_catalogue()
    default_slug = cat.get("default_profile", "Small")
    out = []
    for p in cat.get("profiles", []):
        allowed = set(p.get("self_serve_roles") or [])
        if not roles or (roles & allowed) or "R-SUPER" in roles:
            out.append(_as_kubespawner_profile(p, default_slug))
    if not out:
        out.append({"display_name": "Sandbox (no role profile matched)",
                    "slug": "sandbox", "default": True,
                    "kubespawner_override": {}})
    return out


c.KubeSpawner.profile_list = profile_list

# Pre-spawn: project reconciliation env + warehouse creds
MINIO_ENDPOINT   = os.environ.get(
    "MINIO_ENDPOINT", "http://seaweedfs-s3.nhdw.svc.cluster.local:8333")
ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET", "nhdw-artifacts")


async def pre_spawn_hook(spawner):
    env = spawner.environment
    # botocore#3755 workaround for the lab's boto3 sync: empty PUTs poison pooled
    # connections on Go-based S3 servers such as SeaweedFS.
    env["BOTO_EXPERIMENTAL__NO_EMPTY_CONTINUE"] = os.environ.get("BOTO_EXPERIMENTAL__NO_EMPTY_CONTINUE", "true")
    env["MLFLOW_TRACKING_URI"] = os.environ.get(
        "MLFLOW_TRACKING_URI", "http://mlflow.nhdw.svc.cluster.local:5000")
    # Static read-only warehouse access for notebooks.
    for k in ("TRINO_HOST", "TRINO_PORT", "PG_READ_HOST", "PG_READ_DB",
              "PG_READ_USER", "PG_READ_PASSWORD"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    # Project reconciliation (user_options come from the gateway spawn proxy).
    # The named server's name is the project id, so a spawn without
    # user_options (an implicit or hub-UI start) falls back to spawner.name and
    # is still scoped to the project.
    opts = spawner.user_options or {}
    project_id = opts.get("project_id") or (spawner.name or "")
    if project_id:
        project_slug = str(opts.get("project_slug") or project_id)
        env["NHDW_PROJECT_ID"]     = str(project_id)
        env["NHDW_PROJECT_SLUG"]   = project_slug
        # Per-project MLflow experiment: a bare mlflow.start_run() lands in
        # /projects/<slug> instead of Default. MLflow reads this env natively;
        # the nhdw_mlflow helper calls set_experiment on it, which creates the
        # experiment on first use with the proxied artifact root.
        env["MLFLOW_EXPERIMENT_NAME"] = f"/projects/{project_slug}"
        env["NHDW_PROJECT_PREFIX"] = f"projects/{project_id}/files/"
        env["NHDW_SYNC_BUCKET"]    = ARTIFACTS_BUCKET
        env["NHDW_SYNC_ENDPOINT"]  = MINIO_ENDPOINT
        for k_src, k_dst in (("MINIO_SYNC_ACCESS_KEY", "NHDW_SYNC_ACCESS_KEY"),
                             ("MINIO_SYNC_SECRET_KEY", "NHDW_SYNC_SECRET_KEY")):
            if os.environ.get(k_src):
                env[k_dst] = os.environ[k_src]
        spawner.log.info("project-scoped lab: %s (%s)",
                         opts.get("project_slug"), project_id)


c.KubeSpawner.pre_spawn_hook = pre_spawn_hook

# Hub state
c.JupyterHub.db_url = "sqlite:////srv/jupyterhub-state/jupyterhub.sqlite"
c.JupyterHub.cookie_secret_file = "/srv/jupyterhub-state/jupyterhub_cookie_secret"
c.JupyterHub.log_level = "INFO"
