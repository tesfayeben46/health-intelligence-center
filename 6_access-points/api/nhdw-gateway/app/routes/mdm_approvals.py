"""MDM maker-checker: a steward proposes, a different person approves.

Sits beside routes/mdm.py (same /api/mdm prefix, same direct role gate).
Nothing a steward does here touches ops_governance.mdm_match_decision; only
approval writes the decision row, in the same transaction as the approval, so a
dashboard never shows a change one person made alone. Tables: init script
44_mdm_proposal.sql.

The approver signs off on a check: when a proposal is made or re-checked, the
gateway fingerprints the candidate and the record's current decision and gives
the check a validity window. Approval recomputes the fingerprint; if anything
changed, or the window has passed, approval is refused and the steward checks
again.

  steward   POST candidate/{id}/propose     same | different; creates a proposal, checked at once
            POST candidate/{id}/review      not_sure | dismissed (no decision)
            POST decision/{id}/propose-restore
            POST proposals/bulk             many candidates, one result per item
            POST proposal/{id}/check        re-check an expired / failed proposal
            POST proposal/{id}/withdraw     proposer only
  approver  POST proposal/{id}/approve      writes the decision (never the proposer)
            POST proposal/{id}/decline      reason required
  both      GET  review-queue, proposals, proposal/{id}

Who approves: a token role in APPROVER_ROLES, or the `mdm_approve` capability
on a role granted in ops_perm (the console's role grant). The capability path
needs no Keycloak change; ops_perm.user_role is the single source of grants,
the same check perm_admin._require_capability makes.

Audit rows (ops_audit.policy_log) are written inside each transaction, so a
change and its audit entry commit or roll back together; if the audit row
cannot be written, the change does not happen.

Four-eyes compares the Keycloak subject (the person), never the username: a
service token and a personal login of the same person are the same person.
Every action here therefore needs a personal identity (a kc_sub).

The decision row keeps the old meaning of decided_by (the steward who judged
the records); the approver is on the proposal row and in the decision note.
"""
from __future__ import annotations

import json
import os
from uuid import UUID

import logging

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Path as PathParam, Query

from ..auth import UserCtx, get_user
from .. import ops_db, permissions
from .mdm import STEWARD_ROLES

log = logging.getLogger("gateway.mdm_approvals")

router = APIRouter(prefix="/api/mdm", tags=["mdm"])

# Approval is data governance, not platform operation, so the ops admin is not
# an approver by default. R-SUPER remains for break-glass. Four-eyes is enforced
# below and by a table constraint regardless of role.
APPROVER_ROLES = ("R-STEW-APR", "R-SUPER")
APPROVE_CAPABILITY = "mdm_approve"

CHECK_TTL_MINUTES = int(os.environ.get("MDM_CHECK_TTL_MINUTES", "30"))
# Score at or above which the console offers "send for approval together".
# Still a proposal, never an automatic link. Ambiguous-name suggestions never
# qualify: their 1.00 means the name matches several masters, not certainty.
HIGH_CONFIDENCE = float(os.environ.get("MDM_HIGH_CONFIDENCE_SCORE", "0.95"))

OPEN = ("proposed", "checked")
BULK_MAX = 500


def _plain(value) -> dict:
    """JSON-safe copy (timestamps as ISO text). The pool's jsonb codec encodes
    Python objects itself, so callers pass the result, never a JSON string."""
    return json.loads(json.dumps(value, default=lambda v: v.isoformat()
                                  if hasattr(v, "isoformat") else str(v)))


def _has(user: UserCtx, roles: tuple[str, ...]) -> bool:
    return any(r in roles for r in user.roles)


def _require(user: UserCtx, roles: tuple[str, ...], what: str) -> None:
    if not _has(user, roles):
        raise HTTPException(403, f"{what}: roles {user.roles} not allowed")


async def is_approver(user: UserCtx) -> bool:
    """Approver by token role, or by the mdm_approve capability granted in
    ops_perm. Fails closed: no personal identity, or no ops_perm, means no."""
    if _has(user, APPROVER_ROLES):
        return True
    if not user.kc_sub:
        return False
    pool = await ops_db.ensure_pool()
    try:
        async with pool.acquire() as conn:
            rids = await permissions._resolve_role_ids(conn, user)
            return APPROVE_CAPABILITY in await permissions._role_capabilities(conn, rids)
    except (asyncpg.UndefinedTableError, asyncpg.InvalidSchemaNameError) as exc:
        log.warning("approver capability check unavailable: %s", exc)
        return False


async def _require_approver(user: UserCtx, what: str) -> None:
    if not await is_approver(user):
        raise HTTPException(403, f"{what}: approvers only")


def _person(user: UserCtx) -> str:
    """The caller's Keycloak subject, or 403. Four-eyes and the audit trail
    both need to know which person acted."""
    try:
        return str(UUID(user.kc_sub))
    except (ValueError, TypeError):
        raise HTTPException(403, "a personal sign-in is required for this action")


def _is_person(user: UserCtx, sub) -> bool:
    try:
        return str(UUID(user.kc_sub)) == str(sub)
    except (ValueError, TypeError):
        return False


def _uuid(value: str) -> str:
    try:
        UUID(value)
    except (ValueError, TypeError):
        raise HTTPException(404, "not found")
    return value


async def _audit(conn: asyncpg.Connection, user: UserCtx, action: str,
                 resource: str, explanation: dict) -> None:
    """policy_log row in the caller's transaction. kc_sub resolves to NULL when
    the identity is not mirrored in ops_identity (the FK would otherwise abort
    the business change); the username always travels in the explanation."""
    await conn.execute("""
        INSERT INTO ops_audit.policy_log
            (kc_sub, action, resource, decision, rule_hits, explanation)
        VALUES ((SELECT u.kc_sub FROM ops_identity."user" u
                  WHERE u.kc_sub = NULLIF($1,'')::uuid),
                $2, $3, 'allow', $4, $5::jsonb)
    """, user.kc_sub, action, resource, list(user.roles),
         _plain({"actor": user.username, **explanation}))


async def _event(conn: asyncpg.Connection, proposal_id: str, event: str,
                 actor: str, detail: dict | None = None) -> None:
    await conn.execute("""
        INSERT INTO ops_governance.mdm_proposal_event (proposal_id, event, actor, detail)
        VALUES ($1::uuid, $2, $3, $4::jsonb)
    """, proposal_id, event, actor, _plain(detail or {}))


# The check
# Fingerprint of everything an approval depends on: the candidate as the
# matcher left it, and the decision the record currently carries.
_CURRENT_DECISION = """
    COALESCE((SELECT d.decision_id::text || ':' || d.decision || ':' || COALESCE(d.master_bk, '')
                FROM ops_governance.mdm_match_decision d
               WHERE d.deployment_context = p.deployment_context
                 AND d.entity_class_code  = p.entity_class_code
                 AND d.source_system_code = p.source_system_code
                 AND d.source_record_key  = p.source_record_key
                 AND d.recorded_to IS NULL), 'none')
"""

_DIGEST_SQL = f"""
    SELECT md5(concat_ws('|',
               COALESCE(c.status, ''), COALESCE(c.suggested_master_bk, ''),
               COALESCE(c.score::text, ''), COALESCE(c.match_rule, ''),
               COALESCE(c.match_features::text, ''),
               {_CURRENT_DECISION})) AS digest,
           c.status AS cand_status, c.suggested_master_bk AS cand_master,
           (SELECT d.recorded_to IS NULL FROM ops_governance.mdm_match_decision d
             WHERE d.decision_id = p.restores_decision_id) AS restore_target_current
    FROM ops_governance.mdm_change_proposal p
    LEFT JOIN ops_governance.mdm_match_candidate c ON c.candidate_id = p.candidate_id
    WHERE p.proposal_id = $1::uuid
"""


def _evidence(cand) -> dict:
    """What the approver is shown: the suggestion as the matcher left it."""
    return {
        "score": float(cand["score"]), "match_rule": cand["match_rule"],
        "match_features": cand["match_features"],
        "high_confidence_score": HIGH_CONFIDENCE,
        "right_source_system_code": cand["right_source_system_code"],
        "right_source_record_key": cand["right_source_record_key"],
        "matcher_version": cand["matcher_version"],
    }


async def _lock_inputs(conn: asyncpg.Connection, prop) -> None:
    """Row-lock everything the fingerprint covers (candidate, the record's
    current decision, the decision a restore targets) so nothing moves
    between the check and the write."""
    if prop["candidate_id"]:
        await conn.execute("""
            SELECT 1 FROM ops_governance.mdm_match_candidate
             WHERE candidate_id = $1::uuid FOR UPDATE
        """, prop["candidate_id"])
    await conn.execute("""
        SELECT 1 FROM ops_governance.mdm_match_decision
         WHERE (deployment_context = $1 AND entity_class_code = $2
                AND source_system_code = $3 AND source_record_key = $4
                AND recorded_to IS NULL)
            OR decision_id = $5::uuid
        FOR UPDATE
    """, prop["deployment_context"], prop["entity_class_code"],
         prop["source_system_code"], prop["source_record_key"],
         prop["restores_decision_id"])


async def _check_state(conn: asyncpg.Connection, prop) -> tuple[str, str | None]:
    """(digest, failure reason or None) for a proposal as the data stands now."""
    st = await conn.fetchrow(_DIGEST_SQL, str(prop["proposal_id"]))
    if prop["kind"] == "restore":
        if not st["restore_target_current"]:
            return st["digest"], "the decision to restore is no longer current"
    else:
        if st["cand_status"] not in ("pending", "auto_accepted"):
            return st["digest"], f"the suggestion is already {st['cand_status']}"
        if prop["kind"] == "link" and st["cand_master"] != prop["master_bk"]:
            return st["digest"], "the suggested master changed"
    return st["digest"], None


async def _run_check(conn: asyncpg.Connection, prop, actor: str) -> dict:
    await _lock_inputs(conn, prop)
    digest, failure = await _check_state(conn, prop)
    pid = str(prop["proposal_id"])
    if prop["candidate_id"] and prop["kind"] != "restore":
        # the approver signs off on what the records say now, not at proposal time
        cand = await conn.fetchrow("""
            SELECT * FROM ops_governance.mdm_match_candidate WHERE candidate_id = $1::uuid
        """, prop["candidate_id"])
        await conn.execute("""
            UPDATE ops_governance.mdm_change_proposal SET evidence = $2::jsonb
             WHERE proposal_id = $1::uuid
        """, pid, _plain(_evidence(cand)))
    if failure:
        await conn.execute("""
            UPDATE ops_governance.mdm_change_proposal
               SET status = 'check_failed', check_digest = $2, checked_at = now(),
                   valid_until = NULL
             WHERE proposal_id = $1::uuid
        """, pid, digest)
        await _event(conn, pid, "check_failed", actor, {"reason": failure})
        return {"status": "check_failed", "reason": failure}
    row = await conn.fetchrow(f"""
        UPDATE ops_governance.mdm_change_proposal
           SET status = 'checked', check_digest = $2, checked_at = now(),
               valid_until = now() + make_interval(mins => {CHECK_TTL_MINUTES})
         WHERE proposal_id = $1::uuid
        RETURNING valid_until
    """, pid, digest)
    await _event(conn, pid, "checked", actor, {"valid_until": row["valid_until"]})
    return {"status": "checked", "valid_until": row["valid_until"].isoformat()}


# Output shapes
_EFFECTIVE = ("CASE WHEN p.status = 'checked' AND p.valid_until < now() "
              "THEN 'expired' ELSE p.status END")


def _prop_out(r) -> dict:
    return {
        "proposal_id": str(r["proposal_id"]),
        "deployment_context": r["deployment_context"],
        "entity_class_code": r["entity_class_code"],
        "kind": r["kind"],
        "candidate_id": str(r["candidate_id"]) if r["candidate_id"] else None,
        "restores_decision_id": str(r["restores_decision_id"]) if r["restores_decision_id"] else None,
        "source_system_code": r["source_system_code"],
        "source_record_key": r["source_record_key"],
        "master_bk": r["master_bk"],
        "evidence": r["evidence"],
        "note": r["note"],
        "proposed_by": r["proposed_by"],
        "proposed_at": r["proposed_at"].isoformat(),
        "status": r["effective_status"],
        "checked_at": r["checked_at"].isoformat() if r["checked_at"] else None,
        "valid_until": r["valid_until"].isoformat() if r["valid_until"] else None,
        "reviewed_by": r["reviewed_by"],
        "reviewed_at": r["reviewed_at"].isoformat() if r["reviewed_at"] else None,
        "review_note": r["review_note"],
        "applied_decision_id": str(r["applied_decision_id"]) if r["applied_decision_id"] else None,
    }


async def _load(conn: asyncpg.Connection, proposal_id: str, lock: bool = False):
    row = await conn.fetchrow(f"""
        SELECT p.*, {_EFFECTIVE} AS effective_status
        FROM ops_governance.mdm_change_proposal p
        WHERE p.proposal_id = $1::uuid {'FOR UPDATE' if lock else ''}
    """, proposal_id)
    if row is None:
        raise HTTPException(404, "not found")
    return row


# Steward: review queue
@router.get("/review-queue")
async def review_queue(
    user: UserCtx = Depends(get_user),
    entity_class: str = Query(default="facility"),
    min_score: float | None = Query(default=None, ge=0, le=1),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Suggestions still waiting for a steward: pending, not under an open
    proposal, not dismissed. A 'not sure' stays in the queue, flagged."""
    _require(user, STEWARD_ROLES, "mdm review")
    base = """
        FROM ops_governance.mdm_match_candidate c
        LEFT JOIN ops_governance.mdm_candidate_review r
               ON r.candidate_id = c.candidate_id AND r.recorded_to IS NULL
        WHERE c.entity_class_code = $1
          AND c.status IN ('pending', 'auto_accepted')
          AND (r.outcome IS NULL OR r.outcome <> 'dismissed')
          AND NOT EXISTS (
                SELECT 1 FROM ops_governance.mdm_change_proposal p
                 WHERE p.deployment_context = c.deployment_context
                   AND p.entity_class_code = c.entity_class_code
                   AND p.source_system_code = c.left_source_system_code
                   AND p.source_record_key = c.left_source_record_key
                   AND p.status IN ('proposed', 'checked'))
    """
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT c.*, r.outcome AS review_outcome, r.reviewed_by AS review_by
            {base} AND c.score >= $2
            ORDER BY c.score DESC, c.left_source_record_key, c.suggested_master_bk
            LIMIT {limit} OFFSET {offset}
        """, entity_class, min_score if min_score is not None else 0)
        totals = await conn.fetchrow(f"""
            SELECT count(*) AS waiting,
                   count(*) FILTER (WHERE c.score >= $2
                                      AND c.suggested_master_bk IS NOT NULL
                                      AND c.match_rule <> 'name_ambiguous') AS high_confidence
            {base}
        """, entity_class, HIGH_CONFIDENCE)
    return {
        "candidates": [{
            "candidate_id": str(r["candidate_id"]),
            "entity_class_code": r["entity_class_code"],
            "left_source_system_code": r["left_source_system_code"],
            "left_source_record_key": r["left_source_record_key"],
            "right_source_system_code": r["right_source_system_code"],
            "right_source_record_key": r["right_source_record_key"],
            "suggested_master_bk": r["suggested_master_bk"],
            "score": float(r["score"]),
            "match_rule": r["match_rule"],
            "match_features": r["match_features"],
            "review": ({"outcome": r["review_outcome"], "by": r["review_by"]}
                       if r["review_outcome"] else None),
        } for r in rows],
        "waiting": totals["waiting"],
        "high_confidence": totals["high_confidence"],
        "high_confidence_score": HIGH_CONFIDENCE,
        "limit": limit, "offset": offset,
    }


# Steward: propose
async def _propose_candidate(conn: asyncpg.Connection, user: UserCtx,
                             candidate_id: str, verdict: str, note: str | None) -> dict:
    """One proposal and its check, on the caller's connection (own transaction)."""
    sub = _person(user)
    kind = {"same": "link", "different": "distinct"}.get(verdict)
    if kind is None:
        raise HTTPException(422, "verdict must be same or different")
    async with conn.transaction():
        cand = await conn.fetchrow("""
            SELECT * FROM ops_governance.mdm_match_candidate
            WHERE candidate_id = $1::uuid FOR UPDATE
        """, candidate_id)
        if cand is None:
            raise HTTPException(404, "not found")
        if cand["status"] not in ("pending", "auto_accepted"):
            raise HTTPException(409, f"suggestion already {cand['status']}")
        if kind == "link" and not cand["suggested_master_bk"]:
            raise HTTPException(409, "suggestion carries no master to link to")
        evidence = _evidence(cand)
        try:
            prop = await conn.fetchrow("""
                INSERT INTO ops_governance.mdm_change_proposal
                    (deployment_context, entity_class_code, kind, candidate_id,
                     source_system_code, source_record_key, master_bk,
                     evidence, note, proposed_by, proposed_by_sub)
                VALUES ($1, $2, $3, $4::uuid, $5, $6, $7, $8::jsonb, $9, $10, $11::uuid)
                RETURNING *
            """, cand["deployment_context"], cand["entity_class_code"], kind,
                 candidate_id, cand["left_source_system_code"],
                 cand["left_source_record_key"],
                 cand["suggested_master_bk"] if kind == "link" else None,
                 _plain(evidence), note, user.username, sub)
        except asyncpg.UniqueViolationError:
            raise HTTPException(409, "this record already has an open proposal")
        pid = str(prop["proposal_id"])
        await _event(conn, pid, "proposed", user.username, {"kind": kind})
        check = await _run_check(conn, prop, user.username)
        await _audit(conn, user, "mdm.propose", pid,
                     {"kind": kind, "candidate_id": candidate_id,
                      "record": f"{cand['left_source_system_code']}/{cand['left_source_record_key']}",
                      "master_bk": prop["master_bk"], "check": check["status"]})
    return {"proposal_id": pid, "kind": kind, **check}


@router.post("/candidate/{candidate_id}/propose")
async def propose(
    candidate_id: str = PathParam(...),
    user: UserCtx = Depends(get_user),
    verdict: str = Body(..., embed=True),
    note: str | None = Body(default=None, embed=True),
) -> dict:
    """'Same' proposes linking the record to the suggested master; 'different'
    proposes recording that it is not. Checked at once; nothing is applied."""
    _require(user, STEWARD_ROLES, "mdm propose")
    _uuid(candidate_id)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        return await _propose_candidate(conn, user, candidate_id, verdict, note)


@router.post("/proposals/bulk")
async def propose_bulk(
    user: UserCtx = Depends(get_user),
    candidate_ids: list[str] = Body(..., embed=True),
    note: str | None = Body(default=None, embed=True),
) -> dict:
    """Propose 'same' for many suggestions. Each item is its own transaction:
    the result lists every item, so a partial failure is reported, never hidden."""
    _require(user, STEWARD_ROLES, "mdm propose")
    _person(user)
    if not candidate_ids:
        raise HTTPException(422, "candidate_ids is empty")
    if len(candidate_ids) > BULK_MAX:
        raise HTTPException(422, f"at most {BULK_MAX} per request")
    results = []
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        for cid in dict.fromkeys(candidate_ids):          # de-duplicate, keep order
            try:
                _uuid(cid)
                res = await _propose_candidate(conn, user, cid, "same", note)
                results.append({"candidate_id": cid, "ok": True, **res})
            except HTTPException as exc:
                results.append({"candidate_id": cid, "ok": False, "error": exc.detail})
            except asyncpg.PostgresError as exc:      # e.g. a deadlock: this item only
                results.append({"candidate_id": cid, "ok": False,
                                "error": f"database error: {type(exc).__name__}"})
    ok = sum(1 for r in results if r["ok"])
    return {"requested": len(results), "proposed": ok,
            "failed": len(results) - ok, "results": results}


@router.post("/candidate/{candidate_id}/review")
async def review(
    candidate_id: str = PathParam(...),
    user: UserCtx = Depends(get_user),
    outcome: str = Body(..., embed=True),
    note: str | None = Body(default=None, embed=True),
) -> dict:
    """'not_sure' parks the suggestion with a flag; 'dismissed' removes it from
    the queue. Neither is a mastering statement: the decision log is untouched."""
    _require(user, STEWARD_ROLES, "mdm review")
    _uuid(candidate_id)
    if outcome not in ("not_sure", "dismissed"):
        raise HTTPException(422, "outcome must be not_sure or dismissed")
    _person(user)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("""
                SELECT 1 FROM ops_governance.mdm_match_candidate
                 WHERE candidate_id = $1::uuid FOR UPDATE
            """, candidate_id) is None:
                raise HTTPException(404, "not found")
            await conn.execute("""
                UPDATE ops_governance.mdm_candidate_review SET recorded_to = now()
                 WHERE candidate_id = $1::uuid AND recorded_to IS NULL
            """, candidate_id)
            rid = await conn.fetchval("""
                INSERT INTO ops_governance.mdm_candidate_review
                    (candidate_id, outcome, reviewed_by, note)
                VALUES ($1::uuid, $2, $3, $4) RETURNING review_id
            """, candidate_id, outcome, user.username, note)
            await _audit(conn, user, "mdm.review", candidate_id,
                         {"outcome": outcome, "note": note})
    return {"candidate_id": candidate_id, "review_id": str(rid), "outcome": outcome}


@router.post("/decision/{decision_id}/propose-restore")
async def propose_restore(
    decision_id: str = PathParam(...),
    user: UserCtx = Depends(get_user),
    note: str | None = Body(default=None, embed=True),
) -> dict:
    """Undo goes through the same four eyes: propose superseding a current
    decision. On approval the record falls back to deterministic conformance."""
    _require(user, STEWARD_ROLES, "mdm propose")
    sub = _person(user)
    _uuid(decision_id)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            dec = await conn.fetchrow("""
                SELECT * FROM ops_governance.mdm_match_decision WHERE decision_id = $1::uuid
            """, decision_id)
            if dec is None:
                raise HTTPException(404, "not found")
            if dec["recorded_to"] is not None:
                raise HTTPException(409, "decision already superseded")
            try:
                prop = await conn.fetchrow("""
                    INSERT INTO ops_governance.mdm_change_proposal
                        (deployment_context, entity_class_code, kind, candidate_id,
                         restores_decision_id, source_system_code, source_record_key,
                         master_bk, evidence, note, proposed_by, proposed_by_sub)
                    VALUES ($1, $2, 'restore', $3, $4::uuid, $5, $6, $7, $8::jsonb, $9, $10, $11::uuid)
                    RETURNING *
                """, dec["deployment_context"], dec["entity_class_code"],
                     dec["candidate_id"], decision_id, dec["source_system_code"],
                     dec["source_record_key"], dec["master_bk"],
                     _plain({"restores": {"decision": dec["decision"],
                                           "master_bk": dec["master_bk"],
                                           "decided_by": dec["decided_by"]}}),
                     note, user.username, sub)
            except asyncpg.UniqueViolationError:
                raise HTTPException(409, "this record already has an open proposal")
            pid = str(prop["proposal_id"])
            await _event(conn, pid, "proposed", user.username, {"kind": "restore"})
            check = await _run_check(conn, prop, user.username)
            await _audit(conn, user, "mdm.propose", pid,
                         {"kind": "restore", "decision_id": decision_id,
                          "check": check["status"]})
    return {"proposal_id": pid, "kind": "restore", **check}


@router.post("/proposal/{proposal_id}/check")
async def recheck(
    proposal_id: str = PathParam(...),
    user: UserCtx = Depends(get_user),
) -> dict:
    """Re-run the check on an open or failed proposal (e.g. after it expired)."""
    _require(user, STEWARD_ROLES, "mdm check")
    _person(user)
    _uuid(proposal_id)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            prop = await _load(conn, proposal_id, lock=True)
            if prop["status"] not in ("proposed", "checked", "check_failed"):
                raise HTTPException(409, f"proposal is {prop['status']}")
            if prop["status"] == "check_failed":
                # Back into the open set; the unique index guards against a newer
                # open proposal for the same record.
                try:
                    await conn.execute("""
                        UPDATE ops_governance.mdm_change_proposal SET status = 'proposed'
                         WHERE proposal_id = $1::uuid
                    """, proposal_id)
                except asyncpg.UniqueViolationError:
                    raise HTTPException(409, "this record already has a newer open proposal")
            check = await _run_check(conn, prop, user.username)
            await _audit(conn, user, "mdm.check", proposal_id, {"check": check["status"]})
    return {"proposal_id": proposal_id, **check}


@router.post("/proposal/{proposal_id}/withdraw")
async def withdraw(
    proposal_id: str = PathParam(...),
    user: UserCtx = Depends(get_user),
) -> dict:
    _require(user, STEWARD_ROLES, "mdm withdraw")
    _person(user)
    _uuid(proposal_id)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            prop = await _load(conn, proposal_id, lock=True)
            if not _is_person(user, prop["proposed_by_sub"]):
                raise HTTPException(403, "only the proposer can withdraw a proposal")
            if prop["status"] not in ("proposed", "checked", "check_failed"):
                raise HTTPException(409, f"proposal is {prop['status']}")
            await conn.execute("""
                UPDATE ops_governance.mdm_change_proposal SET status = 'withdrawn'
                 WHERE proposal_id = $1::uuid
            """, proposal_id)
            await _event(conn, proposal_id, "withdrawn", user.username)
            await _audit(conn, user, "mdm.withdraw", proposal_id, {})
    return {"proposal_id": proposal_id, "status": "withdrawn"}


# Approver
async def _apply(conn: asyncpg.Connection, prop, approver: str) -> str | None:
    """Write the decision: supersede the current decision, insert the new one,
    and close the candidate and its pending siblings; for a restore, supersede
    and re-open. Returns decision_id."""
    ctx, cls = prop["deployment_context"], prop["entity_class_code"]
    sys_code, rec_key = prop["source_system_code"], prop["source_record_key"]
    if prop["kind"] == "restore":
        done = await conn.execute("""
            UPDATE ops_governance.mdm_match_decision SET recorded_to = now()
             WHERE decision_id = $1::uuid AND recorded_to IS NULL
        """, prop["restores_decision_id"])
        if done != "UPDATE 1":       # never re-stamp history that already moved
            raise HTTPException(409, "stale: the decision to restore is no longer current")
        if prop["candidate_id"]:
            await conn.execute("""
                UPDATE ops_governance.mdm_match_candidate SET status = 'pending'
                 WHERE candidate_id = $1::uuid
            """, prop["candidate_id"])
        return None

    verdict = "accept" if prop["kind"] == "link" else "reject"
    await conn.execute("""
        UPDATE ops_governance.mdm_match_decision SET recorded_to = now()
         WHERE deployment_context = $1 AND entity_class_code = $2
           AND source_system_code = $3 AND source_record_key = $4
           AND recorded_to IS NULL
    """, ctx, cls, sys_code, rec_key)
    note = f"proposed by {prop['proposed_by']}, approved by {approver}"
    if prop["note"]:
        note += f": {prop['note']}"
    dec_id = await conn.fetchval("""
        INSERT INTO ops_governance.mdm_match_decision
            (deployment_context, entity_class_code, source_system_code,
             source_record_key, decision, master_bk, candidate_id, score,
             decided_by, note)
        VALUES ($1, $2, $3, $4, $5, $6, $7::uuid, $8, $9, $10)
        RETURNING decision_id
    """, ctx, cls, sys_code, rec_key, verdict,
         prop["master_bk"] if verdict == "accept" else None,
         prop["candidate_id"],
         await conn.fetchval("SELECT score FROM ops_governance.mdm_match_candidate "
                             "WHERE candidate_id = $1::uuid", prop["candidate_id"]),
         prop["proposed_by"], note)
    await conn.execute("""
        UPDATE ops_governance.mdm_match_candidate SET status = 'decided'
         WHERE candidate_id = $1::uuid
    """, prop["candidate_id"])
    if verdict == "accept":
        await conn.execute("""
            UPDATE ops_governance.mdm_match_candidate SET status = 'superseded'
             WHERE deployment_context = $1 AND entity_class_code = $2
               AND left_source_system_code = $3 AND left_source_record_key = $4
               AND status = 'pending' AND candidate_id <> $5::uuid
        """, ctx, cls, sys_code, rec_key, prop["candidate_id"])
    return str(dec_id)


@router.post("/proposal/{proposal_id}/approve")
async def approve(
    proposal_id: str = PathParam(...),
    user: UserCtx = Depends(get_user),
    note: str | None = Body(default=None, embed=True),
) -> dict:
    """Approve and apply in one transaction. Refused when: the caller proposed
    it (403), it is not checked (409), the check expired (409 expired), or the
    records moved since the check (409 stale)."""
    await _require_approver(user, "mdm approve")
    sub = _person(user)
    _uuid(proposal_id)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            prop = await _load(conn, proposal_id, lock=True)
            if str(prop["proposed_by_sub"]) == sub:
                raise HTTPException(403, "you cannot approve your own proposal")
            if prop["effective_status"] == "expired":
                raise HTTPException(409, "expired: check the changes again")
            if prop["status"] != "checked":
                raise HTTPException(409, f"proposal is {prop['effective_status']}")
            await _lock_inputs(conn, prop)
            digest, failure = await _check_state(conn, prop)
            if failure or digest != prop["check_digest"]:
                raise HTTPException(409, "stale: the records changed since the check"
                                         + (f" ({failure})" if failure else ""))
            dec_id = await _apply(conn, prop, user.username)
            await conn.execute("""
                UPDATE ops_governance.mdm_change_proposal
                   SET status = 'applied', reviewed_by = $2, reviewed_by_sub = $5::uuid,
                       reviewed_at = now(), review_note = $3, applied_decision_id = $4::uuid
                 WHERE proposal_id = $1::uuid
            """, proposal_id, user.username, note, dec_id, sub)
            await _event(conn, proposal_id, "approved", user.username, {"note": note})
            await _event(conn, proposal_id, "applied", user.username,
                         {"decision_id": dec_id})
            await _audit(conn, user, "mdm.approve", proposal_id,
                         {"kind": prop["kind"], "proposed_by": prop["proposed_by"],
                          "record": f"{prop['source_system_code']}/{prop['source_record_key']}",
                          "master_bk": prop["master_bk"], "decision_id": dec_id})
    return {"proposal_id": proposal_id, "status": "applied", "decision_id": dec_id}


@router.post("/proposal/{proposal_id}/decline")
async def decline(
    proposal_id: str = PathParam(...),
    user: UserCtx = Depends(get_user),
    note: str = Body("", embed=True),
) -> dict:
    await _require_approver(user, "mdm decline")
    sub = _person(user)
    _uuid(proposal_id)
    if not note or not note.strip():
        raise HTTPException(422, "a reason is required to decline")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            prop = await _load(conn, proposal_id, lock=True)
            if str(prop["proposed_by_sub"]) == sub:
                raise HTTPException(403, "you cannot decline your own proposal; withdraw it")
            if prop["status"] not in ("proposed", "checked", "check_failed"):
                raise HTTPException(409, f"proposal is {prop['status']}")
            await conn.execute("""
                UPDATE ops_governance.mdm_change_proposal
                   SET status = 'declined', reviewed_by = $2, reviewed_by_sub = $4::uuid,
                       reviewed_at = now(), review_note = $3
                 WHERE proposal_id = $1::uuid
            """, proposal_id, user.username, note.strip(), sub)
            await _event(conn, proposal_id, "declined", user.username, {"note": note.strip()})
            await _audit(conn, user, "mdm.decline", proposal_id,
                         {"proposed_by": prop["proposed_by"], "note": note.strip()})
    return {"proposal_id": proposal_id, "status": "declined"}


# Lists and detail
@router.get("/proposals")
async def proposals(
    user: UserCtx = Depends(get_user),
    view: str = Query(default="mine"),
    entity_class: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """mine       everything I proposed, newest first
       attention  mine that need me: check failed, expired, declined
       approvals  checked and in time, proposed by someone else (approvers)
       all        every proposal (approvers and ops)"""
    if view == "approvals":
        await _require_approver(user, "mdm approvals")
    elif view == "all":
        if not (_has(user, STEWARD_ROLES) or await is_approver(user)):
            raise HTTPException(403, "mdm proposals: stewards and approvers only")
    elif view in ("mine", "attention"):
        _require(user, STEWARD_ROLES, "mdm proposals")
    else:
        raise HTTPException(422, "view must be mine, attention, approvals or all")
    me = _person(user)
    where, args = [], []
    if view in ("mine", "attention"):
        args.append(me)
        where.append(f"p.proposed_by_sub = ${len(args)}::uuid")
    if view == "attention":
        where.append(f"{_EFFECTIVE} IN ('check_failed', 'expired', 'declined')")
    if view == "approvals":
        args.append(me)
        where.append(f"p.proposed_by_sub <> ${len(args)}::uuid")
        where.append("p.status = 'checked' AND p.valid_until >= now()")
    if entity_class:
        args.append(entity_class)
        where.append(f"p.entity_class_code = ${len(args)}")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT p.*, {_EFFECTIVE} AS effective_status
            FROM ops_governance.mdm_change_proposal p {clause}
            ORDER BY p.proposed_at DESC
            LIMIT {limit} OFFSET {offset}
        """, *args)
        total = await conn.fetchval(f"""
            SELECT count(*) FROM ops_governance.mdm_change_proposal p {clause}
        """, *args)
    return {"proposals": [_prop_out(r) for r in rows], "total": total,
            "view": view, "limit": limit, "offset": offset}


@router.get("/proposal/{proposal_id}")
async def proposal_detail(
    proposal_id: str = PathParam(...),
    user: UserCtx = Depends(get_user),
) -> dict:
    """The drawer: the proposal, its step history, and whether the caller may
    approve it (the console renders Approve only when this says so)."""
    approver = await is_approver(user)
    if not (_has(user, STEWARD_ROLES) or approver):
        raise HTTPException(403, "mdm proposals: stewards and approvers only")
    _uuid(proposal_id)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        prop = await _load(conn, proposal_id)
        events = await conn.fetch("""
            SELECT event, actor, at, detail FROM ops_governance.mdm_proposal_event
            WHERE proposal_id = $1::uuid ORDER BY at, event_id
        """, proposal_id)
    out = _prop_out(prop)
    out["events"] = [{"event": e["event"], "actor": e["actor"],
                      "at": e["at"].isoformat(), "detail": e["detail"]} for e in events]
    out["can_approve"] = (approver
                          and not _is_person(user, prop["proposed_by_sub"])
                          and prop["effective_status"] == "checked")
    out["can_withdraw"] = (_is_person(user, prop["proposed_by_sub"])
                           and prop["status"] in ("proposed", "checked", "check_failed"))
    return out
