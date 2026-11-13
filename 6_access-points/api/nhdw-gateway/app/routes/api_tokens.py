"""Personal Access Token (PAT) CRUD.

A user issues a PAT through the admin UI (or directly through this endpoint)
and uses it to authenticate API calls from tools that cannot carry a Keycloak
session: notebooks, Power BI, dataset exporters, and report generators in the
BI and workshop dashboards.

The token's effective permission set is the intersection of:
  - the owner's 4-axis grants (computed in permissions.py)
  - the PAT's optional scope override (accepted and stored here)

A PAT can therefore narrow the owner's access but never widen it.

Service-account tokens (owner_kc_sub null, role_code set) are bound to a role
rather than a user and serve system-to-system integrations.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import UserCtx, get_user
from .. import ops_db

log = logging.getLogger("gateway.api_tokens")

router = APIRouter(prefix="/api/api-tokens", tags=["api-tokens"])

PREFIX_LEN = 8
SECRET_LEN = 48
SECRET_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _mint() -> tuple[str, str, str]:
    """Return (full_token, prefix, secret_hash)."""
    prefix = "".join(secrets.choice(SECRET_ALPHABET) for _ in range(PREFIX_LEN))
    secret = "".join(secrets.choice(SECRET_ALPHABET) for _ in range(SECRET_LEN))
    secret_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return f"nhdw_pat_{prefix}_{secret}", prefix, secret_hash


def _has_cap(user: UserCtx, code: str) -> bool:
    return code in user.roles  # capability codes are read from the roles claim


class CreateTokenIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    scope: dict[str, Any] | None = None     # {geographies:[], programs:[]}
    expires_in_days: int = Field(default=90, ge=1, le=365)
    rate_limit_per_min: int = Field(default=120, ge=1, le=10000)
    for_kc_sub: str | None = None           # admin-only: issue for someone else
    role_code: str | None = None            # SA-only path (no owner)


@router.get("")
async def list_my_tokens(user: UserCtx = Depends(get_user),
                          all: bool = False) -> dict[str, Any]:
    """List the caller's tokens. With ?all=true and the `manage_api_tokens`
    capability, returns every token in the system."""
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        if all and _has_cap(user, "manage_api_tokens"):
            rows = await conn.fetch("""
                SELECT id, owner_kc_sub, role_code, name, prefix, scope,
                       rate_limit_per_min, created_at, expires_at, last_used_at,
                       revoked_at, is_active, display
                  FROM ops_perm.v_api_token ORDER BY created_at DESC
            """)
        else:
            rows = await conn.fetch("""
                SELECT id, owner_kc_sub, role_code, name, prefix, scope,
                       rate_limit_per_min, created_at, expires_at, last_used_at,
                       revoked_at, is_active, display
                  FROM ops_perm.v_api_token
                 WHERE owner_kc_sub::text = $1
                 ORDER BY created_at DESC
            """, user.kc_sub)
    return {"tokens": [_serialize(r) for r in rows]}


@router.post("")
async def create_token(body: CreateTokenIn,
                       user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Issue a new PAT. The full secret is returned once; only its hash is
    stored."""
    target_kc_sub = user.kc_sub
    if body.for_kc_sub:
        if not _has_cap(user, "issue_api_token_for_others"):
            raise HTTPException(403, "missing capability: issue_api_token_for_others")
        target_kc_sub = body.for_kc_sub
    if body.role_code:
        if not _has_cap(user, "manage_api_tokens"):
            raise HTTPException(403, "service-account tokens require manage_api_tokens")
        target_kc_sub = None

    full_token, prefix, secret_hash = _mint()
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO ops_perm.api_token
                (owner_kc_sub, role_code, name, prefix, secret_hash, scope,
                 rate_limit_per_min, expires_at, created_by_kc_sub)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7,
                    now() + ($8::int || ' days')::interval, $9)
            RETURNING id, prefix, created_at, expires_at
        """, target_kc_sub, body.role_code, body.name, prefix, secret_hash,
            (body.scope and __import__("json").dumps(body.scope)),
            body.rate_limit_per_min, body.expires_in_days,
            user.kc_sub or None)
    log.info("PAT created: id=%s name=%s owner=%s by=%s",
             row["id"], body.name, target_kc_sub, user.kc_sub)
    return {
        "id":         row["id"],
        "name":       body.name,
        "prefix":     prefix,
        "token":      full_token,         # returned only in this response
        "expires_at": row["expires_at"].isoformat(),
        "warning":    "Save the token now; it cannot be retrieved later.",
    }


@router.delete("/{token_id}")
async def revoke_token(token_id: int,
                       reason: str = "user revocation",
                       user: UserCtx = Depends(get_user)) -> dict[str, str]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        owner = await conn.fetchval("""
            SELECT owner_kc_sub FROM ops_perm.api_token WHERE id = $1
        """, token_id)
        if owner is None:
            raise HTTPException(404, "token not found")
        # Users revoke their own tokens; manage_api_tokens can revoke any.
        if (str(owner) != user.kc_sub
                and not _has_cap(user, "manage_api_tokens")):
            raise HTTPException(403, "not your token and missing manage_api_tokens")
        await conn.execute("""
            UPDATE ops_perm.api_token
               SET revoked_at = now(), revoke_reason = $2
             WHERE id = $1
        """, token_id, reason)
    return {"status": "revoked", "id": str(token_id)}


@router.get("/{token_id}/audit")
async def token_audit(token_id: int,
                      limit: int = 100,
                      user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        owner = await conn.fetchval(
            "SELECT owner_kc_sub FROM ops_perm.api_token WHERE id = $1", token_id)
        if owner is None:
            raise HTTPException(404, "token not found")
        if (str(owner) != user.kc_sub
                and not _has_cap(user, "manage_api_tokens")):
            raise HTTPException(403, "not your token and missing manage_api_tokens")
        rows = await conn.fetch("""
            SELECT id, ts, method, path, status_code, bytes_out, duration_ms,
                   client_ip::text, user_agent
              FROM ops_perm.api_token_audit
             WHERE token_id = $1
             ORDER BY ts DESC LIMIT $2
        """, token_id, max(1, min(limit, 1000)))
    return {"entries": [dict(r) for r in rows]}


def _serialize(r) -> dict[str, Any]:
    return {
        "id":                 r["id"],
        "owner_kc_sub":       str(r["owner_kc_sub"]) if r["owner_kc_sub"] else None,
        "role_code":          r["role_code"],
        "name":               r["name"],
        "prefix":             r["prefix"],
        "display":            r["display"],
        "scope":              r["scope"],
        "rate_limit_per_min": r["rate_limit_per_min"],
        "created_at":         r["created_at"].isoformat() if r["created_at"] else None,
        "expires_at":         r["expires_at"].isoformat() if r["expires_at"] else None,
        "last_used_at":       r["last_used_at"].isoformat() if r["last_used_at"] else None,
        "revoked_at":         r["revoked_at"].isoformat() if r["revoked_at"] else None,
        "is_active":          r["is_active"],
    }
