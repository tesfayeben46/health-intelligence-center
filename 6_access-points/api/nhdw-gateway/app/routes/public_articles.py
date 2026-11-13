"""/api/public/articles: anonymous public newsroom and articles feed.

The public dashboard's Newsroom (top 3), Articles list and ArticleView read
these without a Bearer token, like the public access-request router. Content
lives in ops_governance.public_article, seeded from
bundle/seed-pack/app/articles/*.yaml by the security-seed job and editable at
runtime.

Only published, curated content is served. body_html is trusted authored HTML,
never visitor input.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from .. import ops_db

log = logging.getLogger("gateway.public_articles")

public_router = APIRouter(prefix="/api/public/articles", tags=["articles:public"])


def _row(r: dict[str, Any]) -> dict[str, Any]:
    """Shape a DB row into the SPA's Article contract."""
    return {
        "id":       r["id"],
        "slug":     r["slug"],
        "category": r["category"],
        "author":   r["author"],
        "title":    r["title"],
        "summary":  r["summary"],
        "body":     r["body_html"],                       # SPA field is `body` (HTML)
        "tags":     list(r["tags"] or []),
        "date":     r["published_at"].isoformat() if r["published_at"] else None,
        "linked_widget": r.get("linked_widget"),
    }


@public_router.get("")
async def list_articles(
        category: str | None = Query(default=None),
        limit:    int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    """Anonymous: list published articles, newest first (optional category)."""
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        if category and category != "all":
            rows = await conn.fetch("""
                SELECT id, slug, category, author, title, summary, body_html,
                       tags, published_at, linked_widget
                FROM ops_governance.public_article
                WHERE category = $1
                ORDER BY published_at DESC, id DESC LIMIT $2;
            """, category, limit)
        else:
            rows = await conn.fetch("""
                SELECT id, slug, category, author, title, summary, body_html,
                       tags, published_at, linked_widget
                FROM ops_governance.public_article
                ORDER BY published_at DESC, id DESC LIMIT $1;
            """, limit)
    return {"articles": [_row(dict(r)) for r in rows]}


@public_router.get("/{article_id}")
async def get_article(article_id: str) -> dict[str, Any]:
    """Anonymous: fetch one article by id (or slug)."""
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow("""
            SELECT id, slug, category, author, title, summary, body_html,
                   tags, published_at, linked_widget
            FROM ops_governance.public_article
            WHERE id = $1 OR slug = $1;
        """, article_id)
    if r is None:
        raise HTTPException(404, "article not found")
    return _row(dict(r))
