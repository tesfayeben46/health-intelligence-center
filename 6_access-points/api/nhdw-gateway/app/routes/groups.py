"""/api/groups: workshop community groups.

Every authenticated caller can list groups and view a roster. Contributors can
create a group and become its owner. Membership is gated: a user joins only by
accepting an owner's invite or by having a join request approved by an owner.
The `shared` artifact tier references group ids from here, so this is the
canonical catalogue.

  GET  /api/groups                          groups with member and recent-artifact counts
  GET  /api/groups/{id}                     members and recent artifacts shared with the group
  POST /api/groups                          create (contributor roles), 201
  POST /api/groups/{id}/request-join        ask to join
  POST /api/groups/{id}/invite              owner invites a user
  GET  /api/groups/invites/received         invites sent to me
  GET  /api/groups/requests/incoming        join requests to groups I own
  GET  /api/groups/requests/mine            my join requests and sent invites
  POST /api/groups/invites/{id}/accept|decline|revoke
  POST /api/groups/requests/{id}/approve|decline
  POST /api/groups/{id}/leave               leave (owners must transfer first)
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ..auth import UserCtx, get_user
from .. import ops_db
from .graduation import CONTRIBUTOR_ROLES

log = logging.getLogger("gateway.groups")
router = APIRouter(prefix="/api/groups", tags=["groups"])


@router.get("")
async def list_groups(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                g.id, g.name, g.description, g.visibility, g.created_at,
                (SELECT COUNT(*) FROM ops_artifact.workshop_group_member m
                   WHERE m.group_id = g.id)                         AS member_count,
                (SELECT COUNT(*) FROM ops_artifact.artifact a
                   WHERE a.workshop_group = g.id AND a.tier = 'shared') AS artifact_count,
                (SELECT MAX(a.updated_at) FROM ops_artifact.artifact a
                   WHERE a.workshop_group = g.id)                   AS last_activity_at,
                EXISTS (SELECT 1 FROM ops_artifact.workshop_group_member m
                          WHERE m.group_id = g.id AND m.kc_sub = $1::uuid) AS i_am_a_member,
                (g.owner = $1::uuid
                 OR EXISTS (SELECT 1 FROM ops_artifact.workshop_group_member m
                             WHERE m.group_id = g.id AND m.kc_sub = $1::uuid AND m.role = 'owner')) AS i_own
            FROM ops_artifact.workshop_group g
            ORDER BY g.name;
        """, user.kc_sub or "00000000-0000-0000-0000-000000000000")
    return {"groups": [dict(r) for r in rows]}


@router.get("/{group_id}")
async def get_group(group_id: str,
                    user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        group = await conn.fetchrow("""
            SELECT g.id, g.name, g.description, g.visibility,
                   g.created_at, u.username AS owner_username
            FROM ops_artifact.workshop_group g
            LEFT JOIN ops_identity.user u ON u.kc_sub = g.owner
            WHERE g.id = $1::uuid;
        """, group_id)
        if group is None:
            raise HTTPException(404, "group not found")
        members = await conn.fetch("""
            SELECT m.kc_sub, m.role, m.joined_at, u.username
            FROM ops_artifact.workshop_group_member m
            LEFT JOIN ops_identity.user u ON u.kc_sub = m.kc_sub
            WHERE m.group_id = $1::uuid
            ORDER BY m.role, m.joined_at;
        """, group_id)
        artifacts = await conn.fetch("""
            SELECT id, kind, title, description, peer_review_status,
                   created_at, updated_at
            FROM ops_artifact.artifact
            WHERE workshop_group = $1::uuid
            ORDER BY updated_at DESC NULLS LAST, created_at DESC
            LIMIT 25;
        """, group_id)
    return {
        "group":     dict(group),
        "members":   [dict(m) for m in members],
        "artifacts": [dict(a) for a in artifacts],
    }


async def _require_identity(conn, user: UserCtx) -> str:
    """Group writes reference ops_identity.user; refuse subjects with no
    identity row with 409 instead of 500."""
    if not user.kc_sub:
        raise HTTPException(401, "no subject in token")
    row = await conn.fetchval(
        "SELECT 1 FROM ops_identity.user WHERE kc_sub = $1::uuid;", user.kc_sub)
    if row is None:
        raise HTTPException(409, "caller has no identity row")
    return user.kc_sub


@router.post("", status_code=201)
async def create_group(
        user: UserCtx = Depends(get_user),
        name: str = Body(..., embed=True, min_length=3, max_length=80),
        description: str | None = Body(default=None, embed=True),
        visibility: str = Body(default="open", embed=True),
        ) -> dict[str, Any]:
    if not any(r in CONTRIBUTOR_ROLES for r in user.roles):
        raise HTTPException(403, "group creation is for workshop contributors")
    if visibility not in ("open", "request", "closed"):
        raise HTTPException(400, "visibility must be open | request | closed")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            kc = await _require_identity(conn, user)
            group = await conn.fetchrow("""
                INSERT INTO ops_artifact.workshop_group
                    (name, description, owner, visibility)
                VALUES ($1, $2, $3::uuid, $4)
                ON CONFLICT (name) DO NOTHING
                RETURNING id, name, description, visibility, created_at;
            """, name.strip(), description, kc, visibility)
            if group is None:
                raise HTTPException(409, "a group with that name already exists")
            await conn.execute("""
                INSERT INTO ops_artifact.workshop_group_member
                    (group_id, kc_sub, role)
                VALUES ($1::uuid, $2::uuid, 'owner');
            """, group["id"], kc)
    await ops_db.log_data_event(
        user=user, kind="group.created",
        payload={"group_id": str(group["id"]), "name": group["name"]})
    return dict(group)


# Invite and request flows. There is no self-serve join: a user becomes a member
# only by accepting an owner's invite or by an owner approving their join request.

async def _assert_group_owner(conn, group_id: str, kc: str) -> None:
    """Only a group owner may invite, approve, decline or revoke. An owner is
    workshop_group.owner (the creator) or a member whose role is 'owner'."""
    ok = await conn.fetchval("""
        SELECT 1 FROM ops_artifact.workshop_group g
        WHERE g.id = $1::uuid AND (
            g.owner = $2::uuid
            OR EXISTS (SELECT 1 FROM ops_artifact.workshop_group_member m
                        WHERE m.group_id = g.id AND m.kc_sub = $2::uuid AND m.role = 'owner'));
    """, group_id, kc)
    if not ok:
        raise HTTPException(403, "only the group owner can manage its membership")


async def _is_member(conn, group_id: str, kc: str) -> bool:
    return bool(await conn.fetchval("""
        SELECT 1 FROM ops_artifact.workshop_group_member
        WHERE group_id = $1::uuid AND kc_sub = $2::uuid;
    """, group_id, kc))


# Request to join
@router.post("/{group_id}/request-join")
async def request_join(group_id: str,
                       user: UserCtx = Depends(get_user),
                       message: str | None = Body(default=None, embed=True)) -> dict[str, Any]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            kc = await _require_identity(conn, user)
            g = await conn.fetchval("SELECT 1 FROM ops_artifact.workshop_group WHERE id=$1::uuid;", group_id)
            if not g:
                raise HTTPException(404, "group not found")
            if await _is_member(conn, group_id, kc):
                raise HTTPException(409, "you are already a member of this group")
            # If an owner already invited the caller, accept that invite instead.
            row = await conn.fetchrow("""
                INSERT INTO ops_artifact.workshop_group_join_request (group_id, requester, message, status)
                VALUES ($1::uuid, $2::uuid, $3, 'pending')
                ON CONFLICT (group_id, requester) DO UPDATE
                    SET status='pending', message=EXCLUDED.message, created_at=now(), decided_at=NULL
                RETURNING id;
            """, group_id, kc, message)
    await ops_db.log_data_event(user=user, kind="group.join_requested",
                                payload={"group_id": group_id})
    return {"requested": True, "group_id": group_id, "request_id": str(row["id"])}


# Owner invites a user into the group
@router.post("/{group_id}/invite", status_code=201)
async def invite_to_group(group_id: str,
                          user: UserCtx = Depends(get_user),
                          username: str = Body(..., embed=True),
                          message: str | None = Body(default=None, embed=True)) -> dict[str, Any]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            kc = await _require_identity(conn, user)
            await _assert_group_owner(conn, group_id, kc)
            invitee = await conn.fetchval(
                'SELECT kc_sub FROM ops_identity."user" WHERE lower(username)=lower($1);',
                username.strip())
            if invitee is None:
                raise HTTPException(404, f"no user named {username!r}")
            if await _is_member(conn, group_id, str(invitee)):
                raise HTTPException(409, "that user is already a member")
            row = await conn.fetchrow("""
                INSERT INTO ops_artifact.workshop_group_invite (group_id, invitee, invited_by, message, status)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4, 'pending')
                ON CONFLICT (group_id, invitee) DO UPDATE
                    SET status='pending', invited_by=EXCLUDED.invited_by,
                        message=EXCLUDED.message, created_at=now(), decided_at=NULL
                RETURNING id;
            """, group_id, invitee, kc, message)
    await ops_db.log_data_event(user=user, kind="group.invited",
                                payload={"group_id": group_id, "invitee": str(invitee)})
    return {"invited": True, "group_id": group_id, "invite_id": str(row["id"]), "invitee": username}


# Inbox: invitations sent to me and join requests to groups I own
@router.get("/invites/received")
async def invites_received(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT i.id, i.group_id, g.name AS group_name, i.message, i.created_at,
                   u.username AS invited_by
            FROM ops_artifact.workshop_group_invite i
            JOIN ops_artifact.workshop_group g ON g.id = i.group_id
            LEFT JOIN ops_identity."user" u ON u.kc_sub = i.invited_by
            WHERE i.invitee = $1::uuid AND i.status = 'pending'
            ORDER BY i.created_at DESC;
        """, user.kc_sub or "00000000-0000-0000-0000-000000000000")
    return {"invites": [dict(r) for r in rows]}


@router.get("/requests/incoming")
async def requests_incoming(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT r.id, r.group_id, g.name AS group_name, r.message, r.created_at,
                   u.username AS requester
            FROM ops_artifact.workshop_group_join_request r
            JOIN ops_artifact.workshop_group g ON g.id = r.group_id
            LEFT JOIN ops_identity."user" u ON u.kc_sub = r.requester
            WHERE r.status = 'pending' AND (
                g.owner = $1::uuid
                OR EXISTS (SELECT 1 FROM ops_artifact.workshop_group_member m
                            WHERE m.group_id = g.id AND m.kc_sub = $1::uuid AND m.role='owner'))
            ORDER BY r.created_at DESC;
        """, user.kc_sub or "00000000-0000-0000-0000-000000000000")
    return {"requests": [dict(r) for r in rows]}


# Outbox: join requests I sent and invites I sent as an owner
@router.get("/requests/mine")
async def requests_mine(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        reqs = await conn.fetch("""
            SELECT r.id, r.group_id, g.name AS group_name, r.status, r.created_at
            FROM ops_artifact.workshop_group_join_request r
            JOIN ops_artifact.workshop_group g ON g.id = r.group_id
            WHERE r.requester = $1::uuid
            ORDER BY r.created_at DESC;
        """, user.kc_sub or "00000000-0000-0000-0000-000000000000")
        sent = await conn.fetch("""
            SELECT i.id, i.group_id, g.name AS group_name, i.status, i.created_at,
                   u.username AS invitee
            FROM ops_artifact.workshop_group_invite i
            JOIN ops_artifact.workshop_group g ON g.id = i.group_id
            LEFT JOIN ops_identity."user" u ON u.kc_sub = i.invitee
            WHERE i.invited_by = $1::uuid
            ORDER BY i.created_at DESC;
        """, user.kc_sub or "00000000-0000-0000-0000-000000000000")
    return {"requests": [dict(r) for r in reqs], "invites_sent": [dict(r) for r in sent]}


# Decisions
async def _add_member(conn, group_id, kc) -> None:
    await conn.execute("""
        INSERT INTO ops_artifact.workshop_group_member (group_id, kc_sub, role)
        VALUES ($1::uuid, $2::uuid, 'member') ON CONFLICT (group_id, kc_sub) DO NOTHING;
    """, group_id, kc)


@router.post("/invites/{invite_id}/accept")
async def accept_invite(invite_id: str, user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            kc = await _require_identity(conn, user)
            inv = await conn.fetchrow("""
                SELECT group_id, invitee, status FROM ops_artifact.workshop_group_invite
                WHERE id = $1::uuid FOR UPDATE;
            """, invite_id)
            if inv is None or str(inv["invitee"]) != kc:
                raise HTTPException(404, "invite not found")
            if inv["status"] != "pending":
                raise HTTPException(409, f"invite already {inv['status']}")
            await _add_member(conn, inv["group_id"], kc)
            await conn.execute("UPDATE ops_artifact.workshop_group_invite SET status='accepted', decided_at=now() WHERE id=$1::uuid;", invite_id)
    await ops_db.log_data_event(user=user, kind="group.invite_accepted", payload={"invite_id": invite_id})
    return {"accepted": True, "group_id": str(inv["group_id"])}


@router.post("/invites/{invite_id}/decline")
async def decline_invite(invite_id: str, user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        kc = await _require_identity(conn, user)
        res = await conn.execute("""
            UPDATE ops_artifact.workshop_group_invite SET status='declined', decided_at=now()
            WHERE id=$1::uuid AND invitee=$2::uuid AND status='pending';
        """, invite_id, kc)
    if res.endswith("0"):
        raise HTTPException(404, "invite not found or not pending")
    return {"declined": True}


@router.post("/invites/{invite_id}/revoke")
async def revoke_invite(invite_id: str, user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        kc = await _require_identity(conn, user)
        inv = await conn.fetchrow("SELECT group_id, invited_by FROM ops_artifact.workshop_group_invite WHERE id=$1::uuid;", invite_id)
        if inv is None:
            raise HTTPException(404, "invite not found")
        if str(inv["invited_by"]) != kc:
            await _assert_group_owner(conn, str(inv["group_id"]), kc)
        await conn.execute("UPDATE ops_artifact.workshop_group_invite SET status='revoked', decided_at=now() WHERE id=$1::uuid AND status='pending';", invite_id)
    return {"revoked": True}


@router.post("/requests/{request_id}/approve")
async def approve_request(request_id: str, user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            kc = await _require_identity(conn, user)
            req = await conn.fetchrow("""
                SELECT group_id, requester, status FROM ops_artifact.workshop_group_join_request
                WHERE id = $1::uuid FOR UPDATE;
            """, request_id)
            if req is None:
                raise HTTPException(404, "request not found")
            await _assert_group_owner(conn, str(req["group_id"]), kc)
            if req["status"] != "pending":
                raise HTTPException(409, f"request already {req['status']}")
            await _add_member(conn, req["group_id"], req["requester"])
            await conn.execute("UPDATE ops_artifact.workshop_group_join_request SET status='approved', decided_at=now(), decided_by=$2::uuid WHERE id=$1::uuid;", request_id, kc)
    await ops_db.log_data_event(user=user, kind="group.request_approved", payload={"request_id": request_id})
    return {"approved": True}


@router.post("/requests/{request_id}/decline")
async def decline_request(request_id: str, user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        kc = await _require_identity(conn, user)
        req = await conn.fetchrow("SELECT group_id FROM ops_artifact.workshop_group_join_request WHERE id=$1::uuid;", request_id)
        if req is None:
            raise HTTPException(404, "request not found")
        await _assert_group_owner(conn, str(req["group_id"]), kc)
        await conn.execute("UPDATE ops_artifact.workshop_group_join_request SET status='declined', decided_at=now(), decided_by=$2::uuid WHERE id=$1::uuid AND status='pending';", request_id, kc)
    return {"declined": True}


@router.post("/{group_id}/leave")
async def leave_group(group_id: str,
                      user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            kc = await _require_identity(conn, user)
            role = await conn.fetchval("""
                SELECT role FROM ops_artifact.workshop_group_member
                WHERE group_id = $1::uuid AND kc_sub = $2::uuid;
            """, group_id, kc)
            if role is None:
                raise HTTPException(404, "not a member of this group")
            if role == "owner":
                raise HTTPException(
                    409, "the owner cannot leave their own group")
            await conn.execute("""
                DELETE FROM ops_artifact.workshop_group_member
                WHERE group_id = $1::uuid AND kc_sub = $2::uuid;
            """, group_id, kc)
    await ops_db.log_data_event(
        user=user, kind="group.left", payload={"group_id": group_id})
    return {"left": True, "group_id": group_id}
