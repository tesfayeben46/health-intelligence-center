"""/api/audit: server-side paginated, filterable gateway access log for the
Admin Audit page. Each request returns one page of rows; the KPI aggregates
are computed over the whole filtered set in the same query."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..auth import UserCtx, get_user
from .. import ops_db

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/events")
async def events(
    user: UserCtx = Depends(get_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, description="free-text: path/method/actor/mart/filters"),
    decision: str | None = Query(None, pattern="^(allow|deny|error)$"),
    method: str | None = Query(None),
    status_class: str | None = Query(None, pattern="^([2345]xx)$"),
    since: str | None = Query(None, description="ISO timestamp lower bound (inclusive)"),
    until: str | None = Query(None, description="ISO timestamp upper bound (inclusive)"),
    actor_sub: str | None = Query(None, description="restrict to one actor kc_sub"),
    sort: str = Query("ts"),
    dir: str = Query("desc", pattern="^(asc|desc)$"),
) -> dict:
    return await ops_db.paginated_audit(
        q=(q.strip() or None) if q else None,
        decision=decision, method=method, status_class=status_class,
        since=since, until=until, actor_sub=actor_sub,
        sort=sort, direction=dir, page=page, page_size=page_size)
