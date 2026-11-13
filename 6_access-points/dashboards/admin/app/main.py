"""NHDW Admin: backend for the privileged-ops dashboard.

Serves the built SPA and proxies /gw/* to the gateway with the caller's
identity. Two auth modes:

    DEV_AUTH=true   Identity is mocked from DEV_MOCK_USER and DEV_MOCK_ROLE
                    and relayed as X-Mock-* headers. Local development only.
    DEV_AUTH=false  Bearer JWT validated against the privileged-ops realm's
                    JWKS. The PKCE flow lives in the SPA's useAuth hook.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from jose import jwt
from starlette.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
log = logging.getLogger("admin")

DIST_DIR = Path("/app/dist")
STATIC_DIR = Path("/app/static")

# settings
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://nhdw-gateway:8000")
DEV_AUTH = os.environ.get("DEV_AUTH", "true").lower() == "true"
DEV_MOCK_USER = os.environ.get(
    "DEV_MOCK_USER", "11111111-2222-3333-4444-555555555555")
DEV_MOCK_ROLE = os.environ.get("DEV_MOCK_ROLE", "R-OPS-ADM")

KEYCLOAK_BASE = os.environ.get("KEYCLOAK_BASE", "http://keycloak:8080")
KEYCLOAK_PUBLIC_BASE = os.environ.get("KEYCLOAK_PUBLIC_BASE",
                                       "http://127.0.0.1:8090")
REALM = os.environ.get("OIDC_REALM", "privileged-ops")
CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "admin")
REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI",
                               "http://127.0.0.1:8086/")
APP_BASE = os.environ.get("APP_BASE", "http://127.0.0.1:8086")


# auth
@dataclass
class UserCtx:
    sub: str
    username: str
    name: str
    email: str
    roles: list[str] = field(default_factory=list)
    realm: str = ""
    access_token: str | None = None
    auth_mode: str = "unknown"

    @property
    def primary_role(self) -> str:
        for r in self.roles:
            if isinstance(r, str) and r.startswith("R-"):
                return r
        return DEV_MOCK_ROLE if DEV_AUTH else "R-UNKNOWN"


_jwks_cache: dict[str, Any] | None = None


async def _jwks() -> dict[str, Any]:
    global _jwks_cache
    if _jwks_cache is None:
        async with httpx.AsyncClient(timeout=10) as c:
            d = (await c.get(
                f"{KEYCLOAK_BASE}/realms/{REALM}"
                "/.well-known/openid-configuration")).json()
            _jwks_cache = (await c.get(d["jwks_uri"])).json()
    return _jwks_cache


async def _verify_bearer(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, await _jwks(), algorithms=["RS256"],
                          options={"verify_aud": False, "verify_at_hash": False})
    except Exception as e:                                    # noqa: BLE001
        log.warning("bearer token verify failed: %s", e)
        return None


async def get_user(request: Request) -> UserCtx:
    """Resolve the calling user. DEV_AUTH returns the env-mocked identity;
    PROD requires a valid Bearer JWT (401 if absent or invalid)."""
    if DEV_AUTH:
        return UserCtx(
            sub=DEV_MOCK_USER, username="dev-ops-admin",
            name="Dev Ops Admin", email="ops-admin@nhdw.local",
            roles=[DEV_MOCK_ROLE], realm=REALM, auth_mode="dev",
        )
    auth_hdr = request.headers.get("authorization", "")
    if auth_hdr.lower().startswith("bearer "):
        token = auth_hdr.split(None, 1)[1].strip()
        claims = await _verify_bearer(token)
        if claims:
            return UserCtx(
                sub=claims.get("sub", ""),
                username=claims.get("preferred_username", ""),
                name=claims.get("name", ""),
                email=claims.get("email", ""),
                roles=((claims.get("realm_access") or {}).get("roles")
                       or claims.get("roles") or []),  # nhdw-flat-roles fallback: platform tokens carry a flat `roles` claim, not realm_access (see gateway auth.py)
                realm=REALM, access_token=token, auth_mode="bearer",
            )
    raise HTTPException(status_code=401, detail="unauthenticated")


async def get_user_or_none(request: Request) -> UserCtx | None:
    try:
        return await get_user(request)
    except HTTPException:
        return None


# app
app = FastAPI(title="NHDW Admin", version="0.2.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "nhdw-admin",
            "auth": "dev" if DEV_AUTH else "bearer"}


@app.get("/auth/me")
async def auth_me(user: UserCtx | None = Depends(get_user_or_none)) -> dict:
    if user is None:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "kc_sub":        user.sub,
        "username":      user.username or user.sub[:8],
        "email":         user.email,
        "full_name":     user.name,
        "realm":         user.realm,
        "primary_role":  user.primary_role,
        "roles":         user.roles,
        "auth_mode":     user.auth_mode,
    }


@app.get("/auth/config")
async def auth_config() -> dict:
    """Public OIDC config for the SPA's PKCE flow. Safe to expose: no
    secrets, just URLs + realm + client id. SPA reads on boot to know
    where to redirect for login."""
    return {
        "dev_auth":             DEV_AUTH,
        "keycloak_public_base": KEYCLOAK_PUBLIC_BASE,
        "realm":                REALM,
        "client_id":            CLIENT_ID,
        "redirect_uri":         REDIRECT_URI,
        "post_logout_uri":      APP_BASE,
    }


_PROXY_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH")


# The country profile is PUBLIC in the gateway by design (gateway routes/profile.py:
# the masthead, map geometry and branding, served before anyone signs in). Some of
# those reads cannot carry a Bearer (a ministry-crest <img src>, a stylesheet
# url()) and the map geometry is fetched during SPA boot before the auth session
# exists, so this proxy must not force a token on them. A signed-in caller still
# relays their bearer; an un-authable one gets the public response. Everything else
# still requires a valid session.
def _is_public_gw(method: str, path: str) -> bool:
    return method == "GET" and (path == "profile" or path.startswith("profile/"))


@app.api_route("/gw/{path:path}", methods=list(_PROXY_METHODS))
async def proxy(path: str, request: Request,
                user: UserCtx | None = Depends(get_user_or_none)) -> Response:
    """Forward to nhdw-gateway with the caller's identity. Bearer relays
    the actual access token; DEV mode uses X-Mock-User headers. Public
    profile reads (masthead + map + branding) relay without a token."""
    if user is None and not _is_public_gw(request.method, path):
        raise HTTPException(status_code=401, detail="unauthenticated")
    target = f"{GATEWAY_URL.rstrip('/')}/api/{path}"
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in {"host", "content-length", "connection"}}
    if user and user.access_token:
        headers["Authorization"] = f"Bearer {user.access_token}"
    elif user:
        headers.pop("Authorization", None)
        headers["X-Mock-User"] = user.sub
        headers["X-Mock-Role"] = user.primary_role
    else:
        headers.pop("Authorization", None)
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.request(
                request.method, target,
                params=dict(request.query_params),
                headers=headers, content=body,
            )
    except httpx.RequestError as e:
        log.warning("gateway proxy error %s %s: %s", request.method, target, e)
        return JSONResponse({"error": "gateway_unreachable", "detail": str(e)},
                            status_code=502)
    rh = {k: v for k, v in r.headers.items()
          if k.lower() not in {"content-encoding", "transfer-encoding",
                               "connection"}}
    return Response(content=r.content, status_code=r.status_code,
                    headers=rh, media_type=r.headers.get("content-type"))


@app.get("/")
async def index() -> FileResponse:
    # index.html must not be browser-cached: it references hash-named bundles
    # that change on every build, and a stale copy points at bundles that 404
    # after a redeploy, so the SPA stalls on load. The hashed /assets are
    # immutable, so only index.html needs no-cache.
    return FileResponse(DIST_DIR / "index.html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


app.mount("/assets",
          StaticFiles(directory=str(DIST_DIR / "assets")), name="vite-assets")
app.mount("/static",
          StaticFiles(directory=str(STATIC_DIR)), name="static")
