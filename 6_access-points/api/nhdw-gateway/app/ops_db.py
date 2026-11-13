"""Async client for the nhdw_ops database: audit logging, export registry,
just-in-time user caching, saved queries and audit reads."""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from datetime import datetime, timezone
from typing import Any

import asyncpg
from fastapi import HTTPException

from .auth import UserCtx
from .settings import settings

log = logging.getLogger("gateway.ops_db")

_pool: asyncpg.Pool | None = None
_lock = asyncio.Lock()


async def _init_codecs(conn: asyncpg.Connection) -> None:
    """Decode jsonb and json columns to Python objects rather than strings,
    so JSON fields (e.g. in /api/ml/*/predictions) reach clients as objects."""
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads,
                              schema="pg_catalog", format="text")
    await conn.set_type_codec("json",  encoder=json.dumps, decoder=json.loads,
                              schema="pg_catalog", format="text")


async def ensure_pool() -> asyncpg.Pool:
    global _pool
    async with _lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(
                host=settings.OPS_PG_HOST, port=settings.OPS_PG_PORT,
                database=settings.OPS_PG_DATABASE,
                user=settings.OPS_PG_USER, password=settings.OPS_PG_PASSWORD,
                min_size=1, max_size=8, command_timeout=10,
                init=_init_codecs)
            log.info("ops_db pool ready user=%s db=%s",
                     settings.OPS_PG_USER, settings.OPS_PG_DATABASE)
        return _pool


async def close_pool() -> None:
    global _pool
    async with _lock:
        if _pool is not None:
            await _pool.close()
            _pool = None


# JIT identity
async def upsert_user(user: UserCtx) -> None:
    if not user.kc_sub:
        return
    try:
        pool = await ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO ops_identity.user
                    (kc_sub, realm, username, email, full_name, role_label, locale, last_seen_at)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, now())
                ON CONFLICT (kc_sub) DO UPDATE SET
                    -- The X-Mock impersonation channel synthesizes
                    -- 'dev:<sub-prefix>' identities; a JIT touch from that
                    -- channel must never clobber a SEEDED username/profile
                    -- (it made artifact owners render as 'dev:33333333').
                    username     = CASE WHEN EXCLUDED.username LIKE 'dev:%'
                                        THEN ops_identity.user.username
                                        ELSE EXCLUDED.username END,
                    email        = CASE WHEN EXCLUDED.username LIKE 'dev:%'
                                        THEN ops_identity.user.email
                                        ELSE EXCLUDED.email END,
                    full_name    = CASE WHEN EXCLUDED.username LIKE 'dev:%'
                                        THEN ops_identity.user.full_name
                                        ELSE EXCLUDED.full_name END,
                    role_label   = EXCLUDED.role_label,
                    locale       = COALESCE(EXCLUDED.locale, ops_identity.user.locale),
                    last_seen_at = now();
            """, user.kc_sub, user.realm, user.username, user.email,
                 user.full_name, user.primary_role, user.locale)
    except Exception as exc:
        log.warning("upsert_user dropped: %s sub=%s", exc, user.kc_sub)


async def grant_user_role(conn, kc_sub: str, role_id: int,
                          granted_by: str | None = None,
                          source: str = "admin_grant") -> None:
    """The single role-assignment write, shared by the admin editor
    (perm_admin.grant_user_role) and access-request provisioning
    (access_requests._provision_account).
    Idempotent: re-activates and refreshes an existing (kc_sub, role_id) grant.
    Runs inside the caller's transaction (takes a connection, not the pool)."""
    await conn.execute("""
        INSERT INTO ops_perm.user_role
            (kc_sub, role_id, source, granted_at, granted_by, is_active)
        VALUES ($1::uuid, $2, $3, now(), NULLIF($4,'')::uuid, true)
        ON CONFLICT (kc_sub, role_id) DO UPDATE
           SET is_active  = true,
               source     = EXCLUDED.source,
               granted_by = EXCLUDED.granted_by,
               granted_at = now()
    """, kc_sub, role_id, source, granted_by or "")


# Audit and data events.
# Per-request "a handler already wrote a rich row" flag, used by the catch-all
# HTTP middleware (main.py) to avoid double logging. The middleware seeds a
# fresh mutable marker before call_next; log_gateway_query() sets
# marker["logged"] via _mark_logged(). The marker is a mutable object held in a
# ContextVar set before call_next, so the handler's mutation is visible to the
# middleware even though ContextVar reassignments do not propagate back out of
# BaseHTTPMiddleware.
_handler_logged: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "nhdw_handler_logged", default=None)


def new_request_log_marker() -> dict:
    """Seed a fresh 'did a handler log?' marker for the current request and
    return it. Call from the middleware just before dispatching call_next."""
    marker = {"logged": False}
    _handler_logged.set(marker)
    return marker


def _mark_logged() -> None:
    """Flag that a rich per-handler row was written for this request, so the
    catch-all middleware skips its base row. Never raises."""
    marker = _handler_logged.get()
    if marker is not None:
        marker["logged"] = True


async def log_gateway_query(*, user: UserCtx, method: str, path: str,
                            mart_scope: list[str], filters: dict[str, Any],
                            row_count: int | None, byte_count: int | None,
                            latency_ms: int, http_status: int,
                            policy_decision: str,
                            policy_obligations: dict[str, Any] | None = None,
                            denied_reason: str | None = None,
                            client_ip: str | None = None,
                            user_agent: str | None = None) -> None:
    try:
        pool = await ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO ops_audit.gateway_log
                    (kc_sub, method, path, mart_scope, filters,
                     row_count, byte_count, latency_ms, http_status,
                     policy_decision, policy_obligations, denied_reason,
                     client_ip, user_agent)
                VALUES (NULLIF($1,'')::uuid, $2, $3, $4, $5::jsonb, $6, $7,
                        $8, $9, $10, $11::jsonb, $12, $13::inet, $14);
            """, user.kc_sub, method, path, mart_scope, json.dumps(filters),
                 row_count, byte_count, latency_ms, http_status,
                 policy_decision,
                 json.dumps(policy_obligations) if policy_obligations else None,
                 denied_reason, client_ip, user_agent)
        # Rich row written: the catch-all middleware skips its base row.
        _mark_logged()
    except Exception as exc:
        log.warning("gateway_log dropped: %s path=%s", exc, path)


async def log_gateway_request(*, kc_sub: str | None, method: str, path: str,
                              latency_ms: int, http_status: int,
                              policy_decision: str,
                              client_ip: str | None = None,
                              user_agent: str | None = None,
                              auth_hint: str | None = None) -> None:
    """Base audit row written by the HTTP middleware for every request,
    including endpoints that never call log_gateway_query and requests rejected
    with 401 before any handler runs. Best-effort: all failures are swallowed so
    audit logging never affects the response.

    `kc_sub` is resolved through a subquery against ops_identity.user, so an
    unknown or not-yet-cached subject becomes NULL (actor shown as anonymous)
    instead of failing the foreign key. Non-JWT principals (PAT, mock) carry
    `kc_sub=None` and an `auth_hint` recorded in filters.

    ops_audit.gateway_log is append-only; retention is not handled here."""
    try:
        pool = await ensure_pool()
        filters = {"auth_hint": auth_hint} if auth_hint else {}
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO ops_audit.gateway_log
                    (kc_sub, method, path, filters, latency_ms, http_status,
                     policy_decision, client_ip, user_agent)
                VALUES (
                    (SELECT u.kc_sub FROM ops_identity."user" u
                      WHERE u.kc_sub = NULLIF($1,'')::uuid),
                    $2, $3, $4::jsonb, $5, $6, $7, $8::inet, $9);
            """, kc_sub, method, path, json.dumps(filters), latency_ms,
                 http_status, policy_decision, client_ip, user_agent)
    except Exception as exc:
        log.warning("gateway_log (middleware) dropped: %s path=%s", exc, path)


async def log_policy_decision(*, user: UserCtx, action: str, resource: str,
                              decision: str, rule_hits: list[str] | None,
                              explanation: dict[str, Any] | None = None,
                              correlation_id: str | None = None) -> None:
    try:
        pool = await ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO ops_audit.policy_log
                    (kc_sub, action, resource, decision, rule_hits,
                     explanation, correlation_id)
                VALUES (NULLIF($1,'')::uuid, $2, $3, $4, $5, $6::jsonb, $7::uuid);
            """, user.kc_sub, action, resource, decision, rule_hits or [],
                 json.dumps(explanation) if explanation else None,
                 correlation_id)
    except Exception as exc:
        log.warning("policy_log dropped: %s", exc)


async def log_data_event(*, user: UserCtx, kind: str,
                         payload: dict[str, Any] | None = None,
                         correlation_id: str | None = None) -> None:
    try:
        pool = await ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO ops_audit.data_event
                    (kc_sub, kind, payload, correlation_id)
                VALUES (NULLIF($1,'')::uuid, $2, $3::jsonb, $4::uuid);
            """, user.kc_sub, kind, json.dumps(payload or {}), correlation_id)
    except Exception as exc:
        log.warning("data_event dropped: %s kind=%s", exc, kind)


# Export registry
async def register_export(*, user: UserCtx, mart_scope: list[str],
                          sql_text: str | None, filter_envelope: dict[str, Any],
                          policy_decision: dict[str, Any], format: str,
                          retention_ttl_days: int = 90) -> str:
    pool = await ensure_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow("""
                INSERT INTO ops_export.export_registry
                    (requester, mart_scope, sql_text, filter_envelope,
                     policy_decision, format, status, retention_ttl_days)
                VALUES ($1::uuid, $2, $3, $4::jsonb, $5::jsonb, $6, 'pending', $7)
                RETURNING id;
            """, user.kc_sub, mart_scope, sql_text, json.dumps(filter_envelope),
                 json.dumps(policy_decision), format, retention_ttl_days)
        except asyncpg.exceptions.ForeignKeyViolationError:
            # The requester must exist in ops_identity.user, which every
            # interactive login populates. Report the unknown principal
            # explicitly instead of returning a bare 500.
            log.warning("export refused: no identity row for %s", user.kc_sub)
            raise HTTPException(
                409, "no cached identity for this principal; log in to the "
                     "dashboards once so the warehouse records you, then export.")
        return str(row["id"])


async def finalize_export(*, export_id: str, status: str,
                          minio_key: str | None = None,
                          row_count: int | None = None,
                          byte_count: int | None = None,
                          sha256: str | None = None,
                          citation_blurb: str | None = None,
                          error_msg: str | None = None) -> None:
    pool = await ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE ops_export.export_registry
               SET status         = $2,
                   completed_at   = now(),
                   minio_key      = COALESCE($3, minio_key),
                   row_count      = COALESCE($4, row_count),
                   byte_count     = COALESCE($5, byte_count),
                   sha256         = COALESCE($6, sha256),
                   citation_blurb = COALESCE($7, citation_blurb),
                   error_msg      = COALESCE($8, error_msg)
             WHERE id = $1::uuid;
        """, export_id, status, minio_key, row_count, byte_count,
             sha256, citation_blurb, error_msg)


# Read-only helpers
async def my_exports(user: UserCtx, limit: int = 50) -> list[dict[str, Any]]:
    pool = await ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, requested_at, completed_at, mart_scope, format, status,
                   row_count, byte_count, expires_at, minio_key, citation_blurb
            FROM ops_export.export_registry
            WHERE requester = $1::uuid
            ORDER BY requested_at DESC
            LIMIT $2;
        """, user.kc_sub, limit)
        return [dict(r) for r in rows]


# Columns the Audit page may sort on. The sort key is allowlisted, so it never
# reaches SQL as free text; anything else falls back to ts.
_AUDIT_SORT_COLUMNS: dict[str, str] = {
    "ts":          "g.ts",
    "latency_ms":  "g.latency_ms",
    "http_status": "g.http_status",
    "row_count":   "g.row_count",
    "method":      "g.method",
    "path":        "g.path",
    "decision":    "g.policy_decision",
    "actor":       'COALESCE(u.full_name, u.username, g.kc_sub::text)',
}


def _parse_ts(s: str | None) -> datetime | None:
    """Parse an ISO date or datetime bound into an aware datetime so asyncpg
    can bind it as timestamptz (asyncpg cannot encode a str for a parameter it
    infers as timestamptz). A naive value is taken as UTC. Unparseable input
    returns None, which drops the filter."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _build_audit_where(
    *, q: str | None, decision: str | None, method: str | None,
    status_class: str | None, since: datetime | None, until: datetime | None,
    actor_sub: str | None, start_idx: int = 1,
) -> tuple[str, list[Any], int]:
    """Build the parameterised WHERE fragment and positional args for the audit
    query from the active filters. Returns (where_sql, args, next_param_idx) so
    the caller can append LIMIT/OFFSET binds. Every value is bound ($n), never
    interpolated."""
    clauses: list[str] = []
    args: list[Any] = []
    i = start_idx

    if q:
        # One bound value referenced by several branches (asyncpg allows a
        # positional param to be reused across the statement).
        args.append(f"%{q}%")
        clauses.append(
            f"(g.path ILIKE ${i} OR g.method ILIKE ${i} OR g.kc_sub::text ILIKE ${i} "
            f"OR u.username ILIKE ${i} OR u.full_name ILIKE ${i} OR u.email ILIKE ${i} "
            f"OR array_to_string(g.mart_scope, ' ') ILIKE ${i} "
            f"OR g.filters::text ILIKE ${i})")
        i += 1
    if decision in ("allow", "deny", "error"):
        args.append(decision); clauses.append(f"g.policy_decision = ${i}"); i += 1
    if method:
        args.append(method.upper()); clauses.append(f"upper(g.method) = ${i}"); i += 1
    if status_class in ("2xx", "3xx", "4xx", "5xx"):
        lo = int(status_class[0]) * 100
        args.append(lo); args.append(lo + 100)
        clauses.append(f"g.http_status >= ${i} AND g.http_status < ${i + 1}"); i += 2
    if since:
        args.append(since); clauses.append(f"g.ts >= ${i}"); i += 1
    if until:
        args.append(until); clauses.append(f"g.ts <= ${i}"); i += 1
    if actor_sub:
        args.append(actor_sub); clauses.append(f"g.kc_sub = ${i}::uuid"); i += 1

    return (" AND ".join(clauses), args, i)


async def paginated_audit(
    *, q: str | None = None, decision: str | None = None,
    method: str | None = None, status_class: str | None = None,
    since: str | None = None, until: str | None = None,
    actor_sub: str | None = None, sort: str = "ts", direction: str = "desc",
    page: int = 1, page_size: int = 50,
) -> dict[str, Any]:
    """One audit page in a single round trip: the requested slice of
    ops_audit.gateway_log, filtered and sorted server-side, plus aggregate stats
    over the whole filtered set (not just the visible page) for the KPI tiles.
    Only `page_size` rows are returned."""
    pool = await ensure_pool()
    sort_col = _AUDIT_SORT_COLUMNS.get(sort, "g.ts")
    dir_sql = "ASC" if str(direction).lower() == "asc" else "DESC"
    page = max(1, int(page))
    page_size = min(200, max(1, int(page_size)))

    where_sql, args, next_i = _build_audit_where(
        q=q, decision=decision, method=method, status_class=status_class,
        since=_parse_ts(since), until=_parse_ts(until), actor_sub=actor_sub)
    where_clause = f"WHERE {where_sql}" if where_sql else ""
    base_from = (f'FROM ops_audit.gateway_log g '
                 f'LEFT JOIN ops_identity."user" u ON u.kc_sub = g.kc_sub '
                 f'{where_clause}')

    async with pool.acquire() as conn:
        stats = await conn.fetchrow(f"""
            SELECT
              count(*)                                              AS total,
              count(*) FILTER (WHERE g.policy_decision = 'deny')    AS deny,
              count(*) FILTER (WHERE g.policy_decision = 'error')   AS perr,
              count(*) FILTER (WHERE g.http_status >= 400
                                 AND g.http_status < 500)           AS http4xx,
              count(*) FILTER (WHERE g.http_status >= 500)          AS http5xx,
              count(*) FILTER (WHERE g.http_status IN (401, 403))   AS auth_denied,
              avg(g.latency_ms)::int                                AS avg_lat,
              max(g.latency_ms)                                     AS max_lat,
              min(g.ts)                                             AS min_ts,
              max(g.ts)                                             AS max_ts
            {base_from};
        """, *args)

        offset = (page - 1) * page_size
        rows = await conn.fetch(f"""
            SELECT g.id, g.ts, g.kc_sub, u.username, u.full_name, u.email,
                   u.realm, u.role_label, g.jwt_jti, g.method, g.path,
                   g.mart_scope, g.filters, g.row_count, g.byte_count,
                   g.latency_ms, g.http_status, g.policy_decision,
                   g.policy_obligations, g.denied_reason,
                   host(g.client_ip) AS client_ip, g.user_agent
            {base_from}
            ORDER BY {sort_col} {dir_sql} NULLS LAST, g.id DESC
            LIMIT ${next_i} OFFSET ${next_i + 1};
        """, *args, page_size, offset)

    total = int(stats["total"] or 0)
    return {
        "items": [dict(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, (total + page_size - 1) // page_size),
        "stats": {
            "total":       total,
            "deny":        int(stats["deny"] or 0),
            "error":       int(stats["perr"] or 0),
            "http4xx":     int(stats["http4xx"] or 0),
            "http5xx":     int(stats["http5xx"] or 0),
            "auth_denied": int(stats["auth_denied"] or 0),
            "avg_lat":     stats["avg_lat"],
            "max_lat":     stats["max_lat"],
            "min_ts":      stats["min_ts"],
            "max_ts":      stats["max_ts"],
        },
    }
