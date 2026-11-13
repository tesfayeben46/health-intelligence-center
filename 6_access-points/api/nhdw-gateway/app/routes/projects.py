"""/api/projects: the workshop project entity.

A project is a workshop user's unit of work. A single visibility tag controls
sharing:

    private  -> owner only
    group    -> members of group_id (ops_artifact.workshop_group_member)
    public   -> any authenticated user

Creating a project provisions three things in one call:

1. an ops_workspace.project row (owner = caller's kc_sub),
2. an object-store prefix projects/<id>/files/ (a .keep marker; the gateway
   is the only holder of object-store credentials),
3. an MLflow experiment /projects/<id> (best-effort: a registry error is
   logged and never blocks project creation).

Endpoints:
    POST   /api/projects                     create, 201 with the row
        GET    /api/projects                     list projects visible to the caller (?scope=, ?q=)
        GET    /api/projects/{id}                detail, ACL-checked (404 if not visible)
        PATCH  /api/projects/{id}                owner-only update (visibility, group, ...)
        POST   /api/projects/{id}/clone          copy into a new private project
        GET    /api/projects/{id}/files          gateway-proxied object listing
        GET    /api/projects/{id}/download.zip   whole file space as a zip
        POST   /api/projects/{id}/files/folder   create a folder
        POST   /api/projects/{id}/files/upload   upload a file
        POST   /api/projects/{id}/send           copy an item into another project's received/ folder

    An authenticated kc_sub is required, as in queries.py, and every call is
    audit-logged via ops_db. The visibility tag is the authorisation (there is no
    OPA data-plane verb), so the ACL is enforced on every read and write. To
    prevent enumeration, an invisible project returns 404 (never 403) on read;
    PATCH returns 404 when invisible and 403 when visible but not owned.
    Submission for graduation is in routes/graduation.py.
    """
from __future__ import annotations

import logging
import os
import re
import time
import uuid
from typing import Any
from uuid import uuid4

import io
import posixpath
import zipfile

import httpx
from fastapi import (APIRouter, Body, Depends, File, Form, HTTPException,
                     Query, UploadFile)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..auth import UserCtx, get_user
from ..settings import settings
from .. import minio_client, ops_db

log = logging.getLogger("gateway.projects")
router = APIRouter(prefix="/api/projects", tags=["projects"])

MLFLOW_URL      = os.environ.get("MLFLOW_URL",
                                 "http://mlflow.nhdw.svc.cluster.local:5000")
PROJECTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET", settings.ARTIFACTS_BUCKET)


def _files_prefix(pid: str) -> str:
    return f"projects/{pid}/files/"


# Request bodies
class ProjectCreate(BaseModel):
    name:        str        = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    # A group project cannot be created in one call (POST carries no group_id):
    # create it private or public, then PATCH visibility to group with a group_id.
    visibility:  str        = Field(default="private",
                                    pattern="^(private|public)$")


class ProjectPatch(BaseModel):
    # All fields optional (partial update); None means unchanged. group_id is
    # meaningful only with visibility='group'.
    name:        str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    visibility:  str | None = Field(default=None,
                                    pattern="^(private|group|public)$")
    group_id:    str | None = Field(default=None)
    status:      str | None = Field(default=None, pattern="^(active|archived)$")


# Helpers
def _slugify(name: str, pid: str) -> str:
    """Kebab-case name plus a short id from the project's uuid: stable,
    collision-free (the uuid is the primary key) and readable."""
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    base = re.sub(r"-{2,}", "-", base)[:60].strip("-") or "project"
    return f"{base}-{pid.replace('-', '')[:8]}"


def _valid_uuid(s: str) -> bool:
    try:
        uuid.UUID(str(s))
        return True
    except (ValueError, TypeError):
        return False


def _row_out(row: Any) -> dict[str, Any]:
    """Serialise a project record as a JSON-safe dict (uuid as str, timestamps
    as ISO format)."""
    d = dict(row)
    for k in ("id", "owner_kc_sub", "group_id", "submitted_by", "reviewed_by"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    for k in ("created_at", "updated_at", "submitted_at", "reviewed_at"):
        v = d.get(k)
        if v is not None and hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d


# Reviewer visibility: reviewers can also see and open submitted projects, even
# private ones, to review them. Same reviewer set as graduation.py, duplicated so
# projects.py does not import the graduation router.
REVIEWER_ROLES = ("R-OPS-ADM", "R-STEW", "R-SUPER")
# Only submitted projects are reviewer-visible, never an unsubmitted private draft.
_REVIEW_VISIBLE_STATES = "('in_review','changes_requested','approved')"


def is_reviewer(user: UserCtx) -> bool:
    return any(r in REVIEWER_ROLES for r in (user.roles or []))


# Visibility predicate shared by list and single-row fetch; {me} is the caller's
# kc_sub placeholder. With `reviewer` set, submitted projects (review_status in
# in_review, changes_requested, approved) are also visible even if private. This
# is the only widening: a reviewer still cannot see an unsubmitted draft.
def _visible_sql(me: str, *, reviewer: bool = False) -> str:
    clause = (
        "(p.owner_kc_sub = {me}::uuid "
        " OR p.visibility = 'public' "
        " OR (p.visibility = 'group' AND EXISTS ("
        "      SELECT 1 FROM ops_artifact.workshop_group_member m "
        "       WHERE m.group_id = p.group_id AND m.kc_sub = {me}::uuid))"
    ).format(me=me)
    if reviewer:
        clause += f" OR p.review_status IN {_REVIEW_VISIBLE_STATES}"
    return clause + ")"


async def _fetch_visible(conn, pid: str, me: str, *, reviewer: bool = False):
    """One project the caller may see, else None (never another user's private
    draft). `reviewer=True` also exposes a submitted project (in_review,
    changes_requested, approved); used only by the read endpoints (detail,
    files, download), never by the write helpers."""
    return await conn.fetchrow(f"""
        SELECT p.*, u.username AS owner_username
          FROM ops_workspace.project p
          LEFT JOIN ops_identity."user" u ON u.kc_sub = p.owner_kc_sub
         WHERE p.id = $1::uuid AND {_visible_sql('$2', reviewer=reviewer)}
    """, pid, me)


async def _create_experiment(pid: str) -> dict[str, Any] | None:
    """Create the MLflow experiment /projects/<id>. Best-effort: a registry
    error (including already-exists) is logged and swallowed, so project
    creation does not depend on MLflow availability."""
    name = f"/projects/{pid}"
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.post(
                f"{MLFLOW_URL}/api/2.0/mlflow/experiments/create",
                json={"name": name,
                      "tags": [{"key": "project_id", "value": pid},
                               {"key": "nhdw.origin", "value": "gateway"}]})
            if r.status_code == 200:
                return {"experiment_id": (r.json() or {}).get("experiment_id"),
                        "name": name}
            # Already exists (idempotent create): resolve its id.
            if "RESOURCE_ALREADY_EXISTS" in r.text:
                g = await c.get(f"{MLFLOW_URL}/api/2.0/mlflow/experiments/get-by-name",
                                params={"experiment_name": name})
                exp = (g.json() or {}).get("experiment") or {}
                return {"experiment_id": exp.get("experiment_id"), "name": name}
            log.warning("mlflow experiment create %s -> %s %s",
                        name, r.status_code, r.text[:200])
    except Exception as exc:                                    # noqa: BLE001
        log.warning("mlflow experiment create failed for %s: %s", name, exc)
    return None


# Create
@router.post("", status_code=201)
async def create_project(
        user: UserCtx = Depends(get_user),
        body: ProjectCreate = Body(...)) -> dict[str, Any]:
    if not user.kc_sub:
        raise HTTPException(401, "authenticated session required")
    t0 = time.perf_counter()

    # Cache the caller's identity so the owner foreign key resolves (as /api/me does).
    await ops_db.upsert_user(user)

    pid  = str(uuid4())
    slug = _slugify(body.name, pid)

    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow("""
                INSERT INTO ops_workspace.project
                    (id, slug, name, owner_kc_sub, visibility,
                     description, created_at, updated_at)
                VALUES ($1::uuid, $2, $3, $4::uuid, $5, $6, now(), now())
                RETURNING *,
                    (SELECT username FROM ops_identity."user"
                      WHERE kc_sub = $4::uuid) AS owner_username;
            """, pid, slug, body.name, user.kc_sub, body.visibility,
                 body.description)
        except Exception as exc:                                # noqa: BLE001
            log.exception("project insert failed")
            raise HTTPException(500, f"project create failed: {exc}")

    # (2) Object-store prefix projects/<id>/files/: write the .keep marker.
    minio_ok, minio_err = True, None
    try:
        await minio_client.ensure_prefix(prefix=_files_prefix(pid),
                                          bucket=PROJECTS_BUCKET)
    except Exception as exc:                                    # noqa: BLE001
        minio_ok, minio_err = False, str(exc)[:300]
        log.warning("project %s: MinIO prefix create failed: %s", pid, exc)

    # (3) MLflow experiment /projects/<id>, best-effort.
    experiment = await _create_experiment(pid)

    await ops_db.log_data_event(
        user=user, kind="project.created",
        payload={"project_id": pid, "slug": slug, "name": body.name,
                 "visibility": body.visibility,
                 "minio_prefix": _files_prefix(pid), "minio_ok": minio_ok,
                 "mlflow_experiment": experiment})
    await ops_db.log_gateway_query(
        user=user, method="POST", path="/api/projects",
        mart_scope=["ops_workspace.project"],
        filters={"visibility": body.visibility},
        row_count=1, byte_count=None,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        http_status=201, policy_decision="allow")

    out = _row_out(row)
    out["files_prefix"]      = _files_prefix(pid)
    out["mlflow_experiment"] = experiment
    if not minio_ok:
        out["minio_error"] = minio_err
    return out


# Clone
@router.post("/{project_id}/clone", status_code=201)
async def clone_project(project_id: str,
                        user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Copy a project the caller can see, including its files, into a new
    private project the caller owns. This is how a user works on someone else's
    public project without opening a lab on its shared file space: labs sync to
    the project's single prefix, so two users would overwrite each other. Group
    projects share files by design, so members open them directly."""
    if not user.kc_sub:
        raise HTTPException(401, "authenticated session required")
    await ops_db.upsert_user(user)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        src = await _fetch_visible(conn, project_id, user.kc_sub)
        if src is None:
            raise HTTPException(404, "project not found")
        new_pid = str(uuid4())
        new_name = f"Copy of {src['name']}"[:120]
        slug = _slugify(new_name, new_pid)
        row = await conn.fetchrow("""
            INSERT INTO ops_workspace.project
                (id, slug, name, owner_kc_sub, visibility, description, created_at, updated_at)
            VALUES ($1::uuid, $2, $3, $4::uuid, 'private', $5, now(), now())
            RETURNING *,
                (SELECT username FROM ops_identity."user" WHERE kc_sub = $4::uuid) AS owner_username;
        """, new_pid, slug, new_name, user.kc_sub, src["description"])

    # Copy the source's files/ tree into the clone's prefix, best-effort.
    src_prefix, dst_prefix = _files_prefix(project_id), _files_prefix(new_pid)
    copied = 0
    try:
        objs = await minio_client.list_prefix(prefix=src_prefix, bucket=PROJECTS_BUCKET)
        for o in objs:
            k = o["key"]
            if k == src_prefix:
                continue
            await minio_client.copy_object(src_key=k, dst_key=dst_prefix + k[len(src_prefix):],
                                           bucket=PROJECTS_BUCKET, src_bucket=PROJECTS_BUCKET)
            copied += 1
        if not copied:
            await minio_client.ensure_prefix(prefix=dst_prefix, bucket=PROJECTS_BUCKET)
    except Exception as exc:                                        # noqa: BLE001
        log.warning("clone %s->%s: MinIO copy failed: %s", project_id, new_pid, exc)

    await _create_experiment(new_pid)
    await ops_db.log_data_event(user=user, kind="project.cloned",
                                payload={"source": project_id, "project_id": new_pid, "files": copied})
    out = _row_out(row)
    out["files_prefix"] = dst_prefix
    out["cloned_from"] = project_id
    out["files_copied"] = copied
    return out


# List
@router.get("")
async def list_projects(
        user: UserCtx = Depends(get_user),
        scope: str = Query(default="all",
                           pattern="^(mine|group|public|review|all)$"),
        q: str | None = Query(default=None, max_length=200),
        limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    if not user.kc_sub:
        raise HTTPException(401, "authenticated session required")
    t0 = time.perf_counter()
    me = user.kc_sub
    reviewer = is_reviewer(user)

    # Reviewers get the widened visibility clause (submitted private projects
    # too); scope='review' narrows further to the actionable queue (in_review,
    # changes_requested). A non-reviewer asking for scope='review' gets only
    # their own submitted projects, because their visibility is not widened.
    where = [_visible_sql("$1", reviewer=reviewer)]
    args: list[Any] = [me]
    if scope == "mine":
        where.append("p.owner_kc_sub = $1::uuid")
    elif scope == "public":
        where.append("p.visibility = 'public'")
    elif scope == "group":
        where.append("p.visibility = 'group'")
    elif scope == "review":
        where.append("p.review_status IN ('in_review','changes_requested')")
    if q:
        args.append(q)
        where.append(f"p.name ILIKE '%' || ${len(args)} || '%'")
    args.append(limit)

    sql = (f"""SELECT p.*, u.username AS owner_username
                 FROM ops_workspace.project p
                 LEFT JOIN ops_identity."user" u ON u.kc_sub = p.owner_kc_sub
                WHERE {' AND '.join(where)}
                ORDER BY p.updated_at DESC NULLS LAST, p.created_at DESC
                LIMIT ${len(args)}""")

    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    out = [_row_out(r) for r in rows]
    await ops_db.log_gateway_query(
        user=user, method="GET", path="/api/projects",
        mart_scope=["ops_workspace.project"],
        filters={"scope": scope, "q": q},
        row_count=len(out), byte_count=None,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        http_status=200, policy_decision="allow")
    return {"projects": out, "count": len(out), "scope": scope}


# Detail
@router.get("/{project_id}")
async def project_detail(project_id: str,
                         user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    if not user.kc_sub:
        raise HTTPException(401, "authenticated session required")
    if not _valid_uuid(project_id):
        raise HTTPException(404, "not found")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        # Reviewers may also read a submitted project, even a private one.
        row = await _fetch_visible(conn, project_id, user.kc_sub,
                                   reviewer=is_reviewer(user))
    if not row:
        raise HTTPException(404, "not found")           # 404, never 403
    await ops_db.log_gateway_query(
        user=user, method="GET", path=f"/api/projects/{project_id}",
        mart_scope=["ops_workspace.project"], filters={},
        row_count=1, byte_count=None, latency_ms=0,
        http_status=200, policy_decision="allow")
    return _row_out(row)


# Patch (owner only)
@router.patch("/{project_id}")
async def patch_project(project_id: str,
                        user: UserCtx = Depends(get_user),
                        body: ProjectPatch = Body(...)) -> dict[str, Any]:
    if not user.kc_sub:
        raise HTTPException(401, "authenticated session required")
    if not _valid_uuid(project_id):
        raise HTTPException(404, "not found")

    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        row = await _fetch_visible(conn, project_id, user.kc_sub)
        if not row:
            raise HTTPException(404, "not found")       # invisible: 404
        if str(row["owner_kc_sub"]) != user.kc_sub:
            raise HTTPException(403, "only the owner can modify this project")

        # Resolve the post-update field values (None = unchanged).
        name        = body.name        if body.name        is not None else row["name"]
        description = body.description  if body.description is not None else row["description"]
        status      = body.status      if body.status      is not None else row["status"]
        visibility  = body.visibility  if body.visibility  is not None else row["visibility"]

        # group_id is required exactly when visibility == 'group' (as the DB CHECK).
        if visibility == "group":
            gid = body.group_id if body.group_id is not None else (
                str(row["group_id"]) if row["group_id"] else None)
            if not gid:
                raise HTTPException(400,
                    "group_id is required when visibility='group'")
            if not _valid_uuid(gid):
                raise HTTPException(400, "group_id must be a uuid")
            exists = await conn.fetchval(
                "SELECT 1 FROM ops_artifact.workshop_group WHERE id = $1::uuid", gid)
            if not exists:
                raise HTTPException(400, f"unknown group_id {gid}")
        else:
            gid = None      # non-group visibility must not carry a group

        updated = await conn.fetchrow("""
            UPDATE ops_workspace.project
               SET name = $2, description = $3, visibility = $4,
                   group_id = $5::uuid, status = $6, updated_at = now()
             WHERE id = $1::uuid
            RETURNING *;
        """, project_id, name, description, visibility, gid, status)

    out = _row_out(updated)
    out["owner_username"] = row["owner_username"]
    await ops_db.log_data_event(
        user=user, kind="project.updated",
        payload={"project_id": project_id, "visibility": visibility,
                 "group_id": gid, "status": status})
    await ops_db.log_gateway_query(
        user=user, method="PATCH", path=f"/api/projects/{project_id}",
        mart_scope=["ops_workspace.project"],
        filters={"visibility": visibility, "status": status},
        row_count=1, byte_count=None, latency_ms=0,
        http_status=200, policy_decision="allow")
    return out


# Files (gateway-proxied object listing)
@router.get("/{project_id}/files")
async def project_files(project_id: str,
                        user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    if not user.kc_sub:
        raise HTTPException(401, "authenticated session required")
    if not _valid_uuid(project_id):
        raise HTTPException(404, "not found")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        # Reviewers may list a submitted project's files, even a private one.
        row = await _fetch_visible(conn, project_id, user.kc_sub,
                                   reviewer=is_reviewer(user))
    if not row:
        raise HTTPException(404, "not found")           # ACL-checked

    prefix = _files_prefix(project_id)
    try:
        objs = await minio_client.list_prefix(prefix=prefix,
                                               bucket=PROJECTS_BUCKET)
    except Exception as exc:                                    # noqa: BLE001
        log.warning("project %s files list failed: %s", project_id, exc)
        raise HTTPException(502, f"file listing failed: {exc}")

    files = []
    folders: set[str] = set()
    for o in objs:
        name = o["name"]
        if name in ("", ".keep"):           # root prefix marker
            continue
        if name.endswith("/.keep"):         # a folder marker (otherwise empty folder)
            folders.add(name[: -len("/.keep")])
            continue
        if "/" in name:                     # a nested file: register its directories
            parts = name.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                folders.add("/".join(parts[:i]))
        lm = o.get("last_modified")
        files.append({"name": name, "size": o["size"],
                      "last_modified": lm.isoformat() if hasattr(lm, "isoformat") else lm})

    await ops_db.log_gateway_query(
        user=user, method="GET", path=f"/api/projects/{project_id}/files",
        mart_scope=["ops_workspace.project"], filters={},
        row_count=len(files), byte_count=None, latency_ms=0,
        http_status=200, policy_decision="allow")
    return {"project_id": project_id, "prefix": prefix, "files": files,
            "folders": sorted(folders), "count": len(files)}


@router.get("/{project_id}/download.zip")
async def project_download_zip(project_id: str,
                               user: UserCtx = Depends(get_user)):
    """Download the whole project file space as a zip. Requires an
    authenticated session and the project's visibility ACL (owner, group member
    or public); anonymous visitors get 401. Streams a zip of every object under
    projects/<id>/files/, skipping folder markers."""
    if not user.kc_sub:
        raise HTTPException(401, "authenticated session required to download projects")
    if not _valid_uuid(project_id):
        raise HTTPException(404, "not found")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        # Reviewers may download a submitted project, even a private one.
        row = await _fetch_visible(conn, project_id, user.kc_sub,
                                   reviewer=is_reviewer(user))
    if not row:
        raise HTTPException(404, "not found")               # ACL-checked

    prefix = _files_prefix(project_id)
    objs = await minio_client.list_prefix(prefix=prefix, bucket=PROJECTS_BUCKET)
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for o in objs:
            name = o["name"]
            if not name or name == ".keep" or name.endswith("/.keep"):
                continue
            data = await minio_client.read_object(key=prefix + name, bucket=PROJECTS_BUCKET)
            zf.writestr(name, data)
            n += 1
    if n == 0:
        raise HTTPException(404, "project has no files to download")
    buf.seek(0)
    slug = (row["slug"] if "slug" in row.keys() else None) or project_id
    await ops_db.log_gateway_query(
        user=user, method="GET", path=f"/api/projects/{project_id}/download.zip",
        mart_scope=["ops_workspace.project"], filters={"files": n},
        row_count=n, byte_count=buf.getbuffer().nbytes, latency_ms=0,
        http_status=200, policy_decision="allow")
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'})


# File management: nested folders, upload and project-to-project send
def _sanitize_relpath(p: str) -> str:
    """Normalise a user-supplied project-relative path: forward slashes, no
    leading slash, no '..' traversal, dots collapsed. Returns '' for empty."""
    p = (p or "").strip().replace("\\", "/").strip("/")
    if not p:
        return ""
    parts = [seg for seg in p.split("/") if seg and seg not in (".", "..")]
    return "/".join(parts)


async def _assert_contribute(conn, project_id: str, user: UserCtx):
    """Return the project row if the caller may write to it (owner, or member
    of a group project's group); otherwise raise the matching HTTP error."""
    row = await _fetch_visible(conn, project_id, user.kc_sub)
    if row is None:
        raise HTTPException(404, "not found")
    if row["status"] != "active":
        raise HTTPException(409, "project is archived")
    if str(row["owner_kc_sub"]) == (user.kc_sub or ""):
        return row
    is_member = row["visibility"] == "group" and bool(await conn.fetchval("""
        SELECT 1 FROM ops_artifact.workshop_group_member
        WHERE group_id = $1::uuid AND kc_sub = $2::uuid;
    """, row["group_id"], user.kc_sub))
    if not is_member:
        raise HTTPException(403, "you cannot modify this project "
                                 "(owner or project-group members only)")
    return row


@router.post("/{project_id}/files/folder", status_code=201)
async def create_folder(project_id: str,
                        user: UserCtx = Depends(get_user),
                        path: str = Body(..., embed=True)) -> dict[str, Any]:
    """Create a nested folder inside the project (a `.keep` marker at the
    path). Folders are only key prefixes in the object store."""
    if not _valid_uuid(project_id):
        raise HTTPException(404, "not found")
    rel = _sanitize_relpath(path)
    if not rel:
        raise HTTPException(400, "a folder path is required")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        await _assert_contribute(conn, project_id, user)
    marker = await minio_client.ensure_prefix(
        prefix=_files_prefix(project_id) + rel, bucket=PROJECTS_BUCKET)
    await ops_db.log_data_event(user=user, kind="project.folder_created",
                                payload={"project_id": project_id, "path": rel})
    return {"project_id": project_id, "path": rel, "marker": marker}


@router.post("/{project_id}/files/upload", status_code=201)
async def upload_file(project_id: str,
                      user: UserCtx = Depends(get_user),
                      path: str = Form(default=""),
                      file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload a file into the project, optionally under a nested folder path."""
    if not _valid_uuid(project_id):
        raise HTTPException(404, "not found")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        await _assert_contribute(conn, project_id, user)
    folder = _sanitize_relpath(path)
    fname = posixpath.basename((file.filename or "upload").replace("\\", "/")) or "upload"
    key = _files_prefix(project_id) + (f"{folder}/{fname}" if folder else fname)
    body = await file.read()
    _etag, sha256, byte_count = await minio_client.write_export(
        key=key, body=body,
        content_type=file.content_type or "application/octet-stream",
        bucket=PROJECTS_BUCKET)
    await ops_db.log_data_event(
        user=user, kind="project.file_uploaded",
        payload={"project_id": project_id, "key": key, "bytes": byte_count})
    return {"project_id": project_id, "name": (f"{folder}/{fname}" if folder else fname),
            "byte_count": byte_count, "sha256": sha256}


@router.post("/{project_id}/send", status_code=201)
async def send_to_project(
        project_id: str,                                   # SOURCE project
        user: UserCtx = Depends(get_user),
        target_project_id: str = Body(..., embed=True),
        item_type: str = Body(..., embed=True),            # file | dataset | project
        item_ref: str | None = Body(default=None, embed=True),  # file path or dataset id
        ) -> dict[str, Any]:
    """Direct send: copy an item into another project's received/ folder (the
    caller's own or someone else's). The bytes are copied server-side into
    projects/<target>/files/received/...; the sender must be able to read the
    source and see the target."""
    if not (_valid_uuid(project_id) and _valid_uuid(target_project_id)):
        raise HTTPException(404, "not found")
    if item_type not in ("file", "dataset", "project"):
        raise HTTPException(400, "item_type must be file | dataset | project")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        src = await _fetch_visible(conn, project_id, user.kc_sub)
        if src is None:
            raise HTTPException(404, "source project not found")
        # Direct send writes only to the target's received/ folder, so the
        # sender needs only to see the target (public, a group they belong to,
        # or their own), not write access to the rest of it.
        target = await _fetch_visible(conn, target_project_id, user.kc_sub)
        if target is None:
            raise HTTPException(404, "target project not found or not visible")
        if target["status"] != "active":
            raise HTTPException(409, "target project is archived")
        # Dataset visibility uses the dataset's own tier ACL.
        dataset_key = None
        if item_type == "dataset":
            if not item_ref:
                raise HTTPException(400, "item_ref (dataset id) is required")
            ds = await conn.fetchrow(f"""
                SELECT v.minio_bucket, v.minio_key,
                       (SELECT 1 FROM ops_artifact.artifact d WHERE d.id=$1::uuid
                          AND d.kind='dataset' AND (
                             d.tier='public'
                             OR (d.tier='shared' AND d.workshop_group IN
                                 (SELECT group_id FROM ops_artifact.workshop_group_member
                                  WHERE kc_sub=$2::uuid))
                             OR (d.tier='personal' AND d.owner=$2::uuid))) AS visible
                FROM ops_artifact.artifact_version v
                WHERE v.artifact_id=$1::uuid ORDER BY v.version DESC LIMIT 1;
            """, item_ref, user.kc_sub)
            if ds is None or not ds["visible"]:
                raise HTTPException(404, "dataset not found or not visible")
            dataset_key = ds["minio_key"]

    dst_folder = _files_prefix(target_project_id) + "received/"
    copied: list[str] = []
    try:
        if item_type == "file":
            rel = _sanitize_relpath(item_ref or "")
            if not rel:
                raise HTTPException(400, "item_ref (file path) is required")
            src_key = _files_prefix(project_id) + rel
            dst_key = dst_folder + posixpath.basename(rel)
            await minio_client.copy_object(src_key=src_key, dst_key=dst_key,
                                           bucket=PROJECTS_BUCKET)
            copied.append(dst_key)
        elif item_type == "dataset":
            dst_key = dst_folder + posixpath.basename(dataset_key)
            await minio_client.copy_object(src_key=dataset_key, dst_key=dst_key,
                                           bucket=PROJECTS_BUCKET)
            copied.append(dst_key)
        else:  # whole project: copy every file
            objs = await minio_client.list_prefix(
                prefix=_files_prefix(project_id), bucket=PROJECTS_BUCKET)
            for o in objs:
                if o["name"] in ("", ".keep"):
                    continue
                await minio_client.copy_object(
                    src_key=_files_prefix(project_id) + o["name"],
                    dst_key=dst_folder + o["name"], bucket=PROJECTS_BUCKET)
                copied.append(dst_folder + o["name"])
    except HTTPException:
        raise
    except Exception as exc:                                    # noqa: BLE001
        raise HTTPException(502, f"copy failed: {exc}")

    await ops_db.log_data_event(
        user=user, kind="project.item_sent",
        payload={"from": project_id, "to": target_project_id,
                 "item_type": item_type, "item_ref": item_ref,
                 "copied": len(copied)})
    return {"sent": True, "target_project_id": target_project_id,
            "target_name": target["name"], "item_type": item_type,
            "copied": len(copied), "destination": "received/"}
