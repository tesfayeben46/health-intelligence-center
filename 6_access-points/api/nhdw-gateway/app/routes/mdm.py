"""MDM stewardship: the human side of master-data conformance.

The matcher (2_pipelines/mdm) fills ops_governance.mdm_match_candidate with
scored same-entity suggestions; stewards work the queue here. Verdicts are
proposed and approved by two people in routes/mdm_approvals.py, and only an
approval writes ops_governance.mdm_match_decision, which is append-only and
bitemporal: a changed verdict supersedes the old row (stamps recorded_to and
inserts a new one) and never rewrites it. The vault build reads the current
decisions (v_facility_resolution), so a rebuild from the sources and the
decision log always reproduces the same masters.

Same pattern as graduation.py: a direct role gate (these verbs have no OPA Rego
rule), asyncpg via ops_db.ensure_pool(), one transaction per verdict, and
log_policy_decision for the audit trail.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Body, Depends, HTTPException, Path as PathParam, Query

from ..auth import UserCtx, get_user
from .. import opa, ops_db, trino_client

log = logging.getLogger("gateway.mdm")
router = APIRouter(prefix="/api/mdm", tags=["mdm"])

# Same roles as graduation's REVIEWER_ROLES: R-STEW for stewardship, the ops
# admin who operates the platform, and R-SUPER.
STEWARD_ROLES = ("R-STEW", "R-OPS-ADM", "R-SUPER")


def _steward_gate(user: UserCtx) -> opa.Decision:
    """Check the full role set, since primary_role is only the first R- role in
    an unordered Keycloak claim (see graduation._review_gate)."""
    allowed = any(r in STEWARD_ROLES for r in user.roles)
    return opa.Decision(
        allow=allowed,
        reason=f"mdm steward gate: roles {user.roles} "
               f"{'allowed' if allowed else 'denied'} (stewards only)")


def _require_steward(user: UserCtx) -> opa.Decision:
    decision = _steward_gate(user)
    if not decision.allow:
        raise HTTPException(403, decision.reason or "stewards only")
    return decision


def _cand_out(row) -> dict:
    return {
        "candidate_id": str(row["candidate_id"]),
        "deployment_context": row["deployment_context"],
        "entity_class_code": row["entity_class_code"],
        "left_source_system_code": row["left_source_system_code"],
        "left_source_record_key": row["left_source_record_key"],
        "right_source_system_code": row["right_source_system_code"],
        "right_source_record_key": row["right_source_record_key"],
        "suggested_master_bk": row["suggested_master_bk"],
        "score": float(row["score"]),
        "match_rule": row["match_rule"],
        "match_features": row["match_features"],
        "status": row["status"],
        "discovered_at": row["discovered_at"].isoformat(),
    }


@router.get("/queue")
async def queue(
    user: UserCtx = Depends(get_user),
    entity_class: str = Query(default="facility"),
    status: str = Query(default="pending"),
    match_rule: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """The steward work queue: candidates by status, best score first, with
    per-rule counts so the console can show the shape of the backlog."""
    _require_steward(user)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        where = ["entity_class_code = $1", "status = $2"]
        args: list = [entity_class, status]
        if match_rule:
            args.append(match_rule)
            where.append(f"match_rule = ${len(args)}")
        rows = await conn.fetch(f"""
            SELECT * FROM ops_governance.mdm_match_candidate
            WHERE {' AND '.join(where)}
            ORDER BY score DESC, left_source_record_key, suggested_master_bk
            LIMIT {limit} OFFSET {offset}
        """, *args)
        counts = await conn.fetch("""
            SELECT match_rule, status, count(*) AS n
            FROM ops_governance.mdm_match_candidate
            WHERE entity_class_code = $1
            GROUP BY match_rule, status
        """, entity_class)
    return {
        "candidates": [_cand_out(r) for r in rows],
        "counts": [{"match_rule": r["match_rule"], "status": r["status"],
                    "n": r["n"]} for r in counts],
        "limit": limit, "offset": offset,
    }


# There are no one-step verdict routes: verdicts are proposed and approved by
# two people in routes/mdm_approvals.py, and only an approval writes the log.


@router.get("/releases")
async def list_releases(user: UserCtx = Depends(get_user),
                        entity_class: str = Query(default="facility")) -> dict:
    """Published master-data cuts, newest first, with the counts captured when
    each was published."""
    _require_steward(user)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT r.*, COALESCE(jsonb_object_agg(s.stat_key, s.stat_value)
                                 FILTER (WHERE s.stat_key IS NOT NULL), '{}'::jsonb) AS stats
            FROM ops_governance.mdm_release r
            LEFT JOIN ops_governance.mdm_release_stat s USING (release_id)
            WHERE r.entity_class_code = $1
            GROUP BY r.release_id
            ORDER BY r.created_at DESC
        """, entity_class)
    return {"releases": [{
        "release_id": r["release_id"], "status": r["status"],
        "entity_class_code": r["entity_class_code"],
        "deployment_context": r["deployment_context"],
        "decisions_as_of": r["decisions_as_of"].isoformat(),
        "source_watermark": r["source_watermark"].isoformat(),
        "note": r["note"], "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat(),
        "published_at": r["published_at"].isoformat() if r["published_at"] else None,
        "superseded_by": r["superseded_by"],
        "stats": r["stats"],
    } for r in rows]}


@router.post("/releases")
async def publish_release(
    user: UserCtx = Depends(get_user),
    release_id: str = Body(..., embed=True),
    note: str | None = Body(default=None, embed=True),
    entity_class: str = Body(default="facility", embed=True),
) -> dict:
    """Publish a master-data release: pin the decision-log cut and the source
    watermark as of now, and record the master counts at that moment.

    Both coordinates are required to reproduce a master set: a decision cut
    alone does not freeze it, because a source record arriving later still
    joins its cluster. Published releases are immutable; correcting one means
    publishing a successor.
    """
    gate = _require_steward(user)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}", release_id):
        raise HTTPException(422, "release_id must be 3-64 chars of letters, "
                                 "digits, dot, dash or underscore (it gets quoted "
                                 "in reports; keep it readable)")

    # The source watermark and the counts come from the warehouse itself.
    try:
        # Epoch millis, not a formatted string: asyncpg type-checks a
        # timestamptz parameter before any SQL cast, so a Trino-formatted
        # string ('... UTC') is rejected, and parsing it here would depend on
        # Trino's rendering.
        wm = (await trino_client.query(
            "SELECT CAST(to_unixtime(max(load_ts)) * 1000 AS bigint) AS wm_ms "
            "FROM marts.nhdw_vault_business.hub_facility_conformed")).get("rows") or []
        counts = (await trino_client.query("""
            SELECT count(*) AS masters,
                   count(*) FILTER (WHERE mdm_state = 'conformed')     AS conformed,
                   count(*) FILTER (WHERE mdm_state = 'single_source') AS single_source
            FROM marts.nhdw_vault_business.dim_facility
        """)).get("rows") or []
    except Exception as exc:                                      # noqa: BLE001
        raise HTTPException(502, f"could not read the warehouse to pin a release: {exc}")
    if not wm or wm[0].get("wm_ms") is None:
        raise HTTPException(409, "the vault has no facility masters to pin yet")

    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                rel = await conn.fetchrow("""
                    INSERT INTO ops_governance.mdm_release
                        (release_id, entity_class_code, decisions_as_of,
                         source_watermark, note, status, created_by, published_at)
                    VALUES ($1, $2, now(), to_timestamp($3::bigint / 1000.0),
                            $4, 'published', $5, now())
                    RETURNING decisions_as_of, source_watermark
                """, release_id, entity_class, int(wm[0]["wm_ms"]), note, user.username)
            except Exception as exc:                              # noqa: BLE001
                if "duplicate key" in str(exc):
                    raise HTTPException(
                        409, f"release {release_id} already exists; published "
                             f"releases are immutable; publish a successor instead")
                raise
            n_dec = await conn.fetchval("""
                SELECT count(*) FROM ops_governance.mdm_match_decision
                WHERE entity_class_code = $1 AND recorded_to IS NULL
                  AND decision IN ('accept', 'merge')
            """, entity_class)
            stats = {"masters": int(counts[0]["masters"]) if counts else 0,
                     "conformed": int(counts[0]["conformed"]) if counts else 0,
                     "single_source": int(counts[0]["single_source"]) if counts else 0,
                     "decisions_applied": int(n_dec or 0)}
            for k, v in stats.items():
                await conn.execute("""
                    INSERT INTO ops_governance.mdm_release_stat (release_id, stat_key, stat_value)
                    VALUES ($1, $2, $3)
                """, release_id, k, v)

    await ops_db.log_policy_decision(
        user=user, action="mdm.release.publish", resource=release_id,
        decision="allow", rule_hits=gate.rule_hits,
        explanation={"decisions_as_of": rel["decisions_as_of"].isoformat(),
                     "source_watermark": rel["source_watermark"].isoformat(),
                     **stats})
    return {"release_id": release_id, "status": "published",
            "decisions_as_of": rel["decisions_as_of"].isoformat(),
            "source_watermark": rel["source_watermark"].isoformat(),
            "stats": stats,
            # Reproduction is a query, not a deployment: the as-of view reads
            # the pin from a session setting.
            "reproduce_with": (
                f"SET nhdw.release_as_of = '{rel['decisions_as_of'].isoformat()}'; "
                "SELECT * FROM nhdw_vault_business.v_facility_resolution_asof;"),
            "source_watermark_note": (
                "pair the decision pin with this watermark: source rows loaded "
                "after it are not part of this release")}


@router.get("/master/{entity_class}/{master_bk}")
async def master_detail(entity_class: str = PathParam(...),
                        master_bk: str = PathParam(...),
                        user: UserCtx = Depends(get_user)) -> dict:
    """Everything the platform knows about one master: what a steward needs
    before accepting a merge.

    Three reads of the business vault (through Trino as bi_reader):
      members      the source records that resolve to this master, and how
                   (facility_id, vocabulary, norm_name, steward_decision)
      golden       the current canonical revision, with per-field provenance
      identifiers  every namespaced id the master is known by

    If the vault has not built the golden or identifier models yet, those
    sections come back empty instead of failing the whole view.
    """
    _require_steward(user)
    if entity_class != "facility":
        raise HTTPException(422, "master detail is available for facilities today")

    bk = master_bk.replace("'", "''")          # escape single quotes for the SQL literal
    async def q(sql: str) -> list[dict]:
        try:
            return (await trino_client.query(sql)).get("rows", []) or []
        except Exception as exc:                                  # noqa: BLE001
            log.info("master detail: %s", exc)
            return []

    members = await q(f"""
        SELECT source_system, source_name, match_strategy,
               to_hex(hk_source_facility) AS source_record_key
        FROM marts.nhdw_vault_business.v_facility_resolution
        WHERE conformed_bk = '{bk}'
        ORDER BY source_system
    """)
    golden = await q(f"""
        SELECT c.canonical_name, c.name_source, c.operational_status,
               c.status_source, c.telecom, c.telecom_source,
               c.latitude, c.longitude, c.coord_source,
               c.woreda_name, c.zone_name, c.admin_source, c.load_ts
        FROM marts.nhdw_vault_business.sat_facility_canonical c
        JOIN marts.nhdw_vault_business.hub_facility_conformed h
          ON h.hk_facility_conformed = c.hk_facility_conformed
        WHERE h.conformed_bk = '{bk}'
        ORDER BY c.load_ts DESC
        LIMIT 1
    """)
    identifiers = await q(f"""
        SELECT i.identifier_namespace, i.identifier_value
        FROM marts.nhdw_vault_business.lnk_facility_identifier i
        JOIN marts.nhdw_vault_business.hub_facility_conformed h
          ON h.hk_facility_conformed = i.hk_facility_conformed
        WHERE h.conformed_bk = '{bk}'
        ORDER BY i.identifier_namespace, i.identifier_value
    """)

    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        decisions = await conn.fetch("""
            SELECT source_system_code, source_record_key, decision, decided_by,
                   recorded_from
            FROM ops_governance.mdm_match_decision
            WHERE entity_class_code = $1 AND master_bk = $2 AND recorded_to IS NULL
            ORDER BY recorded_from DESC
        """, entity_class, master_bk)

    return {
        "master_bk": master_bk,
        "entity_class": entity_class,
        "members": members,
        "n_source_systems": len({m["source_system"] for m in members}),
        "golden": golden[0] if golden else None,
        "identifiers": identifiers,
        "decisions": [{"source_system_code": d["source_system_code"],
                       "source_record_key": d["source_record_key"],
                       "decision": d["decision"], "decided_by": d["decided_by"],
                       "recorded_from": d["recorded_from"].isoformat()}
                      for d in decisions],
    }


@router.get("/decisions")
async def decisions(
    user: UserCtx = Depends(get_user),
    entity_class: str = Query(default="facility"),
    current_only: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """The decision log: current decisions by default, full bitemporal history
    with current_only=false."""
    _require_steward(user)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        cur_filter = "AND recorded_to IS NULL" if current_only else ""
        rows = await conn.fetch(f"""
            SELECT * FROM ops_governance.mdm_match_decision
            WHERE entity_class_code = $1 {cur_filter}
            ORDER BY recorded_from DESC
            LIMIT {limit} OFFSET {offset}
        """, entity_class)
    return {"decisions": [{
        "decision_id": str(r["decision_id"]),
        "deployment_context": r["deployment_context"],
        "entity_class_code": r["entity_class_code"],
        "source_system_code": r["source_system_code"],
        "source_record_key": r["source_record_key"],
        "decision": r["decision"],
        "master_bk": r["master_bk"],
        "candidate_id": str(r["candidate_id"]) if r["candidate_id"] else None,
        "score": float(r["score"]) if r["score"] is not None else None,
        "decided_by": r["decided_by"],
        "note": r["note"],
        "valid_from": r["valid_from"].isoformat(),
        "valid_to": r["valid_to"].isoformat(),
        "recorded_from": r["recorded_from"].isoformat(),
        "recorded_to": r["recorded_to"].isoformat() if r["recorded_to"] else None,
        "current": r["recorded_to"] is None,
    } for r in rows], "limit": limit, "offset": offset}
