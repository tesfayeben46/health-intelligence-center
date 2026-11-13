"""/api/perm-admin: admin CRUD for the four-axis permission model.

Used by the admin SPA's RolesAdmin, FilterCatalog, WidgetCatalog and SSO pages.
Every write is recorded in ops_perm.audit_permission_change under a
policy_revision, with before and after values.

Each endpoint requires a capability: `edit_roles` (roles, grants,
permissions), `manage_users` (catalog reads, user roles and scope),
`manage_catalog` (catalog overrides and orphans) or `manage_identity_sources`
(identity sources).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import ops_db, permissions
from ..auth import UserCtx, get_user

router = APIRouter(prefix="/api/perm-admin", tags=["perm-admin"])
log = logging.getLogger("gateway.routes.perm_admin")


# Helpers
async def _require_capability(user: UserCtx, capability: str) -> None:
    """Raise 403 if the caller's roles do not grant `capability`."""
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rids = await permissions._resolve_role_ids(conn, user)
        caps = await permissions._role_capabilities(conn, rids)
    if capability not in caps:
        raise HTTPException(403, f"missing capability: {capability}")


async def _new_revision(conn, actor_sub: str, note: str) -> int:
    rec = await conn.fetchrow("""
        INSERT INTO ops_perm.policy_revision (created_by, note)
        VALUES (NULLIF($1,'')::uuid, $2)
        RETURNING id
    """, actor_sub, note)
    return rec["id"]


async def _audit(conn, *, actor_sub: str, revision_id: int, table_name: str,
                 op: str, pk: dict, before: dict | None = None,
                 after: dict | None = None) -> None:
    await conn.execute("""
        INSERT INTO ops_perm.audit_permission_change
            (actor_sub, revision_id, table_name, op, pk, before, after)
        VALUES (NULLIF($1,'')::uuid, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb)
    """, actor_sub, revision_id, table_name, op,
        json.dumps(pk),
        json.dumps(before) if before else None,
        json.dumps(after) if after else None)


# Catalog reads for picker UIs
@router.get("/catalog")
async def get_catalog(user: UserCtx = Depends(get_user)) -> dict:
    """Bulk read of dashboards, pages, widgets, filters, mart_attrs, identity
    sources and the role catalog. The admin SPA loads this once to populate
    every picker."""
    await _require_capability(user, "manage_users")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        dashboards = await conn.fetch(
            "SELECT id, title, description FROM ops_perm.dashboard ORDER BY id")
        pages = await conn.fetch(
            "SELECT id, dashboard_id, title, ordering FROM ops_perm.page "
            "ORDER BY dashboard_id, ordering, id")
        widgets = await conn.fetch(
            "SELECT id, title, kind, requires, description, source, component_type "
            "FROM ops_perm.widget ORDER BY id")
        filters = await conn.fetch(
            "SELECT id, title, source, source_ref, static_values FROM ops_perm.filter "
            "ORDER BY id")
        mart_attrs = await conn.fetch(
            "SELECT schema_name, table_name, subject, data_class, granularity, "
            "default_sensitivity, description FROM ops_perm.mart_attrs "
            "ORDER BY schema_name, table_name")
        idps = await conn.fetch(
            "SELECT id, code, name, kind, protocol, status, config_json, "
            "keycloak_alias, created_at, updated_at FROM ops_identity.identity_source "
            "ORDER BY id")
        roles = await conn.fetch(
            "SELECT id, code, name, description, combine_strategy FROM ops_perm.role "
            "ORDER BY id")
    return {
        "dashboards":       [dict(r) for r in dashboards],
        "pages":            [dict(r) for r in pages],
        "widgets":          [_decode_widget(r) for r in widgets],
        "filters":          [_decode_filter(r) for r in filters],
        "mart_attrs":       [dict(r) for r in mart_attrs],
        "identity_sources": [_decode_idp(r) for r in idps],
        "roles":            [dict(r) for r in roles],
    }


def _decode_widget(r) -> dict:
    d = dict(r)
    d["requires"] = permissions._decode_jsonb(d.get("requires"))
    return d


def _decode_filter(r) -> dict:
    d = dict(r)
    d["static_values"] = permissions._decode_jsonb(d.get("static_values"))
    return d


def _decode_idp(r) -> dict:
    d = dict(r)
    d["config_json"] = permissions._decode_jsonb(d.get("config_json"))
    d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
    d["updated_at"] = d["updated_at"].isoformat() if d.get("updated_at") else None
    return d


# Role CRUD
class RoleIn(BaseModel):
    code:             str = Field(..., min_length=3, max_length=64)
    name:             str
    description:      Optional[str] = None
    combine_strategy: str = "union"
    capabilities:     list[str] = []


@router.get("/roles")
async def list_roles(user: UserCtx = Depends(get_user)) -> list[dict]:
    await _require_capability(user, "manage_users")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT r.id, r.code, r.name, r.description, r.combine_strategy,
                   r.created_at, r.updated_at,
                   COUNT(DISTINCT ur.kc_sub) AS user_count
            FROM ops_perm.role r
            LEFT JOIN ops_perm.user_role ur ON ur.role_id = r.id AND ur.is_active
            GROUP BY r.id ORDER BY r.id
        """)
    out = []
    for r in rows:
        d = dict(r)
        d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
        d["updated_at"] = d["updated_at"].isoformat() if d.get("updated_at") else None
        out.append(d)
    return out


@router.get("/roles/{role_id}")
async def get_role(role_id: int, user: UserCtx = Depends(get_user)) -> dict:
    await _require_capability(user, "manage_users")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        role = await conn.fetchrow("""
            SELECT id, code, name, description, combine_strategy
            FROM ops_perm.role WHERE id = $1
        """, role_id)
        if not role:
            raise HTTPException(404, "role not found")
        caps = await conn.fetch(
            "SELECT capability_code FROM ops_perm.role_capability WHERE role_id = $1",
            role_id)
        dashboards = await conn.fetch(
            "SELECT dashboard_id FROM ops_perm.role_dashboard WHERE role_id = $1",
            role_id)
        widgets = await conn.fetch(
            "SELECT dashboard_id, page_id, widget_id FROM ops_perm.role_widget WHERE role_id = $1 "
            "ORDER BY dashboard_id, page_id, widget_id", role_id)
        # Role filter caps live in ops_perm.role_scope (dimension -> values).
        scope_rows = await conn.fetch(
            "SELECT dimension, value FROM ops_perm.role_scope WHERE role_id = $1 "
            "ORDER BY dimension, value", role_id)
        users = await conn.fetch("""
            SELECT u.kc_sub::text, u.username, u.email, u.full_name, u.realm,
                   ur.source, ur.granted_at, ur.is_active
            FROM ops_perm.user_role ur
            JOIN ops_identity.user u ON u.kc_sub = ur.kc_sub
            WHERE ur.role_id = $1
            ORDER BY u.username
        """, role_id)
    return {
        **dict(role),
        "capabilities":   [r["capability_code"] for r in caps],
        "dashboards":     [r["dashboard_id"] for r in dashboards],
        "widgets":        [dict(r) for r in widgets],
        # Role-level data-scope caps (dimension -> values). global_filters and
        # widget_filters are returned empty for SPA compatibility.
        "scope": {
            dim: sorted(str(r["value"]) for r in scope_rows if r["dimension"] == dim)
            for dim in sorted({r["dimension"] for r in scope_rows})
        },
        "global_filters": [],
        "widget_filters": [],
        "users": [
            {**dict(u), "granted_at": u["granted_at"].isoformat() if u.get("granted_at") else None}
            for u in users
        ],
    }


@router.post("/roles", status_code=201)
async def create_role(body: RoleIn, user: UserCtx = Depends(get_user)) -> dict:
    await _require_capability(user, "edit_roles")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rev = await _new_revision(conn, user.kc_sub, f"create role {body.code}")
            try:
                row = await conn.fetchrow("""
                    INSERT INTO ops_perm.role (code, name, description, combine_strategy)
                    VALUES ($1, $2, $3, $4) RETURNING id, code, name
                """, body.code, body.name, body.description, body.combine_strategy)
            except Exception as e:
                raise HTTPException(409, f"role create failed: {e}")
            if body.capabilities:
                await conn.executemany("""
                    INSERT INTO ops_perm.role_capability (role_id, capability_code)
                    VALUES ($1, $2)
                """, [(row["id"], c) for c in body.capabilities])
            await _audit(conn, actor_sub=user.kc_sub, revision_id=rev,
                         table_name="role", op="insert",
                         pk={"id": row["id"]},
                         after={"code": body.code, "name": body.name,
                                "capabilities": body.capabilities})
    log.info("role created: id=%s code=%s by=%s", row["id"], body.code, user.username)
    return {"id": row["id"], "code": row["code"], "name": row["name"]}


@router.put("/roles/{role_id}")
async def update_role(role_id: int, body: RoleIn,
                       user: UserCtx = Depends(get_user)) -> dict:
    await _require_capability(user, "edit_roles")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            before = await conn.fetchrow(
                "SELECT code, name, description, combine_strategy "
                "FROM ops_perm.role WHERE id = $1", role_id)
            if not before:
                raise HTTPException(404, "role not found")
            rev = await _new_revision(conn, user.kc_sub, f"update role {body.code}")
            await conn.execute("""
                UPDATE ops_perm.role
                SET code = $2, name = $3, description = $4,
                    combine_strategy = $5, updated_at = now()
                WHERE id = $1
            """, role_id, body.code, body.name, body.description,
                body.combine_strategy)
            # Replace capabilities atomically.
            await conn.execute(
                "DELETE FROM ops_perm.role_capability WHERE role_id = $1", role_id)
            if body.capabilities:
                await conn.executemany("""
                    INSERT INTO ops_perm.role_capability (role_id, capability_code)
                    VALUES ($1, $2)
                """, [(role_id, c) for c in body.capabilities])
            await _audit(conn, actor_sub=user.kc_sub, revision_id=rev,
                         table_name="role", op="update",
                         pk={"id": role_id},
                         before=dict(before),
                         after={"code": body.code, "name": body.name,
                                "capabilities": body.capabilities})
    return {"ok": True, "id": role_id}


@router.delete("/roles/{role_id}")
async def delete_role(role_id: int, user: UserCtx = Depends(get_user)) -> dict:
    await _require_capability(user, "edit_roles")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            before = await conn.fetchrow(
                "SELECT code, name FROM ops_perm.role WHERE id = $1", role_id)
            if not before:
                raise HTTPException(404, "role not found")
            rev = await _new_revision(conn, user.kc_sub, f"delete role {before['code']}")
            # CASCADE removes the grant tables, user_role rows and capabilities.
            await conn.execute("DELETE FROM ops_perm.role WHERE id = $1", role_id)
            await _audit(conn, actor_sub=user.kc_sub, revision_id=rev,
                         table_name="role", op="delete",
                         pk={"id": role_id}, before=dict(before))
    return {"ok": True}


# Reusable data-access permission store (ops_perm.permission).
# A permission is one OPA allow-rule tuple, reusable across roles via
# ops_perm.role_permission. compile.py --from-db generates the Rego from these
# rows, so an edit here becomes policy once the bundle is recompiled.
class PermissionIn(BaseModel):
    subject: str
    granularity: str
    verbs: list[str]
    members: str = "scoped"
    purposes: list[str] = Field(default_factory=lambda: ["public-health"])
    data_classes: list[str] = Field(default_factory=list)
    sensitivity: list[str] = Field(default_factory=list)
    code: Optional[str] = None
    description: Optional[str] = None

    def _cols(self) -> dict:
        return {"code": self.code, "subject": self.subject, "members": self.members,
                "granularity": self.granularity, "verbs": self.verbs,
                "purposes": self.purposes, "data_classes": self.data_classes,
                "sensitivity": self.sensitivity, "description": self.description}


@router.get("/permissions")
async def list_permissions(user: UserCtx = Depends(get_user)) -> list[dict]:
    await _require_capability(user, "manage_users")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT p.id, p.code, p.subject, p.members, p.granularity, p.verbs,
                   p.purposes, p.data_classes, p.sensitivity, p.description,
                   coalesce(array_agg(r.code ORDER BY r.code)
                            FILTER (WHERE r.code IS NOT NULL), '{}') AS roles
              FROM ops_perm.permission p
              LEFT JOIN ops_perm.role_permission rp ON rp.permission_id = p.id
              LEFT JOIN ops_perm.role r ON r.id = rp.role_id
             GROUP BY p.id
             ORDER BY p.subject, p.granularity, p.id
        """)
    return [dict(r) for r in rows]


@router.post("/permissions", status_code=201)
async def create_permission(body: PermissionIn,
                            user: UserCtx = Depends(get_user)) -> dict:
    await _require_capability(user, "edit_roles")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rev = await _new_revision(
                conn, user.kc_sub, f"create permission {body.subject}/{body.granularity}")
            try:
                row = await conn.fetchrow("""
                    INSERT INTO ops_perm.permission
                        (code, subject, members, granularity, verbs, purposes,
                         data_classes, sensitivity, description)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id
                """, body.code, body.subject, body.members, body.granularity,
                    body.verbs, body.purposes, body.data_classes,
                    body.sensitivity, body.description)
            except Exception as e:                                     # noqa: BLE001
                raise HTTPException(409, f"permission create failed (duplicate tuple / bad value?): {e}")
            await _audit(conn, actor_sub=user.kc_sub, revision_id=rev,
                         table_name="permission", op="insert",
                         pk={"id": row["id"]}, after=body._cols())
    return {"id": row["id"]}


@router.put("/permissions/{perm_id}")
async def update_permission(perm_id: int, body: PermissionIn,
                            user: UserCtx = Depends(get_user)) -> dict:
    await _require_capability(user, "edit_roles")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            before = await conn.fetchrow(
                "SELECT * FROM ops_perm.permission WHERE id = $1", perm_id)
            if not before:
                raise HTTPException(404, "permission not found")
            rev = await _new_revision(conn, user.kc_sub, f"update permission {perm_id}")
            await conn.execute("""
                UPDATE ops_perm.permission
                SET code=$2, subject=$3, members=$4, granularity=$5, verbs=$6,
                    purposes=$7, data_classes=$8, sensitivity=$9, description=$10,
                    updated_at=now()
                WHERE id=$1
            """, perm_id, body.code, body.subject, body.members, body.granularity,
                body.verbs, body.purposes, body.data_classes, body.sensitivity,
                body.description)
            await _audit(conn, actor_sub=user.kc_sub, revision_id=rev,
                         table_name="permission", op="update", pk={"id": perm_id},
                         before={k: before[k] for k in ("subject","granularity","verbs")},
                         after=body._cols())
    return {"ok": True, "id": perm_id}


@router.delete("/permissions/{perm_id}")
async def delete_permission(perm_id: int,
                            user: UserCtx = Depends(get_user)) -> dict:
    await _require_capability(user, "edit_roles")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            before = await conn.fetchrow(
                "SELECT subject, granularity FROM ops_perm.permission WHERE id=$1", perm_id)
            if not before:
                raise HTTPException(404, "permission not found")
            rev = await _new_revision(conn, user.kc_sub, f"delete permission {perm_id}")
            # CASCADE removes the role_permission links.
            await conn.execute("DELETE FROM ops_perm.permission WHERE id=$1", perm_id)
            await _audit(conn, actor_sub=user.kc_sub, revision_id=rev,
                         table_name="permission", op="delete",
                         pk={"id": perm_id}, before=dict(before))
    return {"ok": True}


class RolePermissionsIn(BaseModel):
    permission_ids: list[int]


@router.put("/roles/{role_id}/permissions")
async def replace_role_permissions(role_id: int, body: RolePermissionsIn,
                                   user: UserCtx = Depends(get_user)) -> dict:
    """Replace a role's runtime permission grants (managed_by='runtime'). Seed
    links (managed_by='seed', from the role YAMLs) are left intact; runtime
    grants add to the authored set and survive a re-seed."""
    await _require_capability(user, "edit_roles")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            role = await conn.fetchrow("SELECT code FROM ops_perm.role WHERE id=$1", role_id)
            if not role:
                raise HTTPException(404, "role not found")
            rev = await _new_revision(
                conn, user.kc_sub, f"set runtime permissions for {role['code']}")
            await conn.execute(
                "DELETE FROM ops_perm.role_permission "
                "WHERE role_id=$1 AND managed_by='runtime'", role_id)
            if body.permission_ids:
                await conn.executemany("""
                    INSERT INTO ops_perm.role_permission (role_id, permission_id, ordering, managed_by)
                    VALUES ($1, $2, 1000, 'runtime')
                    ON CONFLICT (role_id, permission_id) DO NOTHING
                """, [(role_id, pid) for pid in body.permission_ids])
            await _audit(conn, actor_sub=user.kc_sub, revision_id=rev,
                         table_name="role_permission", op="update",
                         pk={"role_id": role_id},
                         after={"runtime_permission_ids": body.permission_ids})
    return {"ok": True, "role_id": role_id}


# Role grants: the four axes in one atomic PUT
class GrantsIn(BaseModel):
    dashboards: list[str] = []
    widgets: list[dict[str, str]] = []          # [{dashboard_id, widget_id}, ...]
    # Role data-scope caps, {dimension: [values]}; None leaves the role's scope
    # unchanged. Written to ops_perm.role_scope.
    scope: dict[str, list[str]] | None = None
    global_filters: list[dict[str, Any]] = []   # deprecated: accepted, ignored
    widget_filters: list[dict[str, Any]] = []   # deprecated: accepted, ignored


@router.put("/roles/{role_id}/grants")
async def replace_grants(role_id: int, body: GrantsIn,
                          user: UserCtx = Depends(get_user)) -> dict:
    """Atomically replace all grant axes for a role.

    Backs the RolesAdmin "Save" button: the SPA sends the full intended state,
    which replaces the current one. Each axis is a DELETE and INSERT inside one
    transaction, and the audit log records before and after as one revision."""
    await _require_capability(user, "edit_roles")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            role = await conn.fetchrow("SELECT code FROM ops_perm.role WHERE id = $1", role_id)
            if not role:
                raise HTTPException(404, "role not found")
            rev = await _new_revision(conn, user.kc_sub,
                                       f"replace grants for {role['code']}")
            counts = {}

            # Page-widget placement catalog, used to expand widgets given without a
            # page_id across all of the widget's pages.
            _pw = await conn.fetch("SELECT page_id, widget_id FROM ops_perm.page_widget")
            _widget_pages: dict[str, list[str]] = {}
            for _r in _pw:
                _widget_pages.setdefault(_r["widget_id"], []).append(_r["page_id"])
            def _pages_for(did: str, wid: str, explicit: str | None) -> list[str]:
                if explicit:
                    return [explicit]
                return [pg for pg in _widget_pages.get(wid, []) if pg.split("/", 1)[0] == did]

            # Axis A: dashboards
            await conn.execute("DELETE FROM ops_perm.role_dashboard WHERE role_id = $1",
                                role_id)
            if body.dashboards:
                await conn.executemany("""
                    INSERT INTO ops_perm.role_dashboard (role_id, dashboard_id)
                    VALUES ($1, $2)
                """, [(role_id, d) for d in body.dashboards])
            counts["dashboards"] = len(body.dashboards)

            # Axis C: widgets, page-scoped. Each {dashboard_id, widget_id[, page_id]}
            # expands across the widget's catalogued pages when page_id is omitted.
            await conn.execute("DELETE FROM ops_perm.role_widget WHERE role_id = $1",
                                role_id)
            rw_rows = [(role_id, w["dashboard_id"], pg, w["widget_id"])
                       for w in body.widgets
                       for pg in _pages_for(w["dashboard_id"], w["widget_id"], w.get("page_id"))]
            if rw_rows:
                await conn.executemany("""
                    INSERT INTO ops_perm.role_widget (role_id, dashboard_id, page_id, widget_id)
                    VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING
                """, rw_rows)
            counts["widgets"] = len(rw_rows)

            # Role-level data scope in ops_perm.role_scope (dimension -> values).
            # When `scope` is sent, this role's runtime scope rows are replaced;
            # seed rows belong to the loader and are left intact.
            # `global_filters` and `widget_filters` are accepted and ignored.
            scope = getattr(body, "scope", None)
            if scope is not None:
                await conn.execute(
                    "DELETE FROM ops_perm.role_scope WHERE role_id = $1 AND managed_by = 'runtime'",
                    role_id)
                rs_rows = [(role_id, dim, str(v)) for dim, vals in scope.items()
                           for v in (vals or [])]
                if rs_rows:
                    await conn.executemany("""
                        INSERT INTO ops_perm.role_scope (role_id, dimension, value, managed_by)
                        VALUES ($1, $2, $3, 'runtime')
                        ON CONFLICT (role_id, dimension, value) DO UPDATE SET managed_by = 'runtime'
                    """, rs_rows)
                counts["scope"] = len(rs_rows)

            await _audit(conn, actor_sub=user.kc_sub, revision_id=rev,
                         table_name="role_grants", op="update",
                         pk={"role_id": role_id},
                         after={"counts": counts, "grants": body.model_dump()})
    log.info("grants replaced: role_id=%s by=%s counts=%s",
             role_id, user.username, counts)
    return {"ok": True, "role_id": role_id, "counts": counts,
            "revision_id": rev}


# User-role assignments
class UserRoleIn(BaseModel):
    kc_sub: str
    role_id: int


@router.post("/user-roles", status_code=201)
async def grant_user_role(body: UserRoleIn,
                           user: UserCtx = Depends(get_user)) -> dict:
    await _require_capability(user, "manage_users")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rev = await _new_revision(conn, user.kc_sub,
                                       f"grant role {body.role_id} to {body.kc_sub}")
            await ops_db.grant_user_role(conn, body.kc_sub, body.role_id,
                                         granted_by=user.kc_sub)
            await _audit(conn, actor_sub=user.kc_sub, revision_id=rev,
                         table_name="user_role", op="insert",
                         pk={"kc_sub": body.kc_sub, "role_id": body.role_id})
    return {"ok": True}


@router.delete("/user-roles")
async def revoke_user_role(kc_sub: str = Query(...), role_id: int = Query(...),
                            user: UserCtx = Depends(get_user)) -> dict:
    await _require_capability(user, "manage_users")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rev = await _new_revision(conn, user.kc_sub,
                                       f"revoke role {role_id} from {kc_sub}")
            res = await conn.execute("""
                UPDATE ops_perm.user_role SET is_active = false
                WHERE kc_sub = $1::uuid AND role_id = $2
            """, kc_sub, role_id)
            await _audit(conn, actor_sub=user.kc_sub, revision_id=rev,
                         table_name="user_role", op="update",
                         pk={"kc_sub": kc_sub, "role_id": role_id},
                         after={"is_active": False})


# user_scope editor (per-user data narrowing)
class UserScopeIn(BaseModel):
    kc_sub: str
    # {kind: [values]}, kind one of geography, programme, facility, artifact_tier, workshop_group.
    # Empty dict clears the user's scope (unrestricted). Full-replace semantics.
    scope: dict[str, list[str]] = {}


@router.get("/user-scope/{kc_sub}")
async def get_user_scope(kc_sub: str, user: UserCtx = Depends(get_user)) -> dict:
    """A user's current data scope {kind: [values]}, for the admin per-user
    scope editor."""
    await _require_capability(user, "manage_users")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT kind, value FROM ops_identity.user_scope WHERE kc_sub = $1::uuid "
            "ORDER BY kind, value", kc_sub)
    scope: dict[str, list[str]] = {}
    for r in rows:
        scope.setdefault(r["kind"], []).append(r["value"])
    return {"kc_sub": kc_sub, "scope": scope}


@router.put("/user-scope")
async def replace_user_scope(body: UserScopeIn,
                              user: UserCtx = Depends(get_user)) -> dict:
    """Replace a user's data scope (ops_identity.user_scope), the per-user
    narrowing OPA row-filters on. Full-state replace, like replace_grants.

    Rows are written with note='admin_grant' and the actor as granted_by. A
    later re-seed re-applies a seeded user's YAML scope."""
    await _require_capability(user, "manage_users")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchrow(
                    "SELECT 1 FROM ops_identity.user WHERE kc_sub = $1::uuid", body.kc_sub):
                raise HTTPException(404, "user not found")
            rev = await _new_revision(conn, user.kc_sub,
                                       f"replace data scope for {body.kc_sub}")
            await conn.execute(
                "DELETE FROM ops_identity.user_scope WHERE kc_sub = $1::uuid", body.kc_sub)
            rows = [(body.kc_sub, kind, str(v), user.kc_sub)
                    for kind, vals in body.scope.items() for v in (vals or [])]
            if rows:
                await conn.executemany("""
                    INSERT INTO ops_identity.user_scope
                        (kc_sub, kind, value, granted_by, note)
                    VALUES ($1::uuid, $2, $3, NULLIF($4,'')::uuid, 'admin_grant')
                    ON CONFLICT (kc_sub, kind, value) DO UPDATE SET note = 'admin_grant'
                """, rows)
            await _audit(conn, actor_sub=user.kc_sub, revision_id=rev,
                         table_name="user_scope", op="update",
                         pk={"kc_sub": body.kc_sub}, after={"scope": body.scope})
    return {"ok": True, "kc_sub": body.kc_sub, "count": len(rows)}


# catalog_override CRUD (Dashboard Control)
_OVERRIDE_KINDS = ("dashboard", "page", "section", "widget", "placement")


class CatalogOverrideIn(BaseModel):
    kind: str                                   # dashboard|page|section|widget|placement
    scope_id: str                               # entity id; placement: '<page_id>::<widget_id>'
    enabled: bool = True
    overrides: dict[str, Any] | None = None     # placement: {col_span,row_span,ordering,section_id}
    note: str | None = None


@router.get("/catalog-overrides")
async def list_catalog_overrides(user: UserCtx = Depends(get_user)) -> list[dict]:
    await _require_capability(user, "manage_catalog")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT co.kind, co.scope_id, co.enabled, co.overrides, co.note,
                   co.updated_at, u.username AS updated_by_username
            FROM ops_perm.catalog_override co
            LEFT JOIN ops_identity.user u ON u.kc_sub = co.updated_by
            ORDER BY co.kind, co.scope_id
        """)
    return [{**dict(r),
             "overrides": permissions._decode_jsonb(r["overrides"]),
             "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None}
            for r in rows]


@router.put("/catalog-override")
async def upsert_catalog_override(body: CatalogOverrideIn,
                                   user: UserCtx = Depends(get_user)) -> dict:
    """Set a runtime catalog override: enable or disable a dashboard, page,
    section, widget or placement, or adjust a placement's layout. Subtract-only:
    grants are still checked first in the resolver, and enabled=true only
    removes a prior disable, never widens access. Survives re-seed (the loader
    never writes this table)."""
    await _require_capability(user, "manage_catalog")
    if body.kind not in _OVERRIDE_KINDS:
        raise HTTPException(422, f"kind must be one of {_OVERRIDE_KINDS}")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rev = await _new_revision(conn, user.kc_sub,
                                       f"catalog override {body.kind}:{body.scope_id} "
                                       f"enabled={body.enabled}")
            await conn.execute("""
                INSERT INTO ops_perm.catalog_override
                    (kind, scope_id, enabled, overrides, note, updated_by, updated_at)
                VALUES ($1, $2, $3, $4::jsonb, $5, NULLIF($6,'')::uuid, now())
                ON CONFLICT (kind, scope_id) DO UPDATE
                  SET enabled = EXCLUDED.enabled, overrides = EXCLUDED.overrides,
                      note = EXCLUDED.note, updated_by = EXCLUDED.updated_by,
                      updated_at = now()
            """, body.kind, body.scope_id, body.enabled,
                 json.dumps(body.overrides) if body.overrides is not None else None,
                 body.note, user.kc_sub)
            await _audit(conn, actor_sub=user.kc_sub, revision_id=rev,
                         table_name="catalog_override", op="update",
                         pk={"kind": body.kind, "scope_id": body.scope_id},
                         after=body.model_dump())
    return {"ok": True}


@router.delete("/catalog-override")
async def delete_catalog_override(kind: str = Query(...), scope_id: str = Query(...),
                                   user: UserCtx = Depends(get_user)) -> dict:
    await _require_capability(user, "manage_catalog")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rev = await _new_revision(conn, user.kc_sub,
                                       f"remove catalog override {kind}:{scope_id}")
            await conn.execute(
                "DELETE FROM ops_perm.catalog_override WHERE kind = $1 AND scope_id = $2",
                kind, scope_id)
            await _audit(conn, actor_sub=user.kc_sub, revision_id=rev,
                         table_name="catalog_override", op="delete",
                         pk={"kind": kind, "scope_id": scope_id})
    return {"ok": True}


# Catalog tree (Dashboard Control read model)
@router.get("/catalog-tree")
async def catalog_tree(user: UserCtx = Depends(get_user)) -> dict:
    """The full catalog (dashboards, pages, sections, widgets and placements)
    with each node's override and orphan state, for the Dashboard Control page.
    This is the catalog structure, not the caller's granted spec."""
    await _require_capability(user, "manage_catalog")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        dashes = await conn.fetch("SELECT id, title FROM ops_perm.dashboard ORDER BY id")
        pages = await conn.fetch(
            "SELECT id, dashboard_id, title, ordering FROM ops_perm.page ORDER BY dashboard_id, ordering, id")
        secs = await conn.fetch(
            "SELECT id, page_id, title, ordering FROM ops_perm.section ORDER BY page_id, ordering, id")
        pw = await conn.fetch("""
            SELECT pw.page_id, pw.widget_id, pw.section_id, pw.ordering,
                   pw.col_span, pw.row_span, w.title, w.kind, w.orphaned_at
            FROM ops_perm.page_widget pw JOIN ops_perm.widget w ON w.id = pw.widget_id
            ORDER BY pw.page_id, pw.ordering, pw.widget_id
        """)
        ovs = await conn.fetch(
            "SELECT kind, scope_id, enabled, overrides, note FROM ops_perm.catalog_override")
    ov = {(r["kind"], r["scope_id"]): {"enabled": r["enabled"],
          "overrides": permissions._decode_jsonb(r["overrides"]), "note": r["note"]}
          for r in ovs}
    def st(kind, sid):
        rec = ov.get((kind, sid))
        return {"enabled": rec["enabled"] if rec else True,
                "override": rec or None}
    out = []
    for d in dashes:
        dp = []
        for p in [x for x in pages if x["dashboard_id"] == d["id"]]:
            psecs = [{"id": s["id"], "title": s["title"], "ordering": s["ordering"],
                      **st("section", s["id"]),
                      "widgets": [{"id": r["widget_id"], "title": r["title"], "kind": r["kind"],
                                   "col_span": r["col_span"], "row_span": r["row_span"],
                                   "ordering": r["ordering"],
                                   "orphaned": r["orphaned_at"] is not None,
                                   "widget_enabled": st("widget", r["widget_id"])["enabled"],
                                   **{"placement_enabled":
                                      st("placement", f"{p['id']}::{r['widget_id']}")["enabled"]}}
                                  for r in pw if r["page_id"] == p["id"] and r["section_id"] == s["id"]]}
                     for s in secs if s["page_id"] == p["id"]]
            default_ws = [{"id": r["widget_id"], "title": r["title"], "kind": r["kind"],
                           "col_span": r["col_span"], "row_span": r["row_span"],
                           "ordering": r["ordering"], "orphaned": r["orphaned_at"] is not None,
                           "widget_enabled": st("widget", r["widget_id"])["enabled"],
                           "placement_enabled": st("placement", f"{p['id']}::{r['widget_id']}")["enabled"]}
                          for r in pw if r["page_id"] == p["id"] and r["section_id"] is None]
            dp.append({"id": p["id"], "title": p["title"], "ordering": p["ordering"],
                       **st("page", p["id"]), "sections": psecs, "default_widgets": default_ws})
        out.append({"id": d["id"], "title": d["title"], **st("dashboard", d["id"]), "pages": dp})
    return {"dashboards": out}


# Catalog orphans (soft-orphaned widgets and explicit purge)
@router.get("/catalog-orphans")
async def list_catalog_orphans(user: UserCtx = Depends(get_user)) -> list[dict]:
    """Core widgets soft-orphaned by the reconciler (no longer in the authored
    catalog). Hidden from every spec; grants and placements are kept until
    purged. Re-adding the widget to the catalog restores it."""
    await _require_capability(user, "manage_catalog")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT w.id, w.title, w.kind, w.orphaned_at,
                   (SELECT count(*) FROM ops_perm.role_widget rw WHERE rw.widget_id = w.id)  AS grant_count,
                   (SELECT count(*) FROM ops_perm.page_widget pw WHERE pw.widget_id = w.id)  AS placement_count
            FROM ops_perm.widget w
            WHERE w.orphaned_at IS NOT NULL
            ORDER BY w.orphaned_at DESC, w.id
        """)
    return [{**dict(r), "orphaned_at": r["orphaned_at"].isoformat()} for r in rows]


@router.delete("/catalog-orphans/{widget_id}")
async def purge_catalog_orphan(widget_id: str,
                                user: UserCtx = Depends(get_user)) -> dict:
    """Hard-delete one soft-orphaned widget, cascading to its placements and
    grants. Refuses to purge a widget that is not orphaned."""
    await _require_capability(user, "manage_catalog")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT orphaned_at FROM ops_perm.widget WHERE id = $1", widget_id)
            if not row:
                raise HTTPException(404, "widget not found")
            if row["orphaned_at"] is None:
                raise HTTPException(409, "widget is live (not orphaned); refuse to purge")
            rev = await _new_revision(conn, user.kc_sub, f"purge orphaned widget {widget_id}")
            await conn.execute("DELETE FROM ops_perm.widget WHERE id = $1", widget_id)
            await _audit(conn, actor_sub=user.kc_sub, revision_id=rev,
                         table_name="widget", op="delete", pk={"id": widget_id},
                         before={"orphaned_at": row["orphaned_at"].isoformat()})
    return {"ok": True, "purged": widget_id}


# identity_source CRUD
class IdentitySourceIn(BaseModel):
    code:           str
    name:           str
    kind:           str                                       # direct | oidc | saml | social | fayda
    protocol:       Optional[str] = None
    status:         str = "available"
    config_json:    Optional[dict[str, Any]] = None
    keycloak_alias: Optional[str] = None


@router.get("/identity-sources")
async def list_identity_sources(user: UserCtx = Depends(get_user)) -> list[dict]:
    await _require_capability(user, "manage_identity_sources")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, code, name, kind, protocol, status, config_json,
                   keycloak_alias, created_at, updated_at
            FROM ops_identity.identity_source ORDER BY id
        """)
    return [_decode_idp(r) for r in rows]


@router.post("/identity-sources", status_code=201)
async def create_identity_source(body: IdentitySourceIn,
                                  user: UserCtx = Depends(get_user)) -> dict:
    await _require_capability(user, "manage_identity_sources")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rev = await _new_revision(conn, user.kc_sub,
                                       f"add identity source {body.code}")
            try:
                row = await conn.fetchrow("""
                    INSERT INTO ops_identity.identity_source
                        (code, name, kind, protocol, status, config_json, keycloak_alias)
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                    RETURNING id, code
                """, body.code, body.name, body.kind, body.protocol, body.status,
                    json.dumps(body.config_json) if body.config_json else None,
                    body.keycloak_alias)
            except Exception as e:
                raise HTTPException(409, f"create failed: {e}")
            await _audit(conn, actor_sub=user.kc_sub, revision_id=rev,
                         table_name="identity_source", op="insert",
                         pk={"id": row["id"]}, after=body.model_dump())
    # Creating the IdP in Keycloak is done by the admin SPA's SSO page, which
    # calls the Keycloak Admin API with the operator's privileged-ops token.
    # The gateway only mirrors the metadata into ops_identity.identity_source.
    log.info("identity_source created (DB mirror only): %s", body.code)
    return {"id": row["id"], "code": row["code"],
            "keycloak_sync": "client-managed"}


@router.put("/identity-sources/{source_id}")
async def update_identity_source(source_id: int, body: IdentitySourceIn,
                                  user: UserCtx = Depends(get_user)) -> dict:
    await _require_capability(user, "manage_identity_sources")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            before = await conn.fetchrow(
                "SELECT code, name, kind, status FROM ops_identity.identity_source "
                "WHERE id = $1", source_id)
            if not before:
                raise HTTPException(404, "identity_source not found")
            rev = await _new_revision(conn, user.kc_sub,
                                       f"update identity source {body.code}")
            await conn.execute("""
                UPDATE ops_identity.identity_source
                SET code = $2, name = $3, kind = $4, protocol = $5, status = $6,
                    config_json = $7::jsonb, keycloak_alias = $8, updated_at = now()
                WHERE id = $1
            """, source_id, body.code, body.name, body.kind, body.protocol,
                body.status,
                json.dumps(body.config_json) if body.config_json else None,
                body.keycloak_alias)
            await _audit(conn, actor_sub=user.kc_sub, revision_id=rev,
                         table_name="identity_source", op="update",
                         pk={"id": source_id},
                         before=dict(before), after=body.model_dump())
    return {"ok": True, "id": source_id, "keycloak_sync": "client-managed"}


@router.delete("/identity-sources/{source_id}")
async def delete_identity_source(source_id: int,
                                  user: UserCtx = Depends(get_user)) -> dict:
    await _require_capability(user, "manage_identity_sources")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            before = await conn.fetchrow(
                "SELECT code, name FROM ops_identity.identity_source WHERE id = $1",
                source_id)
            if not before:
                raise HTTPException(404, "identity_source not found")
            rev = await _new_revision(conn, user.kc_sub,
                                       f"delete identity source {before['code']}")
            await conn.execute(
                "DELETE FROM ops_identity.identity_source WHERE id = $1", source_id)
            await _audit(conn, actor_sub=user.kc_sub, revision_id=rev,
                         table_name="identity_source", op="delete",
                         pk={"id": source_id}, before=dict(before))
    return {"ok": True}

# Audit log read
@router.get("/audit")
async def audit_log(limit: int = 50, user: UserCtx = Depends(get_user)) -> list[dict]:
    await _require_capability(user, "manage_users")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT ac.id, ac.ts, ac.actor_sub::text, u.username AS actor_username,
                   ac.revision_id, ac.table_name, ac.op, ac.pk, ac.before, ac.after,
                   pr.note AS revision_note
            FROM ops_perm.audit_permission_change ac
            LEFT JOIN ops_identity.user u ON u.kc_sub = ac.actor_sub
            LEFT JOIN ops_perm.policy_revision pr ON pr.id = ac.revision_id
            ORDER BY ac.ts DESC LIMIT $1
        """, limit)
    out = []
    for r in rows:
        d = dict(r)
        d["ts"] = d["ts"].isoformat() if d.get("ts") else None
        d["pk"] = permissions._decode_jsonb(d.get("pk"))
        d["before"] = permissions._decode_jsonb(d.get("before"))
        d["after"] = permissions._decode_jsonb(d.get("after"))
        out.append(d)
    return out
