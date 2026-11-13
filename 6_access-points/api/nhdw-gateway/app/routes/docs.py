"""/api/docs: operator system documentation, served from the object store
(SeaweedFS, S3 API; bucket nhdw-docs).

The curated system docs (docs/*.md in the repo) are uploaded to the private
`nhdw-docs` bucket at boot by bundle/scripts/seed-docs.sh, together with a
`docs-index.json` catalogue (name/title/description/section). This route lists
them and serves each one as markdown text (for the in-app modal viewer) or as a
presigned download. Access requires view_ops_console (R-OPS-* roles): the docs
are operator reference and are never anonymous, unlike nhdw-static.

The capability gate is duplicated here so the routers stay decoupled.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import minio_client, ops_db, permissions
from ..auth import UserCtx, get_user

router = APIRouter(prefix="/api/docs", tags=["docs"])
log = logging.getLogger("gateway.routes.docs")

DOCS_BUCKET = "nhdw-docs"
INDEX_KEY = "docs-index.json"
MAX_DOC_BYTES = 4_000_000  # upper bound on a single document read


async def _require_ops(user: UserCtx) -> None:
    """403 unless the caller has view_ops_console (any R-OPS-* admin)."""
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rids = await permissions._resolve_role_ids(conn, user)
        caps = await permissions._role_capabilities(conn, rids)
    if "view_ops_console" not in caps:
        raise HTTPException(403, "missing capability: view_ops_console")


def _safe_name(name: str) -> str:
    if "/" in name or "\\" in name or ".." in name or not name.endswith(".md"):
        raise HTTPException(400, "invalid document name")
    return name


@router.get("")
async def list_docs(user: UserCtx = Depends(get_user)) -> dict:
    """The doc catalogue from docs-index.json. Falls back to a bucket listing
    when the index is absent, so documents stay reachable without one."""
    await _require_ops(user)
    idx = await minio_client.read_object(key=INDEX_KEY, bucket=DOCS_BUCKET,
                                         max_bytes=512_000)
    if idx:
        try:
            docs = json.loads(idx)
        except Exception:
            docs = []
    else:
        objs = await minio_client.list_prefix(prefix="", bucket=DOCS_BUCKET)
        docs = [{"name": o["name"], "title": o["name"].removesuffix(".md"),
                 "description": "", "section": "System documentation",
                 "size": o["size"]}
                for o in objs if o["name"].endswith(".md")]
    return {"docs": docs, "bucket": DOCS_BUCKET}


@router.get("/{name}")
async def get_doc(name: str, user: UserCtx = Depends(get_user),
                  download: int = Query(default=0)) -> dict:
    """Fetch one doc. With ?download=1, return a presigned download URL on the
    browser-reachable MINIO_PUBLIC_ENDPOINT; otherwise return the raw markdown
    for the modal viewer."""
    await _require_ops(user)
    name = _safe_name(name)
    if download:
        url = await minio_client.presign_get(key=name, bucket=DOCS_BUCKET,
                                              filename=name)
        return {"name": name, "url": url}
    body = await minio_client.read_object(key=name, bucket=DOCS_BUCKET,
                                          max_bytes=MAX_DOC_BYTES)
    if body is None:
        raise HTTPException(404, f"document not found: {name}")
    return {"name": name, "markdown": body.decode("utf-8", "replace")}
