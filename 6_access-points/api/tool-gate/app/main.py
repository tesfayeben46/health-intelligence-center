"""NHDW tool-gate: a generic role-aware OIDC reverse proxy.

One image, deployed once per external or operator tool (MLflow, Prometheus,
Airflow, and others). Behaviour:

  1. Deep-link auto-login: the dashboard button passes ?realm=<the dashboard's
     realm>, so the shared Keycloak SSO session is reused and a user arriving
     from a logged-in dashboard is admitted without a second login.
  2. Role gate: every proxied request requires a session role in
     ALLOWED_ROLES, otherwise 403. Button visibility is enforced separately
     as a widget grant.
  3. Direct access: opening the tool URL without a valid session shows the
     gate's Keycloak login; the same NHDW credentials work.
  4. The role check runs on every request, so an authenticated but
     unauthorised user is denied, not just hidden.

The OIDC logic (discovery, JWKS, confidential code exchange without PKCE,
signed session cookie, multiple realms) is the same as the dbt-docs gateway;
instead of serving static files it reverse-proxies an upstream service,
optionally injecting an auth header the upstream needs (for example a
service-account bearer for Headlamp).

Config (env):
  UPSTREAM_URL          http://mlflow.nhdw.svc.cluster.local:5000  (required)
  TOOL_NAME             mlflow                                     (audit + shell)
  ALLOWED_ROLES         R-CONS-SCI,R-SUPER                         (comma list)
  OIDC_REALMS           moh-staff,privileged-ops   (accept a session from any)
  OIDC_CLIENT_ID        mlflow
  OIDC_CLIENT_SECRET    <confidential secret matching the KC client>
  OIDC_REDIRECT_URI     https://mlflow.127.0.0.1.nip.io/oauth2gate/callback
  APP_BASE              https://mlflow.127.0.0.1.nip.io
  KEYCLOAK_BASE / KEYCLOAK_PUBLIC_BASE
  SESSION_SECRET
  INJECT_BEARER_FILE    (optional) path to a file whose contents become the
                        Authorization: Bearer header sent to the upstream
  INJECT_HEADER_NAME / INJECT_HEADER_VALUE  (optional) a fixed header to inject
  LOKI_URL              (optional) audit sink
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import websockets as _ws
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from itsdangerous import BadSignature, TimestampSigner
from jose import jwt
from starlette.background import BackgroundTask
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")

TOOL_NAME = os.environ.get("TOOL_NAME", "tool")
log = logging.getLogger(f"tool-gate:{TOOL_NAME}")

UPSTREAM_URL = os.environ.get("UPSTREAM_URL", "").rstrip("/")
if not UPSTREAM_URL:
    log.warning("UPSTREAM_URL is empty; the gate will 502 every proxied request")

KEYCLOAK_BASE        = os.environ.get("KEYCLOAK_BASE", "http://keycloak:8080")
KEYCLOAK_PUBLIC_BASE = os.environ.get("KEYCLOAK_PUBLIC_BASE", "http://127.0.0.1:8090")
# A tool may be reached from a staff dashboard (moh-staff) or the admin
# dashboard (privileged-ops). A session from any listed realm is accepted, and
# login uses the realm named by the ?realm= hint so the existing SSO session is
# reused. REALMS[0] is the default.
REALMS               = [r.strip() for r in os.environ.get(
                          "OIDC_REALMS", "moh-staff,privileged-ops").split(",") if r.strip()]
CLIENT_ID            = os.environ.get("OIDC_CLIENT_ID", TOOL_NAME)
CLIENT_SECRET        = os.environ.get("OIDC_CLIENT_SECRET", "")
APP_BASE             = os.environ.get("APP_BASE", "http://127.0.0.1:8099").rstrip("/")
REDIRECT_URI         = os.environ.get("OIDC_REDIRECT_URI", f"{APP_BASE}/oauth2gate/callback")

SESSION_SECRET  = os.environ.get("SESSION_SECRET", "dev-only-change-me")
SESSION_COOKIE  = f"nhdw_{TOOL_NAME}_gate"
SESSION_MAX_AGE = 60 * 60 * 8

ALLOWED_ROLES = {r.strip() for r in os.environ.get("ALLOWED_ROLES", "R-SUPER").split(",") if r.strip()}

LOKI_URL = os.environ.get("LOKI_URL", "http://loki:3100")

# Optional upstream auth injection (e.g. Headlamp's SA bearer).
INJECT_BEARER_FILE = os.environ.get("INJECT_BEARER_FILE", "")
INJECT_HEADER_NAME = os.environ.get("INJECT_HEADER_NAME", "")
INJECT_HEADER_VALUE = os.environ.get("INJECT_HEADER_VALUE", "")

# The gate's own routes live under /oauth2gate/* so they cannot collide with
# upstream paths ('/', '/api', '/login' and so on).
GATE_PREFIX = "/oauth2gate"
PUBLIC_PATHS = {f"{GATE_PREFIX}/login", f"{GATE_PREFIX}/callback",
                f"{GATE_PREFIX}/logout", f"{GATE_PREFIX}/health"}

signer = TimestampSigner(SESSION_SECRET)

# Hop-by-hop headers must not be forwarded (RFC 7230 §6.1).
_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length"}


def _realm_for(name: str | None) -> str:
    return name if name in REALMS else (REALMS[0] if REALMS else "moh-staff")


def _set_session(response: Response, claims: dict[str, Any]) -> None:
    payload = signer.sign(json.dumps(claims).encode()).decode()
    response.set_cookie(SESSION_COOKIE, payload,
        max_age=SESSION_MAX_AGE, httponly=True, samesite="lax", secure=False)


def _session_from_cookie(cookie: str | None) -> dict[str, Any] | None:
    if not cookie:
        return None
    try:
        raw = signer.unsign(cookie, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None
    return json.loads(raw.decode())


def _read_session(request: Request) -> dict[str, Any] | None:
    return _session_from_cookie(request.cookies.get(SESSION_COOKIE))


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


def _forbidden_page(session: dict[str, Any]) -> HTMLResponse:
    roles = ", ".join(r for r in session.get("roles", []) if r.startswith("R-")) or "(none)"
    html = f"""<!doctype html><meta charset="utf-8"><title>Access denied: {TOOL_NAME}</title>
<style>body{{font-family:'Inter',system-ui,sans-serif;background:#f5efe6;color:#3a2c1e;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
.card{{max-width:520px;background:#fff;border:1px solid #e0d3c0;border-radius:14px;padding:40px;
box-shadow:0 6px 30px rgba(0,0,0,.08)}}h1{{margin:0 0 10px;font-size:21px;color:#6f4a2e}}
p{{line-height:1.6;font-size:15px}}code{{background:#efe7da;padding:1px 6px;border-radius:5px}}
a{{color:#9a6a3a}}</style>
<div class="card"><h1>You don't have access to {TOOL_NAME}</h1>
<p>You're signed in as <code>{session.get('username','?')}</code> with role
<code>{roles}</code>, which isn't permitted to use this tool.</p>
<p>Allowed roles: <code>{', '.join(sorted(ALLOWED_ROLES))}</code>.
Ask an administrator if you need access, or <a href="{GATE_PREFIX}/logout">sign out</a>.</p></div>"""
    return HTMLResponse(html, status_code=403)


def _inject_upstream_auth(headers: dict[str, str]) -> None:
    """Add the upstream's own auth header if configured (e.g. Headlamp SA bearer)."""
    if INJECT_BEARER_FILE:
        try:
            tok = Path(INJECT_BEARER_FILE).read_text().strip()
            if tok:
                headers["authorization"] = f"Bearer {tok}"
        except Exception as e:  # missing token file: proxy without it
            log.warning("inject bearer read failed: %s", e)
    if INJECT_HEADER_NAME and INJECT_HEADER_VALUE:
        headers[INJECT_HEADER_NAME.lower()] = INJECT_HEADER_VALUE


app = FastAPI(title=f"NHDW tool-gate, {TOOL_NAME}", docs_url=None, redoc_url=None)
_client = httpx.AsyncClient(timeout=None, follow_redirects=False)


# gate routes (under /oauth2gate/*)


@app.get(f"{GATE_PREFIX}/health")
async def health() -> dict:
    return {"status": "ok", "tool": TOOL_NAME}


@app.get(f"{GATE_PREFIX}/login")
async def login(request: Request) -> RedirectResponse:
    realm = _realm_for(request.query_params.get("realm"))
    d = await _discovery(realm)
    state = secrets.token_urlsafe(16)
    nonce = secrets.token_urlsafe(16)
    # where to return the user after login
    rd = request.query_params.get("rd", "/")
    sig_state = signer.sign(json.dumps(
        {"state": state, "nonce": nonce, "realm": realm, "rd": rd}).encode()).decode()
    params = {
        "response_type": "code", "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI, "scope": "openid profile email",
        "state": state, "nonce": nonce,
    }
    url = d["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)
    url = url.replace(KEYCLOAK_BASE, KEYCLOAK_PUBLIC_BASE, 1)
    resp = RedirectResponse(url)
    resp.set_cookie("oidc_state", sig_state, max_age=600, httponly=True, samesite="lax")
    return resp


@app.get(f"{GATE_PREFIX}/callback")
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
            data={"grant_type": "authorization_code", "code": code,
                  "redirect_uri": REDIRECT_URI,
                  "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
            headers={"Content-Type": "application/x-www-form-urlencoded"})
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
    roles = list((access_claims.get("realm_access") or {}).get("roles", []))
    roles += [r for r in (access_claims.get("roles") or []) if r not in roles]
    session = {
        "sub": id_claims.get("sub"), "username": id_claims.get("preferred_username"),
        "name": id_claims.get("name"), "email": id_claims.get("email"),
        "roles": roles, "realm": realm, "issued": int(time.time()),
    }
    log.info("login: %s roles=%s allowed=%s", session["username"],
             [r for r in roles if r.startswith("R-")],
             bool(set(roles) & ALLOWED_ROLES))
    rd = s.get("rd", "/")
    if not rd.startswith("/"):
        rd = "/"
    resp = RedirectResponse(f"{APP_BASE}{rd}")
    _set_session(resp, session)
    resp.delete_cookie("oidc_state")
    await _audit(session, action="login", outcome="0" if set(roles) & ALLOWED_ROLES else "12")
    return resp


@app.get(f"{GATE_PREFIX}/logout")
async def logout(request: Request) -> RedirectResponse:
    sess = _read_session(request)
    d = await _discovery(_realm_for((sess or {}).get("realm")))
    end = d.get("end_session_endpoint", "").replace(KEYCLOAK_BASE, KEYCLOAK_PUBLIC_BASE, 1)
    target = (end + "?" + urllib.parse.urlencode(
        {"post_logout_redirect_uri": APP_BASE, "client_id": CLIENT_ID})) if end else APP_BASE
    resp = RedirectResponse(target)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get(f"{GATE_PREFIX}/me")
async def me(request: Request) -> JSONResponse:
    s = _read_session(request)
    if not s:
        raise HTTPException(401, "no session")
    return JSONResponse({"username": s["username"], "realm": s["realm"],
                         "roles": [r for r in s.get("roles", []) if r.startswith("R-")],
                         "allowed": bool(set(s.get("roles", [])) & ALLOWED_ROLES)})


# catch-all reverse proxy (everything else -> upstream)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def proxy(path: str, request: Request) -> Response:
    # Same policy as dbt-docs, applied to every upstream request.
    session = _read_session(request)
    if not session:
        # Keep the ?realm= hint (silent SSO admit) and the requested path, so a
        # direct hit returns to where it started after login.
        hint = request.query_params.get("realm", "")
        rd = request.url.path + (("?" + request.url.query) if request.url.query else "")
        q = urllib.parse.urlencode({k: v for k, v in
                                    (("realm", hint), ("rd", rd)) if v})
        return RedirectResponse(f"{GATE_PREFIX}/login?{q}")
    if not (set(session.get("roles", [])) & ALLOWED_ROLES):
        return _forbidden_page(session)

    # Authorised: reverse-proxy to the upstream.
    if not UPSTREAM_URL:
        raise HTTPException(502, "no upstream configured")
    url = f"{UPSTREAM_URL}/{path}"
    if request.url.query:
        url += "?" + request.url.query
    fwd = {k.lower(): v for k, v in request.headers.items() if k.lower() not in _HOP}
    _inject_upstream_auth(fwd)
    body = await request.body()
    try:
        up = await _client.request(request.method, url, headers=fwd, content=body)
    except httpx.HTTPError as e:
        log.warning("upstream error %s: %s", url, e)
        raise HTTPException(502, f"upstream unreachable: {e}")
    resp_headers = [(k, v) for k, v in up.headers.items()
                    if k.lower() not in _HOP and k.lower() != "content-encoding"]
    return Response(content=up.content, status_code=up.status_code, headers=dict(resp_headers))


# websocket reverse proxy (live views, e.g. Headlamp's watch multiplexer)
# Same auth policy as the HTTP proxy (session and role), then bridges frames
# both ways to the upstream ws endpoint, injecting the upstream auth header
# (the service-account bearer) on the handshake.


@app.websocket("/{path:path}")
async def ws_proxy(client: WebSocket, path: str) -> None:
    session = _session_from_cookie(client.cookies.get(SESSION_COOKIE))
    if not session or not (set(session.get("roles", [])) & ALLOWED_ROLES):
        await client.close(code=1008)  # policy violation: unauthenticated or forbidden
        return
    if not UPSTREAM_URL:
        await client.close(code=1011)
        return
    scheme = "wss" if UPSTREAM_URL.startswith("https") else "ws"
    up_url = f"{scheme}://{UPSTREAM_URL.split('://', 1)[1]}/{path}"
    if client.url.query:
        up_url += "?" + client.url.query
    hdrs: dict[str, str] = {}
    _inject_upstream_auth(hdrs)
    subprotos = [p.strip() for p in
                 client.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    try:
        upstream = await _ws.connect(up_url, extra_headers=hdrs,
                                     subprotocols=subprotos or None,
                                     open_timeout=10, max_size=None, ping_interval=None)
    except Exception as e:  # noqa: BLE001 - any upstream failure closes the client ws
        log.warning("ws upstream connect failed %s: %s", up_url, e)
        await client.close(code=1011)
        return
    await client.accept(subprotocol=upstream.subprotocol)

    async def client_to_upstream() -> None:
        try:
            while True:
                msg = await client.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("text") is not None:
                    await upstream.send(msg["text"])
                elif msg.get("bytes") is not None:
                    await upstream.send(msg["bytes"])
        except (WebSocketDisconnect, ConnectionClosed):
            pass
        finally:
            await upstream.close()

    async def upstream_to_client() -> None:
        try:
            async for message in upstream:
                if isinstance(message, (bytes, bytearray)):
                    await client.send_bytes(bytes(message))
                else:
                    await client.send_text(message)
        except ConnectionClosed:
            pass
        finally:
            try:
                await client.close()
            except Exception:  # noqa: BLE001 - already closed
                pass

    await asyncio.gather(client_to_upstream(), upstream_to_client())


async def _audit(session: dict[str, Any], *, action: str, resource: str | None = None,
                 outcome: str = "0") -> None:
    role = next((r for r in session.get("roles", []) if r.startswith("R-")), "unknown")
    payload = {"streams": [{
        "stream": {"app": f"tool-gate-{TOOL_NAME}", "realm": session.get("realm", "unknown"),
                   "user": session.get("username", "unknown"), "role": role, "purpose": "public-health"},
        "values": [[str(int(time.time() * 1_000_000_000)),
                    json.dumps({"resourceType": "AuditEvent", "action": action, "outcome": outcome,
                                "recorded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "agent": [{"who": {"display": session.get("username")}}],
                                "source": {"observer": {"display": f"tool-gate-{TOOL_NAME}"}},
                                "entity": [{"what": {"display": resource or TOOL_NAME}}]})]]}]}
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            await c.post(f"{LOKI_URL}/loki/api/v1/push", json=payload)
    except Exception as e:
        log.warning("audit push failed: %s", e)
