"""Fayda Replica: a stand-in for Ethiopia's Fayda national-ID OIDC identity provider.

Runs outside k8s (0_external/, like the source replicas) because it models a
third-party national IdP the platform integrates with. Its citizens are
synthetic (seed/citizens.yaml).

The primary consumer is the public dashboard's identity-verification step on
the access-request form. A non-registered visitor proves they hold a Fayda
identity through an e-mail OTP round-trip that Fayda runs itself, as a real
external IdP sends its own codes:
    POST /verify/challenge {fan,email}: check the FAN exists and the supplied
                                         email matches the address on that
                                         record (both factors), then email a
                                         6-digit OTP to it through Fayda's own
                                         SMTP; return the masked email
    POST /verify/confirm  {fan, code}: validate the code, return the identity
The OTP mailer is Fayda's own (0_external/fayda/.env -> docker-compose),
separate from the NHDW gateway's mailer and smtp-secrets.

The OAuth 2.0 / OpenID Connect endpoints are implemented as a real IdP
exposes them, but the dashboard flow does not use them:
    GET  /.well-known/openid-configuration
    GET  /jwks
    GET  /authorize    login form + redirect with `code`
    POST /authorize    handle form submit
    POST /token        exchange code for tokens
    GET  /userinfo     return claims for a bearer token

Development and demonstration only; never deploy it to production. To use
real Fayda, point the public dashboard's FAYDA_* env vars at the production
Fayda endpoints.
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import os
import random
import secrets
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jose import jwt
from pydantic import BaseModel
from starlette.background import BackgroundTask

log = logging.getLogger("fayda-replica")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

ISSUER = os.environ.get("FAYDA_ISSUER", "http://localhost:8096")
CLIENT_ID = os.environ.get("FAYDA_CLIENT_ID", "nhdw-citizen-portal")
CLIENT_SECRET = os.environ.get("FAYDA_CLIENT_SECRET", "fake-fayda-secret")

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"

# Key material
# Generate a fresh RSA key per startup. Real Fayda holds its own long-lived
# signing key.
_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_private_pem = _private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
_public_numbers = _private_key.public_key().public_numbers()
KID = "fayda-replica-2026"


def _int_to_b64url(i: int) -> str:
    import base64
    n = (i.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(i.to_bytes(n, "big")).rstrip(b"=").decode()


JWKS = {
    "keys": [
        {
            "kty": "RSA",
            "alg": "RS256",
            "use": "sig",
            "kid": KID,
            "n": _int_to_b64url(_public_numbers.n),
            "e": _int_to_b64url(_public_numbers.e),
        }
    ]
}


# Citizen seed
def _load_citizens() -> dict[str, dict[str, Any]]:
    path = SEED_DIR / "citizens.yaml"
    if not path.exists():
        log.warning("citizens.yaml not found at %s: empty seed", path)
        return {}
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    return {c["fayda_id"]: c for c in data.get("citizens", [])}


CITIZENS = _load_citizens()

# In-memory stores (dev only).
_AUTH_CODES: dict[str, dict[str, Any]] = {}
_ACCESS_TOKENS: dict[str, dict[str, Any]] = {}


# Email-OTP verification (Fayda owns this end-to-end)
# The public access-request form asks a visitor to prove they hold a Fayda
# identity before submitting. Fayda (a third-party IdP in production) runs the
# whole round-trip itself, as a real IdP sends its own codes: it checks the FAN
# against its registry, generates a numeric OTP, emails it to the identity's
# registered address through its own SMTP, and confirms the code. Nothing here
# touches NHDW's smtp-secrets; Fayda's SMTP settings live in its own config
# (0_external/fayda/.env -> docker-compose -> these env vars).
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or "587")
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "Fayda Verification <noreply@fayda.local>")
SMTP_TLS = os.environ.get("SMTP_TLS", "true").lower() in ("1", "true", "yes", "on")

# OTP store: FAN -> challenge state. In-memory and ephemeral by design: an OTP
# must not survive a restart.
_OTP: dict[str, dict[str, Any]] = {}
_OTP_TTL_S = 600            # a code is valid for 10 minutes
_OTP_RESEND_COOLDOWN_S = 30  # min gap between two challenges for one FAN
_OTP_MAX_ATTEMPTS = 5        # wrong-code tries before the challenge is burned


class ChallengeReq(BaseModel):
    fan: str
    email: str = ""   # the visitor must supply the email registered to this FAN


class ConfirmReq(BaseModel):
    fan: str
    code: str


def _mask_email(addr: str) -> str:
    """Hide the local part: `aster.bekele@citizens.example.org`
    -> `a•••@citizens.example.org`. Never echo the full registered address back."""
    try:
        local, domain = addr.split("@", 1)
    except ValueError:
        return "•••"
    head = local[0] if local else "•"
    return f"{head}•••@{domain}"


def _send_email_blocking(to_addr: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
        if SMTP_TLS:
            s.starttls()
        if SMTP_USER:
            s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)


async def _send_email(to_addr: str, subject: str, body: str) -> bool:
    """Send one text email through Fayda's own SMTP. Returns True on success, False
    on any failure (logged, never raised) or when SMTP/recipient is unconfigured.
    Runs the blocking smtplib call in a thread so the event loop never stalls."""
    if not SMTP_HOST or not to_addr:
        log.warning("[fayda] SMTP not configured (SMTP_HOST empty): OTP email skipped")
        return False
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _send_email_blocking, to_addr, subject, body)
        return True
    except Exception as e:  # noqa: BLE001 (best-effort; the code is still logged)
        log.warning("[fayda] OTP email to %s failed: %s", _mask_email(to_addr), e)
        return False


app = FastAPI(title="Fayda Replica OIDC", description="Demo replica of Ethiopia's Fayda identity provider (external to NHDW).")


# Discovery + JWKS
@app.get("/.well-known/openid-configuration")
async def discovery() -> JSONResponse:
    return JSONResponse({
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "userinfo_endpoint": f"{ISSUER}/userinfo",
        "jwks_uri": f"{ISSUER}/jwks",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "profile"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
        "claims_supported": [
            "sub", "iss", "aud", "exp", "iat",
            "fayda_id", "name", "given_name", "family_name",
            "gender", "birthdate", "phone_number", "email",
        ],
    })


@app.get("/jwks")
async def jwks() -> JSONResponse:
    return JSONResponse(JWKS)


# Authorize: login form + redirect
@app.get("/authorize", response_class=HTMLResponse)
async def authorize_form(client_id: str, redirect_uri: str, state: str = "",
                         scope: str = "openid", response_type: str = "code") -> HTMLResponse:
    if response_type != "code":
        raise HTTPException(400, "only response_type=code supported")
    if client_id != CLIENT_ID:
        raise HTTPException(400, f"unknown client_id (expected {CLIENT_ID!r})")

    options = "\n".join(
        f'<option value="{fid}">{c["name"]}: FIN {fid}</option>'
        for fid, c in CITIZENS.items()
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Fayda Replica: demo login</title>
<style>
  body {{ font-family: Inter, system-ui, sans-serif; max-width: 480px; margin: 4em auto; padding: 2em;
          background: #fafbfc; color: #222; }}
  .banner {{ background: #fff8c4; border-left: 4px solid #d4a72c; padding: 1em; border-radius: 6px;
             margin-bottom: 2em; font-size: 14px; }}
  h1 {{ font-size: 22px; margin-bottom: 0.2em; }}
  .sub {{ color: #666; margin-top: 0; }}
  label {{ display: block; font-size: 14px; margin-top: 1.5em; }}
  select, button {{ width: 100%; padding: 0.6em; margin-top: 0.4em; font-size: 14px;
                    border: 1px solid #ddd; border-radius: 4px; }}
  button {{ margin-top: 2em; background: #1D4ED8; color: white; border: none; cursor: pointer; }}
  button:hover {{ background: #1E40AF; }}
  code {{ background: #eee; padding: 1px 6px; border-radius: 3px; font-size: 13px; }}
</style>
</head>
<body>
  <div class="banner">
    <strong>Demo replica: Fayda.</strong> No real biometric flow; pick a
    citizen and click to issue a token. Production deploys swap this for
    Ethiopia&rsquo;s real Fayda OIDC.
  </div>
  <h1>Sign in with Fayda</h1>
  <p class="sub">Authenticating for client <code>{client_id}</code>.</p>
  <form method="post" action="/authorize">
    <input type="hidden" name="client_id" value="{_html.escape(client_id, quote=True)}">
    <input type="hidden" name="redirect_uri" value="{_html.escape(redirect_uri, quote=True)}">
    <input type="hidden" name="state" value="{_html.escape(state, quote=True)}">
    <input type="hidden" name="scope" value="{_html.escape(scope, quote=True)}">
    <label>Choose a citizen:
      <select name="fayda_id" required>
        {options}
      </select>
    </label>
    <button type="submit">Sign in</button>
  </form>
</body>
</html>"""
    return HTMLResponse(html)


@app.post("/authorize")
async def authorize_submit(
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(""),
    scope: str = Form("openid"),
    fayda_id: str = Form(...),
) -> RedirectResponse:
    if fayda_id not in CITIZENS:
        raise HTTPException(400, f"unknown citizen FIN {fayda_id}")
    code = secrets.token_urlsafe(24)
    _AUTH_CODES[code] = {
        "fayda_id": fayda_id,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "issued_at": time.time(),
    }
    params = {"code": code}
    if state:
        params["state"] = state
    url = f"{redirect_uri}?{urlencode(params)}"
    log.info("issued code for %s -> %s", fayda_id, url)
    return RedirectResponse(url, status_code=302)


# Token exchange
@app.post("/token")
async def token(request: Request,
                grant_type: str = Form(...),
                code: str = Form(None),
                redirect_uri: str = Form(None),
                client_id: str = Form(None),
                client_secret: str = Form(None)) -> JSONResponse:
    if grant_type != "authorization_code":
        raise HTTPException(400, f"unsupported grant_type {grant_type!r}")

    # Allow client auth via either Basic header OR form body.
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("basic "):
        import base64
        decoded = base64.b64decode(auth_header.split(None, 1)[1]).decode()
        client_id, client_secret = decoded.split(":", 1)

    if client_id != CLIENT_ID or client_secret != CLIENT_SECRET:
        raise HTTPException(401, "invalid client credentials")

    entry = _AUTH_CODES.pop(code, None)
    if not entry:
        raise HTTPException(400, "invalid_grant")
    if redirect_uri != entry["redirect_uri"]:
        raise HTTPException(400, "redirect_uri mismatch")

    citizen = CITIZENS[entry["fayda_id"]]
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": entry["fayda_id"],
        "aud": client_id,
        "exp": now + 900,
        "iat": now,
        "fayda_id": entry["fayda_id"],
        "name": citizen["name"],
        "given_name": citizen.get("given_name", ""),
        "family_name": citizen.get("family_name", ""),
        "gender": citizen.get("gender", ""),
        "birthdate": citizen.get("birthdate", ""),
        "phone_number": citizen.get("phone_number", ""),
        "email": citizen.get("email", ""),
    }
    id_token = jwt.encode(claims, _private_pem.decode(), algorithm="RS256",
                          headers={"kid": KID})
    access_token = secrets.token_urlsafe(32)
    _ACCESS_TOKENS[access_token] = {"sub": entry["fayda_id"], "exp": now + 900}

    return JSONResponse({
        "access_token": access_token,
        "id_token": id_token,
        "token_type": "Bearer",
        "expires_in": 900,
        "scope": entry["scope"],
    })


# UserInfo
@app.get("/userinfo")
async def userinfo(request: Request) -> JSONResponse:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    tok = auth.split(None, 1)[1]
    entry = _ACCESS_TOKENS.get(tok)
    if not entry or entry["exp"] < time.time():
        raise HTTPException(401, "invalid_token")
    c = CITIZENS[entry["sub"]]
    return JSONResponse({
        "sub": entry["sub"],
        "fayda_id": entry["sub"],
        "name": c["name"],
        "given_name": c.get("given_name", ""),
        "family_name": c.get("family_name", ""),
        "gender": c.get("gender", ""),
        "birthdate": c.get("birthdate", ""),
        "phone_number": c.get("phone_number", ""),
        "email": c.get("email", ""),
    })


# Email-OTP verification endpoints (not part of OIDC)
#
# The public dashboard proxies the browser's two calls straight here. Fayda
# owns the entire flow: registry check -> OTP mint -> email (Fayda's own SMTP) ->
# code confirmation. A real external IdP would expose an equivalent
# verification API; the OIDC endpoints above are independent of it.

@app.post("/verify/challenge")
async def verify_challenge(req: ChallengeReq) -> JSONResponse:
    """Step 1: confirm the FAN exists, then email a fresh 6-digit OTP to the
    identity's registered address via Fayda's own SMTP."""
    fan = (req.fan or "").strip()
    if not (fan.isdigit() and 12 <= len(fan) <= 16):
        return JSONResponse({"ok": False, "reason": "invalid_fan"}, status_code=400)

    now = time.time()
    prior = _OTP.get(fan)
    if prior and (now - prior.get("last_sent_at", 0)) < _OTP_RESEND_COOLDOWN_S:
        return JSONResponse({"ok": False, "reason": "rate_limited"}, status_code=429)

    citizen = CITIZENS.get(fan)
    if not citizen:
        return JSONResponse({"ok": False, "reason": "not_found"}, status_code=404)

    # Two-factor: the visitor must supply the email registered to this FAN. The
    # OTP goes only to the address on the record, and only when the supplied
    # address matches it, so knowing a FAN alone is not enough and the code
    # cannot be diverted to an attacker-chosen inbox.
    record_email = (citizen.get("email") or "").strip()
    if not record_email:
        return JSONResponse(
            {"ok": False, "reason": "no_email_on_record"}, status_code=422)

    supplied_email = (req.email or "").strip()
    if not supplied_email:
        return JSONResponse({"ok": False, "reason": "email_required"}, status_code=400)
    if supplied_email.casefold() != record_email.casefold():
        return JSONResponse({"ok": False, "reason": "email_mismatch"}, status_code=403)
    email = record_email

    code = f"{random.randint(0, 999999):06d}"
    _OTP[fan] = {
        "code": code,
        "email": email,
        "name": citizen.get("name", ""),
        "given_name": citizen.get("given_name", ""),
        "family_name": citizen.get("family_name", ""),
        "phone_number": citizen.get("phone_number", ""),
        "fayda_id": citizen.get("fayda_id", fan),
        "expires_at": now + _OTP_TTL_S,
        "attempts": 0,
        "last_sent_at": now,
        "consumed": False,
    }

    # Development-only trace: the code is recoverable from the container logs
    # (`docker logs fayda-replica | grep OTP`) when email delivery is slow.
    log.info("[fayda][DEV-ONLY] OTP for FAN %s = %s (emailed to %s)",
             fan, code, email)

    subject = "Your Fayda verification code"
    body = (
        f"Hello {citizen.get('given_name') or citizen.get('name') or ''},\n\n"
        f"Your Fayda verification code for your NHDW access request is:\n\n"
        f"    {code}\n\n"
        f"Enter this code on the access-request form to confirm your identity. "
        f"It expires in 10 minutes. If you did not request this, you can ignore "
        f"this email.\n\n"
        f"Fayda (Ethiopian National ID)"
    )
    # Send in the background: an SMTP round-trip must not block (or time out)
    # the challenge response. The OTP is already stored and logged, and email is
    # best-effort. The task runs after the response is flushed.
    return JSONResponse(
        {
            "ok": True,
            "masked_email": _mask_email(email),
            "emailed": bool(SMTP_HOST and email),  # dispatched (SMTP configured)
        },
        background=BackgroundTask(_send_email, email, subject, body),
    )


@app.post("/verify/confirm")
async def verify_confirm(req: ConfirmReq) -> JSONResponse:
    """Step 2: check the submitted code against the stored challenge. On success
    return the verified identity for the access-request form to pre-fill."""
    fan = (req.fan or "").strip()
    code = (req.code or "").strip()

    entry = _OTP.get(fan)
    now = time.time()
    if not entry or entry.get("consumed") or entry.get("expires_at", 0) < now:
        return JSONResponse({"ok": False, "reason": "expired"}, status_code=400)

    if entry["attempts"] >= _OTP_MAX_ATTEMPTS:
        return JSONResponse(
            {"ok": False, "reason": "too_many_attempts"}, status_code=429)

    if code != entry["code"]:
        entry["attempts"] += 1
        return JSONResponse({"ok": False, "reason": "bad_code"}, status_code=400)

    entry["consumed"] = True   # single-use
    return JSONResponse({
        "ok": True,
        "fayda_id": entry["fayda_id"],
        "name": entry["name"],
        "given_name": entry["given_name"],
        "family_name": entry["family_name"],
        "phone_number": entry["phone_number"],
    })


@app.get("/")
async def index() -> JSONResponse:
    return JSONResponse({
        "service": "fayda-replica",
        "issuer": ISSUER,
        "citizens_loaded": len(CITIZENS),
        "note": "sandbox-only OIDC mock of Ethiopia's Fayda IdP",
    })
