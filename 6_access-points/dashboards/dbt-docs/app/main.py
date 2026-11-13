"""NHDW dbt-docs gateway.

A small FastAPI app in front of the dbt-generated static docs sites
(`2_pipelines/<project>/target/`). OIDC against the realms in OIDC_REALMS,
signed session cookies, and access limited to ALLOWED_ROLES (analysts,
researchers, stewards, operators and super-users by default).

URL layout:
    /                                  project index (lists generated docs)
    /<project>/                        that project's dbt-docs index.html
    /<project>/<rest…>                 static asset within target/
    /api/projects                      JSON list of available projects
    /login, /callback, /logout, /health, /api/me: session and health endpoints

The dbt-staging, dbt-vault and dbt-marts k8s Jobs each run `dbt docs generate`
(through their entrypoint.py wrapper) and write the output to
`/dbt-projects/<project>/target/` on the shared dbt-docs-content PVC.
`ALLOWED_PROJECTS` (env, default `marts`; `marts,vault,staging` in the
deployment) drives discovery: `/api/projects` lists every allowed project and
gives it a URL once its index.html exists, so a project appears in the picker
as soon as its Job has run.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from itsdangerous import BadSignature, TimestampSigner
from jose import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
log = logging.getLogger("dbt-docs")

# Static-site root for the gateway shell, not the dbt-docs sites themselves.
SHELL_DIR = Path("/app/html")

# Each project's target/ is mounted under /dbt-projects/<project>/target/
PROJECTS_ROOT = Path(os.environ.get("PROJECTS_ROOT", "/dbt-projects"))
ALLOWED_PROJECTS = set(os.environ.get("ALLOWED_PROJECTS", "marts").split(","))

KEYCLOAK_BASE        = os.environ.get("KEYCLOAK_BASE", "http://keycloak:8080")
KEYCLOAK_PUBLIC_BASE = os.environ.get("KEYCLOAK_PUBLIC_BASE", "http://127.0.0.1:8090")
# Lineage is reachable from a staff dashboard (moh-staff) or the admin dashboard
# (privileged-ops), so a session from any listed realm is accepted. Each login
# uses the realm named by the `?realm=` hint (the dashboard button passes its
# realm) so the existing Keycloak SSO session is reused. REALMS[0] is the default.
REALMS               = [r.strip() for r in os.environ.get(
                          "OIDC_REALMS", "moh-staff,privileged-ops").split(",") if r.strip()]
CLIENT_ID            = os.environ.get("OIDC_CLIENT_ID", "dbt-docs")
CLIENT_SECRET        = os.environ.get("OIDC_CLIENT_SECRET", "")
REDIRECT_URI         = os.environ.get("OIDC_REDIRECT_URI", "http://127.0.0.1:8097/callback")
APP_BASE             = os.environ.get("APP_BASE", "http://127.0.0.1:8097")

SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-only-change-me")
SESSION_COOKIE = "dbt_docs_session"
SESSION_MAX_AGE = 60 * 60 * 8

LOKI_URL = os.environ.get("LOKI_URL", "http://loki:3100")

# Roles allowed to view dbt docs. Lineage is non-sensitive metadata, so the
# default covers analysts, researchers, stewards and operators.
ALLOWED_ROLES = {r.strip() for r in os.environ.get(
    "ALLOWED_ROLES",
    "R-CONS-ANA,R-CONS-RES,R-STEW,R-OPS-ADM,R-OPS-ENG,R-OPS-SEC,R-SUPER").split(",") if r.strip()}


def _realm_for(name: str | None) -> str:
    """Validate a requested realm against the allow-list; default to REALMS[0]."""
    return name if name in REALMS else REALMS[0]

PUBLIC_PATHS = {"/health", "/login", "/callback", "/logout"}
signer = TimestampSigner(SESSION_SECRET)


def _set_session(response: Response, claims: dict[str, Any]) -> None:
    payload = signer.sign(json.dumps(claims).encode()).decode()
    response.set_cookie(SESSION_COOKIE, payload,
        max_age=SESSION_MAX_AGE, httponly=True, samesite="lax", secure=False)


def _read_session(request: Request) -> dict[str, Any] | None:
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    try:
        raw = signer.unsign(cookie, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None
    return json.loads(raw.decode())


_discovery_cache: dict[str, dict[str, Any]] = {}
_jwks_cache: dict[str, dict[str, Any]] = {}


async def _discovery(realm: str) -> dict[str, Any]:
    if realm not in _discovery_cache:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{KEYCLOAK_BASE}/realms/{realm}/.well-known/openid-configuration")
            r.raise_for_status()
            _discovery_cache[realm] = r.json()
    return _discovery_cache[realm]


async def _jwks(realm: str) -> dict[str, Any]:
    if realm not in _jwks_cache:
        d = await _discovery(realm)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(d["jwks_uri"])
            r.raise_for_status()
            _jwks_cache[realm] = r.json()
    return _jwks_cache[realm]


class OIDCAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/static/"):
            return await call_next(request)
        session = _read_session(request)
        if not session:
            # Preserve a ?realm= hint through the login redirect so the user's
            # existing Keycloak session (in whichever realm) is reused silently.
            hint = request.query_params.get("realm", "")
            q = f"?realm={urllib.parse.quote(hint)}" if hint else ""
            return RedirectResponse(f"{APP_BASE}/login{q}")
        if not (set(session.get("roles", [])) & ALLOWED_ROLES):
            return JSONResponse(
                {"error": "role required", "allowed": sorted(ALLOWED_ROLES)},
                status_code=403,
            )
        request.state.session = session
        return await call_next(request)


app = FastAPI(title="NHDW dbt docs", docs_url=None, redoc_url=None)
app.add_middleware(OIDCAuthMiddleware)
app.mount("/static", StaticFiles(directory=str(SHELL_DIR), html=False), name="static")


@app.get("/login")
async def login(request: Request) -> RedirectResponse:
    realm = _realm_for(request.query_params.get("realm"))
    d = await _discovery(realm)
    state = secrets.token_urlsafe(16)
    nonce = secrets.token_urlsafe(16)
    sig_state = signer.sign(json.dumps({"state": state, "nonce": nonce, "realm": realm}).encode()).decode()
    params = {
        "response_type": "code", "client_id": CLIENT_ID,
        # No explicit `roles` scope: the realm has no standard `roles` client
        # scope, and role claims arrive through the default `nhdw-flat-roles`
        # scope. Requesting an unassigned scope fails with invalid_scope.
        "redirect_uri": REDIRECT_URI, "scope": "openid profile email",
        "state": state, "nonce": nonce,
    }
    url = d["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)
    url = url.replace(KEYCLOAK_BASE, KEYCLOAK_PUBLIC_BASE, 1)
    resp = RedirectResponse(url)
    resp.set_cookie("oidc_state", sig_state, max_age=600, httponly=True, samesite="lax")
    return resp


@app.get("/callback")
async def callback(request: Request, code: str = "", state: str = "") -> Response:
    saved = request.cookies.get("oidc_state")
    if not saved or not code:
        raise HTTPException(400, "missing code/state")
    try:
        s = json.loads(signer.unsign(saved, max_age=600).decode())
    except BadSignature:
        raise HTTPException(400, "bad state")
    if s["state"] != state:
        raise HTTPException(400, "state mismatch")
    realm = _realm_for(s.get("realm"))

    d = await _discovery(realm)
    async with httpx.AsyncClient(timeout=10) as c:
        tok_resp = await c.post(
            d["token_endpoint"],
            data={
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if tok_resp.status_code != 200:
        raise HTTPException(401, f"token exchange failed: {tok_resp.text}")
    tokens = tok_resp.json()

    jwks = await _jwks(realm)
    try:
        id_claims = jwt.decode(tokens["id_token"], jwks, algorithms=["RS256"],
                               audience=CLIENT_ID, options={"verify_at_hash": False})
        access_claims = jwt.decode(tokens["access_token"], jwks, algorithms=["RS256"],
                                   options={"verify_aud": False, "verify_at_hash": False})
    except Exception as e:
        raise HTTPException(401, f"token verify failed: {e}")
    if id_claims.get("nonce") != s["nonce"]:
        raise HTTPException(401, "nonce mismatch")

    # Roles come from realm_access.roles or the flat top-level `roles` claim
    # (nhdw-flat-roles scope); both are merged so either realm's mapping works.
    roles = list((access_claims.get("realm_access") or {}).get("roles", []))
    roles += [r for r in (access_claims.get("roles") or []) if r not in roles]
    session = {
        "sub":      id_claims.get("sub"),
        "username": id_claims.get("preferred_username"),
        "name":     id_claims.get("name"),
        "email":    id_claims.get("email"),
        "roles":    roles,
        "realm":    realm,
        "issued":   int(time.time()),
    }
    log.info("login: %s roles=%s", session["username"], session["roles"])

    resp = RedirectResponse(f"{APP_BASE}/")
    _set_session(resp, session)
    resp.delete_cookie("oidc_state")
    await _audit(session, action="login", resource="dbt-docs")
    return resp


@app.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    sess = _read_session(request)
    d = await _discovery(_realm_for((sess or {}).get("realm")))
    end = d.get("end_session_endpoint", "").replace(KEYCLOAK_BASE, KEYCLOAK_PUBLIC_BASE, 1)
    target = end + "?" + urllib.parse.urlencode(
        {"post_logout_redirect_uri": APP_BASE, "client_id": CLIENT_ID}) if end else APP_BASE
    resp = RedirectResponse(target)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/me")
async def me(request: Request) -> JSONResponse:
    s = request.state.session
    return JSONResponse({
        "username": s["username"], "name": s.get("name"), "email": s.get("email"),
        "roles":    [r for r in s.get("roles", []) if r.startswith("R-")],
        "realm":    s["realm"],
    })


@app.get("/api/projects")
async def list_projects(request: Request) -> JSONResponse:
    """List dbt projects whose docs are currently present on disk."""
    available = []
    for proj in sorted(ALLOWED_PROJECTS):
        idx = PROJECTS_ROOT / proj / "target" / "index.html"
        if idx.is_file():
            stat = idx.stat()
            available.append({
                "project": proj,
                "url":     f"/{proj}/",
                "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
                "size":    stat.st_size,
            })
        else:
            available.append({"project": proj, "url": None, "built_at": None, "size": 0})
    return JSONResponse({"projects": available})


@app.get("/")
async def root(request: Request) -> Response:
    return FileResponse(SHELL_DIR / "index.html")


# Per-project static serving


@app.get("/{project}/")
async def project_index(project: str, request: Request) -> Response:
    if project not in ALLOWED_PROJECTS:
        raise HTTPException(404, f"project {project!r} not allowed")
    idx = PROJECTS_ROOT / project / "target" / "index.html"
    if not idx.is_file():
        raise HTTPException(404, f"docs not yet generated for {project!r}")
    await _audit(request.state.session, action="R", resource=f"dbt-docs/{project}")
    return FileResponse(idx)


@app.get("/{project}/{path:path}")
async def project_asset(project: str, path: str, request: Request) -> Response:
    if project not in ALLOWED_PROJECTS:
        raise HTTPException(404, f"project {project!r} not allowed")
    # Refuse paths that resolve outside the project's target/ directory.
    target = (PROJECTS_ROOT / project / "target" / path).resolve()
    base   = (PROJECTS_ROOT / project / "target").resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target)


async def _audit(session: dict[str, Any], *, action: str, resource: str, outcome: str = "0") -> None:
    role = next((r for r in session.get("roles", []) if r.startswith("R-")), "unknown")
    payload = {
        "streams": [{
            "stream": {"app": "dbt-docs", "realm": session.get("realm", REALMS[0] if REALMS else "unknown"),
                       "user": session.get("username", "unknown"), "role": role, "purpose": "public-health"},
            "values": [[
                str(int(time.time() * 1_000_000_000)),
                json.dumps({
                    "resourceType": "AuditEvent", "action": action, "outcome": outcome,
                    "recorded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "agent": [{"who": {"display": session.get("username")}}],
                    "source": {"observer": {"display": "dbt-docs"}},
                    "entity": [{"what": {"display": resource}}],
                }),
            ]],
        }]
    }
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            await c.post(f"{LOKI_URL}/loki/api/v1/push", json=payload)
    except Exception as e:
        log.warning("audit push failed: %s", e)
