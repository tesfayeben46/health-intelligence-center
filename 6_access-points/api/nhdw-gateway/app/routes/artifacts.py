"""/api/artifacts: unified read view across community artifacts.

Source of truth: ops_artifact.artifact, artifact_version, artifact_metric and
artifact_review, all populated by the graduation pathway. For models, the
MLflow alias set (@champion, @challenger) is included. Object-store keys come
from artifact_version.minio_key (historical column name).

  GET  /api/artifacts               faceted list (kind, tier, q, owner=me, sort, page)
  GET  /api/artifacts/{id}          full detail (versions, reviews, metrics);
                                    records a `views` event on artifact_metric
  POST /api/artifacts/{id}/like     per-user like toggle (artifact_like ledger)
  POST /api/artifacts/{id}/download presigned URL for the latest version bytes;
                                    records a `downloads` event

Auth: any authenticated caller; the tier filter respects share-scope:
- tier=public:  visible to everyone
- tier=shared:  visible to members of the artifact's workshop_group
- tier=personal: visible only to the owner

The route enforces this in the WHERE clause, so the dashboard cannot render
an unauthorised row. Like and download apply the same clause, so an
out-of-scope artifact returns 404 rather than revealing that it exists.
"""
from __future__ import annotations

import json
import logging
import os
import posixpath
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import UserCtx, get_user
from .. import minio_client, ops_db, opa

log = logging.getLogger("gateway.artifacts")
router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])

MLFLOW_URL = os.environ.get("MLFLOW_URL", "http://mlflow.nhdw.svc.cluster.local:5000")


def _visibility_clause() -> str:
    """SQL fragment, appended as "AND ( ... )" to the base WHERE, that binds
    the caller's identity as $1 (kc_sub as text). The public tier is open;
    shared requires workshop-group membership; personal requires ownership."""
    return ("""(
        a.tier = 'public'
        OR (a.tier = 'shared' AND a.workshop_group IN
              (SELECT group_id FROM ops_artifact.workshop_group_member
               WHERE kc_sub = $1::uuid))
        OR (a.tier = 'personal' AND a.owner = $1::uuid)
    )""")


@router.get("")
async def list_artifacts(
        user: UserCtx = Depends(get_user),
        kind: str | None = Query(default=None,
                                  description="model | dataset | notebook"),
        tier: str | None = Query(default=None,
                                  description="public | shared | personal"),
        q:    str | None = Query(default=None,
                                  description="case-insensitive title/desc search"),
        owner: str | None = Query(default=None,
                                   description="'me' for the caller's artifacts only"),
        project: str | None = Query(default=None,
                                    description="project id (uuid): only "
                                                "artifacts scoped to this project"),
        sort: str = Query(default="recent",
                          description="recent | name | downloads"),
        limit:  int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        ) -> dict[str, Any]:
    # Build the WHERE clause incrementally.
    where = [_visibility_clause()]
    params: list[Any] = [user.kc_sub or "00000000-0000-0000-0000-000000000000"]
    if kind:
        where.append(f"a.kind = ${len(params) + 1}")
        params.append(kind)
    if tier:
        where.append(f"a.tier = ${len(params) + 1}")
        params.append(tier)
    if q:
        where.append(f"(a.title ILIKE ${len(params) + 1} "
                     f"OR a.description ILIKE ${len(params) + 1})")
        params.append(f"%{q}%")
    if owner:
        # Only 'me' is supported: the Contributions board lists the caller's own
        # rows across all tiers. $1 is already the caller's kc_sub.
        if owner != "me":
            raise HTTPException(400, "owner supports only the value 'me'")
        where.append("a.owner = $1::uuid")
    if project:
        # Optional project scope, ANDed with the visibility clause above, so it
        # can only narrow the result.
        where.append(f"a.project_id = ${len(params) + 1}::uuid")
        params.append(project)

    order = ("a.updated_at DESC" if sort == "recent"
             else "a.title ASC"   if sort == "name"
             else "COALESCE(m.downloads, 0) DESC, a.updated_at DESC")

    sql = f"""
        SELECT
            a.id, a.kind, a.title, a.description, a.tier,
            a.license, a.citation, a.tags, a.peer_review_status,
            a.workshop_group, a.project_id, a.mlflow_model_name,
            a.created_at, a.updated_at,
            u.username AS owner_username,
            m.downloads, m.views, m.likes,
            (SELECT MAX(version)::int FROM ops_artifact.artifact_version v
                WHERE v.artifact_id = a.id) AS latest_version,
            (SELECT COUNT(*)::int FROM ops_artifact.artifact_review r
                WHERE r.artifact_id = a.id) AS review_count
        FROM ops_artifact.artifact a
        LEFT JOIN ops_identity.user u ON u.kc_sub = a.owner
        LEFT JOIN ops_artifact.artifact_metric m ON m.artifact_id = a.id
        WHERE { ' AND '.join(where) }
        ORDER BY { order }
        LIMIT ${len(params) + 1} OFFSET ${len(params) + 2};
    """
    params.extend([limit, offset])

    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
        total = await conn.fetchval(f"""
            SELECT COUNT(*) FROM ops_artifact.artifact a
            WHERE { ' AND '.join(where) };
        """, *params[:-2])
    items = [dict(r) for r in rows]
    return {"items": items, "total": int(total or 0),
            "limit": limit, "offset": offset}


async def _mlflow_for_model(name: str) -> dict[str, Any] | None:
    """Best-effort MLflow lookup. Returns None if the registry is unavailable
    or no matching registered model exists."""
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            r = await c.get(
                f"{MLFLOW_URL}/api/2.0/mlflow/registered-models/get",
                params={"name": name})
        if r.status_code != 200:
            return None
        body = (r.json() or {}).get("registered_model") or {}
        latest = (body.get("latest_versions") or [{}])[-1]
        # The registry uses aliases, not stages: render the alias set as the
        # "stage" string the workshop artifact modal shows.
        aliases = [f"@{a.get('alias')}" for a in (body.get("aliases") or [])]
        return {
            "mlflow_name":    body.get("name"),
            "mlflow_stage":   ", ".join(sorted(aliases)) or "None",
            "mlflow_version": latest.get("version"),
            "mlflow_run_id":  latest.get("run_id"),
        }
    except Exception:                                       # noqa: BLE001
        return None


@router.get("/{artifact_id}")
async def get_artifact(artifact_id: str,
                       user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        # Visibility-checked single fetch.
        artifact_row = await conn.fetchrow(f"""
            SELECT
                a.*,
                u.username AS owner_username
            FROM ops_artifact.artifact a
            LEFT JOIN ops_identity.user u ON u.kc_sub = a.owner
            WHERE a.id = $2::uuid AND { _visibility_clause() };
        """, user.kc_sub or "00000000-0000-0000-0000-000000000000",
            artifact_id)
        if artifact_row is None:
            raise HTTPException(404, "not found or not visible")
        # Record the view before reading the metric row back, so the modal shows
        # the updated count. Downloads and likes are maintained by their own
        # endpoints below (likes derive from the artifact_like ledger).
        await conn.execute("""
            INSERT INTO ops_artifact.artifact_metric (artifact_id, views)
            VALUES ($1::uuid, 1)
            ON CONFLICT (artifact_id) DO UPDATE
               SET views = artifact_metric.views + 1,
                   last_event_at = now();
        """, artifact_id)
        versions = await conn.fetch("""
            SELECT version, byte_count, minio_key,
                   created_at, sha256, change_note
            FROM ops_artifact.artifact_version
            WHERE artifact_id = $1::uuid
            ORDER BY version DESC;
        """, artifact_id)
        reviews = await conn.fetch("""
            SELECT r.reviewer, u.username AS reviewer_username,
                   r.verdict, r.comment AS note, r.reviewed_at
            FROM ops_artifact.artifact_review r
            LEFT JOIN ops_identity.user u ON u.kc_sub = r.reviewer
            WHERE r.artifact_id = $1::uuid
            ORDER BY r.reviewed_at DESC;
        """, artifact_id)
        metric = await conn.fetchrow("""
            SELECT downloads, views, likes, last_event_at
            FROM ops_artifact.artifact_metric
            WHERE artifact_id = $1::uuid;
        """, artifact_id)
        liked_by_me = await conn.fetchval("""
            SELECT EXISTS (SELECT 1 FROM ops_artifact.artifact_like
                           WHERE artifact_id = $1::uuid AND kc_sub = $2::uuid);
        """, artifact_id,
            user.kc_sub or "00000000-0000-0000-0000-000000000000")

    artifact = dict(artifact_row)
    if artifact.get("kind") == "model":
        # Artifact-to-MLflow binding (26_artifact_project.sh): use the bound
        # mlflow_model_name when set, otherwise match on title.
        ml_name = artifact.get("mlflow_model_name") or artifact.get("title") or ""
        ml = await _mlflow_for_model(ml_name)
        if ml:
            artifact.update(ml)

    return {
        "artifact": artifact,
        "versions": [dict(v) for v in versions],
        "reviews":  [dict(r) for r in reviews],
        "metric":   dict(metric) if metric else None,
        "liked_by_me": bool(liked_by_me),
    }


@router.post("/{artifact_id}/like")
async def toggle_like(artifact_id: str,
                      user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Per-user like toggle. The artifact_like ledger is the source of truth;
    artifact_metric.likes is recomputed from it inside the same transaction,
    so repeated clicks are idempotent and the counter cannot drift."""
    if not user.kc_sub:
        raise HTTPException(401, "no subject in token")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            visible = await conn.fetchval(f"""
                SELECT 1 FROM ops_artifact.artifact a
                WHERE a.id = $2::uuid AND {_visibility_clause()};
            """, user.kc_sub, artifact_id)
            if visible is None:
                raise HTTPException(404, "not found or not visible")
            # Likes reference ops_identity.user; a token subject with no identity
            # row gets 409, not 500.
            has_identity = await conn.fetchval("""
                SELECT 1 FROM ops_identity.user WHERE kc_sub = $1::uuid;
            """, user.kc_sub)
            if has_identity is None:
                raise HTTPException(409, "caller has no identity row")
            inserted = await conn.fetchval("""
                INSERT INTO ops_artifact.artifact_like (artifact_id, kc_sub)
                VALUES ($1::uuid, $2::uuid)
                ON CONFLICT (artifact_id, kc_sub) DO NOTHING
                RETURNING 1;
            """, artifact_id, user.kc_sub)
            liked = inserted is not None
            if not liked:
                await conn.execute("""
                    DELETE FROM ops_artifact.artifact_like
                    WHERE artifact_id = $1::uuid AND kc_sub = $2::uuid;
                """, artifact_id, user.kc_sub)
            likes = await conn.fetchval("""
                INSERT INTO ops_artifact.artifact_metric (artifact_id, likes)
                VALUES ($1::uuid,
                        (SELECT COUNT(*) FROM ops_artifact.artifact_like
                         WHERE artifact_id = $1::uuid))
                ON CONFLICT (artifact_id) DO UPDATE
                   SET likes = (SELECT COUNT(*) FROM ops_artifact.artifact_like
                                WHERE artifact_id = $1::uuid),
                       last_event_at = now()
                RETURNING likes;
            """, artifact_id)
    await ops_db.log_data_event(
        user=user, kind="artifact.liked" if liked else "artifact.unliked",
        payload={"artifact_id": artifact_id, "likes": int(likes or 0)})
    return {"liked": liked, "likes": int(likes or 0)}


@router.post("/{artifact_id}/download")
async def download_artifact(
        artifact_id: str,
        version: int | None = Query(default=None,
                                    description="specific version; default latest"),
        user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Gateway-mediated download: a visibility-checked presigned URL for the
    requested (default: latest) stored version, recording a `downloads`
    event. The SPA never sees S3 credentials, only a short-lived URL on the
    public object-store endpoint (as in exports.py)."""
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"""
            SELECT v.version, v.minio_bucket, v.minio_key,
                   v.byte_count, v.sha256
            FROM ops_artifact.artifact a
            JOIN ops_artifact.artifact_version v ON v.artifact_id = a.id
            WHERE a.id = $2::uuid
                  AND ($3::int IS NULL OR v.version = $3::int)
                  AND {_visibility_clause()}
            ORDER BY v.version DESC
            LIMIT 1;
        """, user.kc_sub or "00000000-0000-0000-0000-000000000000",
            artifact_id, version)
        if row is None:
            raise HTTPException(
                404, "not found, not visible, or no such stored version")
        await conn.execute("""
            INSERT INTO ops_artifact.artifact_metric (artifact_id, downloads)
            VALUES ($1::uuid, 1)
            ON CONFLICT (artifact_id) DO UPDATE
               SET downloads = artifact_metric.downloads + 1,
                   last_event_at = now();
        """, artifact_id)
    filename = (posixpath.basename(row["minio_key"])
                or f"artifact-v{row['version']}")
    url = await minio_client.presign_get(
        key=row["minio_key"], bucket=row["minio_bucket"], filename=filename)
    await ops_db.log_data_event(
        user=user, kind="artifact.downloaded",
        payload={"artifact_id": artifact_id, "version": row["version"],
                 "minio_key": row["minio_key"]})
    return {"download_url": url, "version": row["version"],
            "filename": filename, "byte_count": row["byte_count"],
            "sha256": row["sha256"]}
