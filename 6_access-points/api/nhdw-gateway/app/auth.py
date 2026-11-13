"""Credential resolution: Keycloak JWT validation plus PAT and mock paths.

Credential paths, in priority order:

  1. PAT:  `Authorization: Bearer nhdw_pat_<prefix>_<secret>`. Looked up in
           ops_perm.api_token; the token's scope can only narrow its owner.

  2. JWT:  `Authorization: Bearer <keycloak access token>`, verified against
           the issuer's JWKS. The `iss` claim selects the realm and must be
           listed in `settings.KEYCLOAK_ALLOWED_ISSUERS`. JWKS is fetched on
           demand and cached per issuer.

  3. Mock: `X-Mock-User: <kc_sub>` and `X-Mock-Role: R-...` headers, honoured
           only when `DEV_AUTH=true` (local, no Keycloak) or when
           `GATEWAY_DEMO_IMPERSONATION=true` and the caller presents the
           `X-Mock-Secret` shared secret (dashboard view-as). Off by default.

With no credential, the request maps to the anonymous R-PUB-ANON role when
DEV_AUTH or GATEWAY_PUBLIC_ANON is set; otherwise it is rejected with 401.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import Header, HTTPException, Request

from .settings import settings

log = logging.getLogger("gateway.auth")

# Per-issuer JWKS cache keyed by the token's `iss` claim. Refreshed via
# _invalidate_jwks(iss) when a token names an unknown key id.
_jwks_by_iss: dict[str, dict[str, Any]] = {}


def _allowed_issuers() -> set[str]:
    return {s.strip() for s in settings.KEYCLOAK_ALLOWED_ISSUERS.split(",")
            if s.strip()}


@dataclass(frozen=True)
class UserCtx:
    kc_sub: str
    username: str
    realm: str
    primary_role: str
    roles: list[str]
    email: str = ""
    full_name: str = ""
    locale: str = "en"
    geographies: list[str] = field(default_factory=list)
    programs: list[str] = field(default_factory=list)
    purpose_of_use: str = "public-health"
    # Federated-identity trust tier. `idp` is the brokering IdP alias (empty for
    # direct password logins); `idp_trust` is "high" or "low". Internal
    # credentials (password login, PAT, dev/mock) are "high". A low-trust
    # federated login (e.g. public Google) is stamped "low" by a Keycloak mapper
    # and kept off sensitive actions by the OPA trust guard. An absent claim
    # means "high": only internal identities lack the claim, because low-trust
    # brokers always stamp it.
    idp: str = ""
    idp_trust: str = "high"
    # A PAT's declared scope as user_scope kinds (geography, programme mapped to
    # value lists), or None for interactive principals.
    # permissions.effective_user_scope intersects it with the owner's DB scope,
    # so a token can only narrow its owner's data reach, never widen it.
    token_scope: dict[str, list[str]] | None = None

    def scope_dict(self) -> dict[str, Any]:
        return {
            "geographies": list(self.geographies),
            "programs": list(self.programs),
            "purpose_of_use": self.purpose_of_use,
            "role": self.primary_role,
        }


def _internal_url_for_iss(iss: str) -> str:
    """Rewrite an external issuer URL to the in-cluster equivalent.

    Tokens carry Keycloak's KC_HOSTNAME (e.g. http://127.0.0.1:8090/realms/X),
    which is not reachable from inside the cluster network."""
    pub = settings.KEYCLOAK_PUBLIC_BASE.rstrip("/")
    intl = settings.KEYCLOAK_INTERNAL_BASE.rstrip("/")
    if iss.startswith(pub):
        return intl + iss[len(pub):]
    return iss


async def _load_jwks_for_iss(iss: str) -> dict[str, Any]:
    """Fetch and cache the JWKS for one issuer (realm), using the in-cluster
    host for both the discovery document and the jwks_uri."""
    cached = _jwks_by_iss.get(iss)
    if cached is not None:
        return cached
    internal = _internal_url_for_iss(iss)
    async with httpx.AsyncClient(timeout=5) as c:
        disc = (await c.get(
            f"{internal}/.well-known/openid-configuration")).json()
        # The discovery document advertises the public jwks_uri.
        jwks_uri = _internal_url_for_iss(disc["jwks_uri"])
        jwks = (await c.get(jwks_uri)).json()
    _jwks_by_iss[iss] = jwks
    return jwks


def _invalidate_jwks(iss: str) -> None:
    _jwks_by_iss.pop(iss, None)


def _dev_user(x_mock_user: str | None, x_mock_role: str | None,
              x_mock_trust: str | None = None) -> UserCtx:
    if not x_mock_user:
        # Anonymous principal for the public dashboard.
        return UserCtx(
            kc_sub="", username="anonymous", realm="public",
            primary_role="R-PUB-ANON", roles=["R-PUB-ANON"])
    role = x_mock_role or "R-DEV"
    # The trust tier can only be downgraded through this channel: "low" is
    # honoured so view-as can show the trust guard and column masking as a
    # federated visitor sees them; anything else stays "high", the default for
    # internal credentials. Downgrading never widens access.
    trust = "low" if (x_mock_trust or "").lower() == "low" else "high"
    return UserCtx(
        kc_sub=x_mock_user, username=f"dev:{x_mock_user[:8]}",
        realm="dev", primary_role=role, roles=[role],
        idp="mock" if trust == "low" else "",
        idp_trust=trust,
        full_name=f"Dev user {x_mock_user[:8]}")


async def get_user(
    request: Request,
    authorization: str | None = Header(default=None),
    x_mock_user: str | None = Header(default=None, alias="X-Mock-User"),
    x_mock_role: str | None = Header(default=None, alias="X-Mock-Role"),
    x_mock_secret: str | None = Header(default=None, alias="X-Mock-Secret"),
    x_mock_trust: str | None = Header(default=None, alias="X-Mock-Trust"),
    x_act_as_owner: str | None = Header(default=None, alias="X-Act-As-Owner"),
) -> UserCtx:
    """FastAPI dependency returning the UserCtx for the calling principal.
    See the module docstring for the credential paths."""
    # PAT: always honoured (a real credential, not a dev convenience).
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
        if token.startswith("nhdw_pat_"):
            return await _pat_user(token, request, act_as_owner=x_act_as_owner)
    else:
        token = ""

    # JWT: verify against the issuer's JWKS.
    if token:
        return await _jwt_user(token)

    # Mock headers: local dev mode or the impersonation channel.
    if x_mock_user or x_mock_role:
        # DEV_AUTH: local dev without Keycloak; never enabled on an exposed gateway.
        if settings.DEV_AUTH:
            return _dev_user(x_mock_user, x_mock_role, x_mock_trust)
        # Deployed impersonation: the ingress is reachable, so the X-Mock-*
        # channel requires the shared secret; otherwise any caller could claim
        # R-SUPER. An empty secret disables the channel (fail-secure).
        if (settings.GATEWAY_DEMO_IMPERSONATION
                and settings.GATEWAY_IMPERSONATION_SECRET
                and x_mock_secret == settings.GATEWAY_IMPERSONATION_SECRET):
            return _dev_user(x_mock_user, x_mock_role, x_mock_trust)
        # Missing or wrong secret, or impersonation off: fall through to 401.
    elif settings.DEV_AUTH or settings.GATEWAY_PUBLIC_ANON:
        # No credential: anonymous public tier (R-PUB-ANON), enabled by DEV_AUTH
        # (local) or GATEWAY_PUBLIC_ANON (deployed public portal). Widget grants
        # and OPA restrict R-PUB-ANON to the public widgets and aggregate
        # (k>=5) data.
        return _dev_user(None, None)                        # public R-PUB-ANON

    raise HTTPException(401, "Bearer token required")


async def _jwt_user(token: str) -> UserCtx:
    try:
        from jose import jwt
    except ImportError as e:
        raise HTTPException(500, f"jose not installed: {e}")

    try:
        unverified_header = jwt.get_unverified_header(token)
        unverified_claims = jwt.get_unverified_claims(token)
    except Exception as e:                                  # noqa: BLE001
        raise HTTPException(401, f"Malformed token: {e}")

    iss = unverified_claims.get("iss", "")
    if iss not in _allowed_issuers():
        raise HTTPException(401, f"Issuer not allowed: {iss}")

    jwks = await _load_jwks_for_iss(iss)
    kid = unverified_header.get("kid")
    key = next((k for k in jwks["keys"] if k.get("kid") == kid), None)
    if not key:
        _invalidate_jwks(iss)
        jwks = await _load_jwks_for_iss(iss)
        key = next((k for k in jwks["keys"] if k.get("kid") == kid), None)
        if not key:
            raise HTTPException(401, "Unknown signing key")

    accepted_aud = [a.strip() for a in
                    [settings.KEYCLOAK_AUDIENCE,
                     *settings.KEYCLOAK_EXTRA_AUDIENCES.split(",")]
                    if a.strip()]
    claims = None
    last_err: Exception | None = None
    for aud in accepted_aud:
        try:
            claims = jwt.decode(token, key,
                                algorithms=[unverified_header["alg"]],
                                audience=aud,
                                options={"verify_at_hash": False})
            break
        except Exception as exc:                            # noqa: BLE001
            last_err = exc
    if claims is None:
        raise HTTPException(401, f"Invalid token: {last_err}")

    # Role source, in order: the standard realm_access.roles (present when the
    # built-in 'roles' scope exists), else the top-level `roles` claim from the
    # nhdw-flat-roles default client scope. The seeded realms have no built-in
    # roles scope, so tokens normally carry the flat claim.
    realm_roles = ((claims.get("realm_access") or {}).get("roles")
                   or claims.get("roles") or [])
    if isinstance(realm_roles, str):          # single-valued mapper edge case
        realm_roles = [realm_roles]
    primary = next((r for r in realm_roles if r.startswith("R-")), "R-UNKNOWN")
    return UserCtx(
        kc_sub=claims.get("sub", ""),
        username=claims.get("preferred_username", ""),
        realm=(claims.get("iss") or "").rsplit("/", 1)[-1],
        primary_role=primary,
        roles=realm_roles,
        email=claims.get("email", ""),
        full_name=claims.get("name", ""),
        locale=claims.get("locale", "en"),
        geographies=list(claims.get("geographies") or []),
        programs=list(claims.get("programs") or []),
        # Trust tier from the Keycloak idp_trust mapper and the broker alias
        # (identity_provider). Direct password logins carry neither, so "high".
        idp=claims.get("identity_provider", "") or "",
        idp_trust=(claims.get("idp_trust") or "high"),
    )


# PAT validation
import hashlib

from . import ops_db


async def _pat_user(token: str, request: Request,
                    act_as_owner: str | None = None) -> UserCtx:
    """Validate a personal access token and return its UserCtx.

    Format: nhdw_pat_<prefix>_<secret>, stored as sha256(secret). The token's
    scope, when set, narrows the owner's geographies and programmes; it never
    widens them.

    act_as_owner: a service-account token (owner_kc_sub NULL, role_code set)
    whose role holds the `act_as_owner` capability may name a target owner
    (kc_sub) in the X-Act-As-Owner header. The effective principal becomes that
    owner, with the owner's active role, identity and DB scope, so OPA and
    effective_user_scope enforce exactly what the owner may see. The token's
    declared scope still narrows the owner."""
    parts = token.split("_", 3)
    if len(parts) != 4 or parts[0] != "nhdw" or parts[1] != "pat":
        raise HTTPException(401, "malformed PAT")
    prefix, secret = parts[2], parts[3]
    secret_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()

    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT t.id, t.owner_kc_sub, t.role_code, t.scope,
                   t.revoked_at, t.expires_at,
                   u.username, u.email, u.full_name, u.realm
              FROM ops_perm.api_token t
              LEFT JOIN ops_identity."user" u ON u.kc_sub = t.owner_kc_sub
             WHERE t.prefix = $1 AND t.secret_hash = $2
        """, prefix, secret_hash)
    if row is None:
        raise HTTPException(401, "invalid PAT")
    if row["revoked_at"] is not None:
        raise HTTPException(401, "PAT revoked")
    if row["expires_at"] is not None and row["expires_at"].timestamp() < _now_ts():
        raise HTTPException(401, "PAT expired")

    # Resolve the role: owner's primary role if user-bound, else the SA role.
    role = row["role_code"]
    if not role and row["owner_kc_sub"]:
        async with pool.acquire() as conn:
            ur = await conn.fetchval("""
                SELECT r.code FROM ops_perm.user_role ur
                  JOIN ops_perm.role r ON r.id = ur.role_id
                 WHERE ur.kc_sub = $1 AND ur.is_active = true
                 ORDER BY ur.granted_at DESC LIMIT 1
            """, row["owner_kc_sub"])
            role = ur or "R-UNKNOWN"
    role = role or "R-UNKNOWN"

    # The declared scope is carried as `token_scope` (user_scope kinds) and
    # intersected with the owner's DB scope in permissions.effective_user_scope.
    scope = row["scope"] or {}
    if isinstance(scope, str):          # asyncpg returns jsonb as a raw string
        try:
            scope = json.loads(scope)
        except Exception:               # noqa: BLE001
            scope = {}
    geos = list(scope.get("geographies") or [])
    progs = list(scope.get("programs") or [])
    _ts: dict[str, list[str]] = {}
    if geos:  _ts["geography"] = geos
    if progs: _ts["programme"] = progs
    token_scope = _ts or None

    # Fire-and-forget audit row and last_used_at update.
    async def _record():
        try:
            async with pool.acquire() as c2:
                await c2.execute("UPDATE ops_perm.api_token SET last_used_at = now() WHERE id = $1",
                                 row["id"])
                await c2.execute("""
                    INSERT INTO ops_perm.api_token_audit
                        (token_id, method, path, status_code, client_ip, user_agent)
                    VALUES ($1, $2, $3, $4, $5::inet, $6)
                """, row["id"], request.method, request.url.path, 0,
                    (request.client.host if request.client else None),
                    request.headers.get("user-agent"))
        except Exception as e:                                # noqa: BLE001
            log.debug("PAT audit write failed: %s", e)
    import asyncio
    asyncio.create_task(_record())

    # Act-as-owner
    if act_as_owner:
        # Only a service-account token (owner_kc_sub NULL, role_code set) may
        # act as an owner; a user-bound PAT must never reach another owner.
        if row["owner_kc_sub"] is not None or not row["role_code"]:
            raise HTTPException(403, "act-as-owner requires a service-account token")
        async with pool.acquire() as conn:
            # The capability must be read from ops_perm.role_capability: a
            # service principal's roles list is just [role_code], so it carries
            # no capability claims to check.
            has_cap = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT 1 FROM ops_perm.role_capability rc
                      JOIN ops_perm.role r ON r.id = rc.role_id
                     WHERE r.code = $1 AND rc.capability_code = 'act_as_owner')
            """, row["role_code"])
            if not has_cap:
                raise HTTPException(403, "token role lacks capability: act_as_owner")
            # The target must be a known user with an active role. Role
            # resolution matches the owner-PAT path above.
            owner = await conn.fetchrow("""
                SELECT u.username, u.email, u.full_name, u.realm,
                       (SELECT r.code FROM ops_perm.user_role ur
                          JOIN ops_perm.role r ON r.id = ur.role_id
                         WHERE ur.kc_sub = u.kc_sub AND ur.is_active = true
                         ORDER BY ur.granted_at DESC LIMIT 1) AS role
                  FROM ops_identity."user" u
                 WHERE u.kc_sub = $1
            """, act_as_owner)
        if owner is None or not owner["role"]:
            raise HTTPException(403, "act-as-owner target is not an active user")
        return UserCtx(
            kc_sub=str(act_as_owner),
            username=owner["username"] or f"act:{str(act_as_owner)[:8]}",
            realm=owner["realm"] or "service",
            primary_role=owner["role"],
            roles=[owner["role"]],
            email=owner["email"] or "",
            full_name=owner["full_name"] or "",
            # geographies/programs stay empty: effective_user_scope loads the
            # owner's DB scope by kc_sub and narrows it by token_scope (the
            # service account's declared scope), so the token can only restrict.
            token_scope=token_scope,
        )

    return UserCtx(
        kc_sub=str(row["owner_kc_sub"] or ""),
        username=row["username"] or f"pat:{prefix}",
        realm=row["realm"] or "service",
        primary_role=role,
        roles=[role],
        email=row["email"] or "",
        full_name=row["full_name"] or "",
        geographies=geos,
        programs=progs,
        token_scope=token_scope,
    )


def _now_ts() -> float:
    import time as _time
    return _time.time()
