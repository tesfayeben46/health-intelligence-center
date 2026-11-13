"""/api/queries: saved-query CRUD for the Export & Query Editor.

Each row in ops_workspace.saved_query stores a builder_spec (the JSON the
dashboard form serialises: schema/table/columns/filters/limit) and optional
sql_text. Execution is not handled here: the Saved-Queries widget runs a
query's sql_text through the OPA-gated executor at /api/sql/execute
(routes/sql_lab.py), the single authorisation chain for SQL (read-only,
mandatory LIMIT, per-table mart_attrs and OPA). After a successful run the
widget calls POST /{id}/touch-run to stamp last_run_at so the list orders by
recency. This module owns only the CRUD and that timestamp.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import UserCtx, get_user
from .. import ops_db

log = logging.getLogger("gateway.queries")
router = APIRouter(prefix="/api/queries", tags=["queries"])


class SavedQueryIn(BaseModel):
    title:        str        = Field(min_length=2, max_length=200)
    description:  str | None = Field(default=None, max_length=2000)
    builder_spec: dict[str, Any] = Field(default_factory=dict)
    sql_text:     str | None = Field(default=None, max_length=8000)
    mart_scope:   list[str]  = Field(default_factory=list)
    share_scope:  str        = Field(default="private",
                                     pattern="^(private|shared|public)$")
    # Optional 5-field UTC cron expression. Stored only: no scheduled runner
    # currently executes saved queries on this schedule.
    schedule_cron: str | None = Field(default=None, max_length=120)
    notify_email:  str | None = Field(default=None, max_length=300)


@router.get("/mine")
async def list_my_queries(
        user: UserCtx = Depends(get_user),
        limit: int = Query(default=50, ge=1, le=200)) -> list[dict[str, Any]]:
    if not user.kc_sub:
        return []
    pool = await ops_db.ensure_pool()
    import json as _json
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, title, description, builder_spec, sql_text,
                   mart_scope, share_scope,
                   schedule_cron, notify_email,
                   last_run_at, last_scheduled_run_at, last_scheduled_status,
                   created_at, updated_at
            FROM ops_workspace.saved_query
            WHERE owner = $1::uuid
            ORDER BY updated_at DESC NULLS LAST, created_at DESC
            LIMIT $2;
        """, user.kc_sub, limit)
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        # asyncpg returns jsonb as a string when no decoder is configured;
        # deserialize so the client gets a proper object.
        bs = d.get("builder_spec")
        if isinstance(bs, str):
            try: d["builder_spec"] = _json.loads(bs)
            except Exception: pass
        out.append(d)
    return out


@router.post("")
async def create_query(
        user: UserCtx = Depends(get_user),
        body: SavedQueryIn = Body(...)) -> dict[str, Any]:
    if not user.kc_sub:
        raise HTTPException(401, "authenticated session required")
    pool = await ops_db.ensure_pool()
    import json as _json
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO ops_workspace.saved_query
                (owner, title, description, builder_spec, sql_text,
                 mart_scope, share_scope, schedule_cron, notify_email,
                 created_at, updated_at)
            VALUES ($1::uuid, $2, $3, $4::jsonb, $5, $6::text[], $7,
                    $8, $9, now(), now())
            RETURNING id, title, share_scope, schedule_cron, created_at;
        """, user.kc_sub, body.title, body.description,
            _json.dumps(body.builder_spec), body.sql_text,
            body.mart_scope, body.share_scope,
            body.schedule_cron, body.notify_email)
    return {"id": str(row["id"]), "title": row["title"],
            "share_scope": row["share_scope"],
            "created_at": row["created_at"].isoformat()}


@router.delete("/{query_id}")
async def delete_query(query_id: str,
                       user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    if not user.kc_sub:
        raise HTTPException(401, "authenticated session required")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        n = await conn.fetchval("""
            DELETE FROM ops_workspace.saved_query
             WHERE id = $1::uuid AND owner = $2::uuid
             RETURNING 1;
        """, query_id, user.kc_sub)
    if n is None:
        raise HTTPException(404, "not found or not yours")
    return {"id": query_id, "deleted": True}


@router.post("/{query_id}/touch-run")
async def touch_last_run(query_id: str,
                         user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Bump last_run_at on this query. The Saved-Queries widget calls it only
    after a successful run (via /api/sql/execute) or export, so the list orders
    by recency. It executes nothing and performs no data authorisation: it only
    stamps a row the caller owns. Execution and OPA gating happen in sql_lab."""
    if not user.kc_sub:
        raise HTTPException(401, "authenticated session required")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        ts = await conn.fetchval("""
            UPDATE ops_workspace.saved_query
               SET last_run_at = now()
             WHERE id = $1::uuid AND owner = $2::uuid
             RETURNING last_run_at;
        """, query_id, user.kc_sub)
    if ts is None:
        raise HTTPException(404, "not found or not yours")
    return {"id": query_id, "last_run_at": ts.isoformat()}
