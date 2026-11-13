"""/api/me: the caller's identity for the browser; each call also upserts the
user into the just-in-time user cache."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import UserCtx, get_user
from .. import ops_db, keycloak_admin

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("")
async def me(request: Request, user: UserCtx = Depends(get_user)) -> dict:
    await ops_db.upsert_user(user)
    return {
        "kc_sub":         user.kc_sub,
        "username":       user.username,
        "full_name":      user.full_name,
        "email":          user.email,
        "primary_role":   user.primary_role,
        "roles":          user.roles,
        "realm":          user.realm,
        "locale":         user.locale,
        "geographies":    user.geographies,
        "programs":       user.programs,
        "purpose_of_use": user.purpose_of_use,
    }


@router.post("/reset-password")
async def reset_password(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Self-service password reset: Keycloak emails the caller a set-password
    link through the configured SMTP relay. The account is taken from the
    verified token and there is no target parameter, so a caller can only
    reset their own password."""
    if not user.kc_sub:
        raise HTTPException(401, "authenticated session required")
    try:
        sent = await keycloak_admin.send_password_reset(
            realm=user.realm, user_id=user.kc_sub)
    except keycloak_admin.KeycloakAdminError as e:
        raise HTTPException(502, f"could not send the reset email: {e}")
    return {"sent": sent, "email": user.email}


@router.get("/sessions")
async def my_sessions(user: UserCtx = Depends(get_user)) -> list[dict[str, Any]]:
    """The caller's active Keycloak sessions (device / IP / last access)."""
    if not user.kc_sub:
        return []
    try:
        sess = await keycloak_admin.list_user_sessions(
            realm=user.realm, user_id=user.kc_sub)
    except keycloak_admin.KeycloakAdminError:
        return []
    return [{"ip":          s.get("ipAddress"),
             "start":       s.get("start"),
             "last_access": s.get("lastAccess"),
             "clients":     sorted((s.get("clients") or {}).values())}
            for s in sess]
