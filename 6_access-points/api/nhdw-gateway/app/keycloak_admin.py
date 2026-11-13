"""Keycloak Admin API client for access-request provisioning.

When an admin approves an access request, the gateway creates the applicant's
Keycloak account, assigns the granted realm role and sets a temporary password
that must be changed at first login. Self-service password reset uses
`execute-actions-email(UPDATE_PASSWORD)`.

Uses the same admin-token pattern as `7_security/keycloak/scripts/setup-keycloak.py`;
Keycloak remains the only identity store. Admin credentials come from
`keycloak-secrets` (the bootstrap admin) through the gateway environment. All
calls use the internal Keycloak service URL.
"""
from __future__ import annotations

import logging
import secrets
from typing import Any

import httpx

from .settings import settings

log = logging.getLogger("gateway.kcadmin")


class KeycloakAdminError(Exception):
    """A Keycloak Admin API call failed (surfaced to the caller as 502)."""


async def _admin_token(client: httpx.AsyncClient) -> str:
    r = await client.post(
        f"{settings.KEYCLOAK_INTERNAL_BASE}/realms/master/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": "admin-cli",
              "username": settings.KEYCLOAK_ADMIN_USER,
              "password": settings.KEYCLOAK_ADMIN_PASSWORD})
    if r.status_code != 200:
        raise KeycloakAdminError(
            f"admin token request failed: {r.status_code} {r.text[:160]}")
    return r.json()["access_token"]


async def provision_user(*, email: str, full_name: str | None,
                         role: str | None, realm: str | None = None
                         ) -> dict[str, Any]:
    """Create (or reuse) a realm user, assign `role` and set a temporary
    password with UPDATE_PASSWORD required at first login. Returns
    {kc_sub, created, role, role_assigned, temp_password, pw_set, realm,
    username}; the caller emails the credential.

    Idempotent: an existing user with this email is reused (role re-assigned,
    password reset), so a repeated approval neither fails nor duplicates."""
    realm = realm or settings.KEYCLOAK_PROVISION_REALM
    base = settings.KEYCLOAK_INTERNAL_BASE
    first, _, last = (full_name or "").strip().partition(" ")
    username = email
    async with httpx.AsyncClient(timeout=30) as c:
        tok = await _admin_token(c)
        h = {"Authorization": f"Bearer {tok}"}

        # 1. Create or find the user (the Keycloak user id becomes the `sub` claim).
        cr = await c.post(f"{base}/admin/realms/{realm}/users", headers=h, json={
            "username": username, "email": email,
            "firstName": first or email.split("@")[0], "lastName": last or "",
            "enabled": True, "emailVerified": False})
        if cr.status_code == 201:
            created = True
            kc_sub = cr.headers.get("location", "").rsplit("/", 1)[-1]
        elif cr.status_code == 409:
            created = False
            found = await c.get(f"{base}/admin/realms/{realm}/users",
                                headers=h, params={"email": email, "exact": "true"})
            arr = found.json() or []
            if not arr:
                raise KeycloakAdminError(
                    "user exists (409) but lookup by email returned none")
            kc_sub = arr[0]["id"]
            username = arr[0].get("username", email)
        else:
            raise KeycloakAdminError(
                f"create user failed: {cr.status_code} {cr.text[:160]}")

        # 2. Assign the granted realm role. Best-effort: an unknown role does not
        #    block the account; it is logged and reported in the result.
        role_assigned = False
        if role:
            rr = await c.get(f"{base}/admin/realms/{realm}/roles/{role}", headers=h)
            if rr.status_code == 200:
                ro = rr.json()
                mr = await c.post(
                    f"{base}/admin/realms/{realm}/users/{kc_sub}/role-mappings/realm",
                    headers=h, json=[{"id": ro["id"], "name": ro["name"]}])
                role_assigned = mr.status_code in (204, 200)
            else:
                log.warning("role %s not found in realm %s (%s)",
                            role, realm, rr.status_code)

        # 3. Set a temporary password and force a change at first login.
        #    execute-actions-email is not used here: mail link scanners (Gmail
        #    prefetches URLs) consume its single-use link before the applicant
        #    opens it. The gateway mails the temporary credential and the sign-in
        #    URL instead. The generated password meets the realm policy
        #    (length >= 10, upper, lower, digit).
        temp_password = f"Nhdw{secrets.randbelow(90) + 10}-{secrets.token_hex(4)}"
        rp = await c.put(
            f"{base}/admin/realms/{realm}/users/{kc_sub}/reset-password",
            headers=h, json={"type": "password", "value": temp_password,
                             "temporary": True})
        pw_set = rp.status_code in (200, 204)
        if not pw_set:
            log.warning("set temp password failed for %s: %s %s",
                        email, rp.status_code, rp.text[:160])
        # Force the password change on first login; mark admin-provisioned users
        # email-verified so they are not held behind a VERIFY_EMAIL action.
        await c.put(f"{base}/admin/realms/{realm}/users/{kc_sub}", headers=h,
                    json={"requiredActions": ["UPDATE_PASSWORD"],
                          "emailVerified": True})

    return {"kc_sub": kc_sub, "created": created, "role": role,
            "role_assigned": role_assigned, "temp_password": temp_password,
            "pw_set": pw_set, "realm": realm, "username": username}


async def send_password_reset(*, realm: str, user_id: str) -> bool:
    """Self-service password reset: trigger execute-actions-email(UPDATE_PASSWORD)
    so Keycloak mails a set-password link through the realm SMTP relay."""
    base = settings.KEYCLOAK_INTERNAL_BASE
    async with httpx.AsyncClient(timeout=30) as c:
        tok = await _admin_token(c)
        r = await c.put(
            f"{base}/admin/realms/{realm}/users/{user_id}/execute-actions-email",
            headers={"Authorization": f"Bearer {tok}"},
            params={"lifespan": 43200}, json=["UPDATE_PASSWORD"])
    return r.status_code in (200, 204)


async def list_user_sessions(*, realm: str, user_id: str) -> list[dict[str, Any]]:
    """The user's active Keycloak sessions (device, IP, last access)."""
    base = settings.KEYCLOAK_INTERNAL_BASE
    async with httpx.AsyncClient(timeout=15) as c:
        tok = await _admin_token(c)
        r = await c.get(
            f"{base}/admin/realms/{realm}/users/{user_id}/sessions",
            headers={"Authorization": f"Bearer {tok}"})
    return r.json() if r.status_code == 200 else []
