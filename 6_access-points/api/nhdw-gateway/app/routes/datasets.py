"""/api/datasets: the workshop dataset builder (templates and governed generator).

A generated dataset is an artifact: the governed query result is written to the
object store and catalogued as ops_artifact.artifact kind=dataset, with a
version row, provenance (source mart and SQL preview) and the usual metric,
like and download lifecycle. A dataset is independent of projects; it can be
linked to any number of projects (ops_artifact.project_dataset), and linking
requires contribute rights on the project.

  GET  /api/datasets/templates   curated starters over catalogued marts
  POST /api/datasets/generate    run governed SQL -> CSV in the object store -> artifact

Authorisation is not re-implemented here: the query runs through
sql_lab.execute(), the single ad-hoc SQL chain (row-scope guard, syntax guard,
per-table mart_attrs and OPA, Trino, audit). A denial there is a denial here.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from typing import Any
from uuid import uuid4

import zipfile

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..auth import UserCtx, get_user
from ..settings import settings
from .. import minio_client, ops_db
from .graduation import CONTRIBUTOR_ROLES
from .sql_lab import ExecuteRequest, HARD_MAX_ROWS, execute as sql_execute

log = logging.getLogger("gateway.datasets")
router = APIRouter(prefix="/api/datasets", tags=["datasets"])

# Curated starters. Every mart here must be catalogued in mart_attrs; the
# executor rejects uncatalogued relations with 403.
TEMPLATES: list[dict[str, str]] = [
    {"id": "disease-weekly",
     "title": "Weekly disease surveillance",
     "description": "EWARS notifiable-disease weekly counts by region and "
                    "disease: the input behind the Disease page.",
     "mart": "bi_idsr.mart_idsr_disease_weekly",
     "sql": "SELECT * FROM bi_idsr.mart_idsr_disease_weekly"},
    {"id": "climate-drivers",
     "title": "Climate drivers (EWARS)",
     "description": "Rainfall / temperature / humidity lags joined to "
                    "disease weeks: the ewars_lite feature surface.",
     "mart": "bi_idsr.mart_idsr_climate_drivers",
     "sql": "SELECT * FROM bi_idsr.mart_idsr_climate_drivers"},
    {"id": "facility-capacity",
     "title": "Facility capacity roster",
     "description": "MFR-conformed facility roster with beds, staffing and "
                    "capacity indicators by region/woreda.",
     "mart": "bi_facility.mart_facility_capacity",
     "sql": "SELECT * FROM bi_facility.mart_facility_capacity"},
    {"id": "stock-by-region",
     "title": "Supply stock by region",
     "description": "Current stock position by region and item "
                    "(HCMIS + mBrana conformed).",
     "mart": "bi_supply.mart_supply_region_stock",
     "sql": "SELECT * FROM bi_supply.mart_supply_region_stock"},
    {"id": "programme-cascade",
     "title": "Programme cascade",
     "description": "HIV / TB / MNCH / NCD end-to-end cascade stages per "
                    "region and period.",
     "mart": "bi_service.mart_programme_cascade",
     "sql": "SELECT * FROM bi_service.mart_programme_cascade"},
]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(title: str) -> str:
    return (_SLUG_RE.sub("-", title.lower()).strip("-") or "dataset")[:60]


def _dataset_visible_clause(param_idx: int) -> str:
    """WHERE fragment binding $param_idx to the caller's kc_sub. A dataset is
    visible when public, shared with a group the caller belongs to, or personal
    and owned by the caller. Same tier ACL as routes/artifacts._visibility_clause,
    with no project gate."""
    p = f"${param_idx}"
    return (f"(d.tier = 'public'"
            f" OR (d.tier = 'shared' AND d.workshop_group IN"
            f"      (SELECT group_id FROM ops_artifact.workshop_group_member"
            f"       WHERE kc_sub = {p}::uuid))"
            f" OR (d.tier = 'personal' AND d.owner = {p}::uuid))")


async def _assert_can_contribute(conn, project_id: str, user: UserCtx) -> None:
    """The caller may add to a project only if they own it or belong to a group
    project's group. Raises 404 (missing), 409 (archived) or 403 (not allowed)."""
    project = await conn.fetchrow("""
        SELECT owner_kc_sub, visibility, group_id, status
        FROM ops_workspace.project WHERE id = $1::uuid;
    """, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    if project["status"] != "active":
        raise HTTPException(409, "project is archived")
    if str(project["owner_kc_sub"]) == (user.kc_sub or ""):
        return
    is_member = project["visibility"] == "group" and bool(await conn.fetchval("""
        SELECT 1 FROM ops_artifact.workshop_group_member
        WHERE group_id = $1::uuid AND kc_sub = $2::uuid;
    """, project["group_id"], user.kc_sub))
    if not is_member:
        raise HTTPException(403, "you cannot modify this project "
                                 "(owner or project-group members only)")


@router.get("/templates")
async def templates(user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    return {"items": [{k: t[k] for k in ("id", "title", "description", "mart")}
                      for t in TEMPLATES]}


@router.get("")
async def list_datasets(
        user: UserCtx = Depends(get_user),
        tier: str | None = Query(default=None, description="public|shared|personal"),
        project_id: str | None = Query(default=None,
                                       description="only datasets linked to this project"),
        owner: str | None = Query(default=None, description="'me' for my datasets"),
        q: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        ) -> dict[str, Any]:
    """List datasets visible under their own tier ACL, each with its linked
    projects (many-to-many)."""
    kc = user.kc_sub or "00000000-0000-0000-0000-000000000000"
    where = [f"d.kind = 'dataset'", _dataset_visible_clause(1)]
    params: list[Any] = [kc]
    if tier:
        where.append(f"d.tier = ${len(params)+1}"); params.append(tier)
    if owner == "me":
        where.append("d.owner = $1::uuid")
    if q:
        where.append(f"(d.title ILIKE ${len(params)+1} OR d.description ILIKE ${len(params)+1})")
        params.append(f"%{q}%")
    if project_id:
        where.append(f"EXISTS (SELECT 1 FROM ops_artifact.project_dataset pd "
                     f"WHERE pd.dataset_id = d.id AND pd.project_id = ${len(params)+1}::uuid)")
        params.append(project_id)
    sql = f"""
        SELECT d.id, d.title, d.description, d.tier, d.owner, d.tags,
               d.created_at, d.updated_at,
               u.username AS owner_username,
               m.downloads, m.views, m.likes,
               (SELECT MAX(version)::int FROM ops_artifact.artifact_version v
                  WHERE v.artifact_id = d.id) AS latest_version,
               COALESCE((SELECT json_agg(json_build_object('id', p.id, 'name', p.name)
                           ORDER BY p.name)
                         FROM ops_artifact.project_dataset pd
                         JOIN ops_workspace.project p ON p.id = pd.project_id
                         WHERE pd.dataset_id = d.id), '[]'::json) AS linked_projects
        FROM ops_artifact.artifact d
        LEFT JOIN ops_identity."user" u ON u.kc_sub = d.owner
        LEFT JOIN ops_artifact.artifact_metric m ON m.artifact_id = d.id
        WHERE {' AND '.join(where)}
        ORDER BY d.updated_at DESC
        LIMIT ${len(params)+1} OFFSET ${len(params)+2};
    """
    params.extend([limit, offset])
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    items = []
    for r in rows:
        d = dict(r)
        d["linked_projects"] = json.loads(d["linked_projects"]) if isinstance(d["linked_projects"], str) else d["linked_projects"]
        items.append(d)
    return {"items": items, "count": len(items)}


@router.post("/{dataset_id}/projects", status_code=201)
async def link_dataset(dataset_id: str,
                       user: UserCtx = Depends(get_user),
                       project_id: str = Body(..., embed=True)) -> dict[str, Any]:
    """Link a dataset to a project (many-to-many). The caller must be able to
    read the dataset (its tier ACL) and contribute to the target project."""
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        visible = await conn.fetchval(f"""
            SELECT 1 FROM ops_artifact.artifact d
            WHERE d.id = $2::uuid AND d.kind='dataset' AND {_dataset_visible_clause(1)};
        """, user.kc_sub or "00000000-0000-0000-0000-000000000000", dataset_id)
        if not visible:
            raise HTTPException(404, "dataset not found or not visible")
        await _assert_can_contribute(conn, project_id, user)
        await conn.execute("""
            INSERT INTO ops_artifact.project_dataset (project_id, dataset_id, added_by)
            VALUES ($1::uuid, $2::uuid, $3::uuid) ON CONFLICT DO NOTHING;
        """, project_id, dataset_id, user.kc_sub)
        # Resolve the dataset's latest version to copy its CSV into the project's
        # file space (same query as dataset_download_zip).
        ver = await conn.fetchrow("""
            SELECT v.minio_bucket, v.minio_key
            FROM ops_artifact.artifact_version v
            WHERE v.artifact_id = $1::uuid
            ORDER BY v.version DESC LIMIT 1;
        """, dataset_id)
    # Copy the linked dataset as a CSV into projects/<pid>/files/ so JupyterLab
    # sees it in the project tree. The key is deterministic (a datasets/
    # subfolder, keyed by dataset_id), so unlink removes exactly this copy and
    # never a user's own upload, and re-linking overwrites it. The copy is a
    # point-in-time snapshot: a dataset version published after linking does
    # not refresh it.
    if ver and ver["minio_key"]:
        await minio_client.copy_object(
            src_key=ver["minio_key"],
            dst_key=f"projects/{project_id}/files/datasets/{dataset_id}.csv",
            bucket=settings.ARTIFACTS_BUCKET,
            src_bucket=ver["minio_bucket"] or settings.ARTIFACTS_BUCKET)
    await ops_db.log_data_event(user=user, kind="dataset.linked",
                                payload={"dataset_id": dataset_id, "project_id": project_id})
    return {"linked": True, "dataset_id": dataset_id, "project_id": project_id}


@router.get("/{dataset_id}/download.zip")
async def dataset_download_zip(dataset_id: str,
                               user: UserCtx = Depends(get_user)):
    """Download a dataset as a zip of the latest version's object. Requires an
    authenticated session and the dataset's tier ACL: an anonymous visitor gets
    401 even for a public-tier dataset (the single-file public download stays
    open; the zip is role-gated)."""
    if not user.kc_sub:
        raise HTTPException(401, "authenticated session required to download datasets")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"""
            SELECT d.title,
                   v.minio_bucket, v.minio_key
            FROM ops_artifact.artifact d
            JOIN ops_artifact.artifact_version v ON v.artifact_id = d.id
            WHERE d.id = $2::uuid AND d.kind = 'dataset' AND {_dataset_visible_clause(1)}
            ORDER BY v.version DESC LIMIT 1;
        """, user.kc_sub, dataset_id)
    if not row or not row["minio_key"]:
        raise HTTPException(404, "dataset not found or not visible")

    data = await minio_client.read_object(key=row["minio_key"], bucket=row["minio_bucket"])
    fname = row["minio_key"].rsplit("/", 1)[-1] or "dataset.csv"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(fname, data)
    buf.seek(0)
    slug = (row["title"] or "dataset").lower().replace(" ", "-")
    await ops_db.log_gateway_query(
        user=user, method="GET", path=f"/api/datasets/{dataset_id}/download.zip",
        mart_scope=["ops_artifact.artifact"], filters={},
        row_count=1, byte_count=buf.getbuffer().nbytes, latency_ms=0,
        http_status=200, policy_decision="allow")
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'})


@router.delete("/{dataset_id}/projects/{project_id}")
async def unlink_dataset(dataset_id: str, project_id: str,
                         user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Unlink a dataset from a project. The caller must be able to contribute
    to the project (dataset links are managed from the project side)."""
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        await _assert_can_contribute(conn, project_id, user)
        await conn.execute("""
            DELETE FROM ops_artifact.project_dataset
            WHERE project_id = $1::uuid AND dataset_id = $2::uuid;
        """, project_id, dataset_id)
    # Remove only the link-managed copy (files/datasets/, keyed by dataset_id),
    # never a user's own upload. Idempotent: a no-op when no copy exists.
    await minio_client.delete_object(
        key=f"projects/{project_id}/files/datasets/{dataset_id}.csv",
        bucket=settings.ARTIFACTS_BUCKET)
    await ops_db.log_data_event(user=user, kind="dataset.unlinked",
                                payload={"dataset_id": dataset_id, "project_id": project_id})
    return {"linked": False, "dataset_id": dataset_id, "project_id": project_id}


@router.post("/generate", status_code=201)
async def generate(
        user: UserCtx = Depends(get_user),
        title: str = Body(..., embed=True, min_length=3, max_length=120),
        description: str | None = Body(default=None, embed=True),
        project_id: str | None = Body(default=None, embed=True),
        tier: str = Body(default="personal", embed=True),
        group_id: str | None = Body(default=None, embed=True),
        template_id: str | None = Body(default=None, embed=True),
        sql: str | None = Body(default=None, embed=True),
        limit: int = Body(default=2000, embed=True, ge=1, le=HARD_MAX_ROWS),
        ) -> dict[str, Any]:
    if not any(r in CONTRIBUTOR_ROLES for r in user.roles):
        raise HTTPException(403, "dataset generation is for workshop contributors")
    if tier not in ("personal", "shared", "public"):
        raise HTTPException(400, "tier must be personal | shared | public")
    if bool(template_id) == bool(sql):
        raise HTTPException(400, "provide exactly one of template_id or sql")
    if template_id:
        tpl = next((t for t in TEMPLATES if t["id"] == template_id), None)
        if tpl is None:
            raise HTTPException(404, f"unknown template {template_id!r}")
        sql = tpl["sql"]
        tag = template_id
    else:
        tag = "ad-hoc-sql"
    project_id = (project_id or "").strip() or None

    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        # A project is optional. When given, it is a many-to-many link (the
        # caller must be able to contribute to it), never the dataset's owner.
        if project_id:
            await _assert_can_contribute(conn, project_id, user)
        # The shared tier needs a group the caller belongs to; default to the
        # linked project's group when it has one.
        proj_group = None
        if project_id:
            proj_group = await conn.fetchval(
                "SELECT group_id FROM ops_workspace.project WHERE id=$1::uuid;",
                project_id)
        share_group = group_id or (str(proj_group) if proj_group else None)
        if tier == "shared":
            if not share_group:
                raise HTTPException(
                    400, "tier=shared needs a group_id (or a group project)")
            member = await conn.fetchval("""
                SELECT 1 FROM ops_artifact.workshop_group_member
                WHERE group_id = $1::uuid AND kc_sub = $2::uuid;
            """, share_group, user.kc_sub)
            if not member:
                raise HTTPException(403, "you are not a member of that group")

    # Governed execution through the single ad-hoc SQL chain (row-scope guard,
    # syntax guard, per-table OPA, audit). Its HTTPExceptions propagate.
    result = await sql_execute(ExecuteRequest(sql=sql, limit=limit), user)
    columns: list[str] = result["columns"]
    rows: list[dict[str, Any]] = result["rows"]
    if not columns:
        raise HTTPException(400, "query returned no columns")

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({c: r.get(c) for c in columns})
    body = buf.getvalue().encode("utf-8")

    artifact_id = str(uuid4())
    key = f"datasets/{artifact_id}/v1/{_slug(title)}.csv"
    _etag, sha256, byte_count = await minio_client.write_export(
        key=key, body=body, content_type="text/csv",
        bucket=settings.ARTIFACTS_BUCKET)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # project_id stays NULL on the artifact; project links live in
            # ops_artifact.project_dataset.
            await conn.execute("""
                INSERT INTO ops_artifact.artifact
                    (id, owner, kind, title, description, license, tier,
                     peer_review_status, tags, workshop_group)
                VALUES ($1::uuid, $2::uuid, 'dataset', $3, $4, 'CC-BY-4.0',
                        $5, 'unreviewed', $6, $7::uuid);
            """, artifact_id, user.kc_sub, title.strip(), description,
                tier, ["generated", tag],
                share_group if tier == "shared" else None)
            await conn.execute("""
                INSERT INTO ops_artifact.artifact_version
                    (artifact_id, version, minio_bucket, minio_key,
                     sha256, byte_count, change_note)
                VALUES ($1::uuid, 1, $2, $3, $4, $5, 'generated by the dataset builder');
            """, artifact_id, settings.ARTIFACTS_BUCKET, key, sha256, byte_count)
            # Optional link to the project it was generated from.
            if project_id:
                await conn.execute("""
                    INSERT INTO ops_artifact.project_dataset
                        (project_id, dataset_id, added_by)
                    VALUES ($1::uuid, $2::uuid, $3::uuid)
                    ON CONFLICT DO NOTHING;
                """, project_id, artifact_id, user.kc_sub)
                # Copy the new CSV into the project's file space for JupyterLab,
                # using the same key as link_dataset
                # (files/datasets/<dataset_id>.csv). Server-side copy within the
                # artifacts bucket; a point-in-time snapshot (see link_dataset).
                await minio_client.copy_object(
                    src_key=key,
                    dst_key=f"projects/{project_id}/files/datasets/{artifact_id}.csv",
                    bucket=settings.ARTIFACTS_BUCKET)
            # Lineage: the dataset derives from the mart(s) it selected from.
            for mart in result.get("tables_used") or []:
                await conn.execute("""
                    INSERT INTO ops_artifact.artifact_provenance
                        (artifact_id, parent_mart_name, note)
                    VALUES ($1::uuid, $2, $3)
                    ON CONFLICT DO NOTHING;
                """, artifact_id, mart, f"sql: {sql[:180]}")

    await ops_db.log_data_event(
        user=user, kind="dataset.generated",
        payload={"artifact_id": artifact_id, "project_id": project_id,
                 "tier": tier, "rows": len(rows), "minio_key": key,
                 "template": tag})
    return {"artifact_id": artifact_id, "version": 1, "rows": len(rows),
            "byte_count": byte_count, "sha256": sha256, "minio_key": key,
            "project_id": project_id, "tables_used": result.get("tables_used") or []}
