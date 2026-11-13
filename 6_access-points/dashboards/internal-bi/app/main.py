"""NHDW Internal BI: analytics dashboard backend.

The frontend is a React SPA (built under /app/dist). The backend is a
thin shell around two responsibilities:

  1. Identity surface for the SPA
       GET /auth/me      current UserCtx, or {authenticated:false}
       GET /auth/config  Keycloak settings (for the PKCE flow)

  2. Gateway proxy
       /gw/{path}        relays calls to nhdw-gateway with the
                            caller's identity.

Two auth modes:

    DEV_AUTH=true   the backend mocks identity from the DEV_MOCK_USER and
                    DEV_MOCK_ROLE env vars. Local development only.
    DEV_AUTH=false  Bearer JWT validated against the realm's JWKS.
                    PKCE lives in the SPA's useAuth hook.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from starlette.middleware.gzip import GZipMiddleware
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from jose import jwt
from starlette.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
log = logging.getLogger("internal-bi")

DIST_DIR = Path("/app/dist")
STATIC_DIR = Path("/app/static")

# settings
KEYCLOAK_BASE = os.environ.get("KEYCLOAK_BASE", "http://keycloak:8080")
KEYCLOAK_PUBLIC_BASE = os.environ.get("KEYCLOAK_PUBLIC_BASE",
                                       "http://127.0.0.1:8090")
REALM = os.environ.get("OIDC_REALM", "moh-staff")
CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "internal-bi")
REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "http://127.0.0.1:8087/")
APP_BASE = os.environ.get("APP_BASE", "http://127.0.0.1:8087")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://nhdw-gateway:8000")

DEV_AUTH = os.environ.get("DEV_AUTH", "false").lower() == "true"
DEV_MOCK_USER = os.environ.get(
    "DEV_MOCK_USER", "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11")
DEV_MOCK_ROLE = os.environ.get("DEV_MOCK_ROLE", "R-DEC")


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
    """Resolve the calling user. DEV_AUTH returns the env-mocked
    identity; PROD requires a valid Bearer JWT."""
    if DEV_AUTH:
        return UserCtx(
            sub=DEV_MOCK_USER, username=DEV_MOCK_USER, name="Dev User",
            email="", roles=[DEV_MOCK_ROLE], realm=REALM, auth_mode="dev",
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
                       or claims.get("roles") or []),  # nhdw-flat-roles fallback: real tokens carry no realm_access (see gateway auth.py)
                realm=REALM, access_token=token, auth_mode="bearer",
            )
    raise HTTPException(status_code=401, detail="unauthenticated")


async def get_user_or_none(request: Request) -> UserCtx | None:
    try:
        return await get_user(request)
    except HTTPException:
        return None


# app
app = FastAPI(title="NHDW Internal BI", docs_url=None, redoc_url=None)
# Plan payloads (the Control Tower's is ~4 MB of JSON) and bundles compress ~10x;
# the /gw proxy returns decoded gateway bodies, so compression happens here, at the
# browser-facing edge.
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "internal-bi",
            "auth": "dev" if DEV_AUTH else "bearer"}


@app.get("/")
async def root() -> Response:
    # no-cache: index.html references hash-named bundles that change every build;
    # a cached copy points at 404'd bundles after a redeploy, so the SPA stalls.
    return FileResponse(DIST_DIR / "index.html",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


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
# "the masthead, not a secret… the map, which the public dashboard needs before
# anyone signs in"). Several of those reads cannot carry a Bearer at all: a
# ministry-crest <img src>, a stylesheet url(): and the map geometry is fetched
# during SPA boot before the auth session is established. So this proxy must not
# force a token on them, or it would 401 reads the gateway is glad to serve
# unauthenticated. A signed-in caller still relays their bearer (identity costs
# nothing and keeps any future row-filtering intact); an un-authable one gets the
# public response. Everything else still requires a valid session.
def _is_public_gw(method: str, path: str) -> bool:
    return method == "GET" and (path == "profile" or path.startswith("profile/"))


@app.api_route("/gw/{path:path}", methods=list(_PROXY_METHODS))
async def gw_proxy(path: str, request: Request,
                   user: UserCtx | None = Depends(get_user_or_none)) -> Response:
    """Forward to nhdw-gateway with the caller's identity. Bearer relays
    the user's access token; DEV mode sends mock identity headers the
    gateway resolves via its own DEV_AUTH path. Public profile reads
    (masthead, map geometry, branding) relay without requiring a token;
    see _is_public_gw."""
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
        # public profile read, no identity: the gateway serves it unauthenticated
        headers.pop("Authorization", None)
    body = await request.body()
    # The AI-assistant endpoints call a local LLM (one round can take tens of
    # seconds: much more on CPU, and multi-round agentic turns stack up), so the
    # 20s data-fetch timeout would spuriously return gateway_unreachable. Give the
    # chat + SQL paths a generous timeout; everything else stays snappy.
    _t = 240 if path.startswith(("chat/", "sql/")) else 20
    try:
        async with httpx.AsyncClient(timeout=_t) as client:
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


app.mount("/static",
          StaticFiles(directory=str(STATIC_DIR), html=False), name="static")
app.mount("/assets",
          StaticFiles(directory=str(DIST_DIR / "assets"), html=False),
          name="vite-assets")
