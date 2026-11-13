"""/api/policy: admin OPA inspection and ad-hoc evaluator.

Backs the Admin dashboard's "Policies (OPA)" section:
  GET  /api/policy/recent     last N policy_log rows
  POST /api/policy/evaluate   run a what-if input doc through OPA

The evaluator uses opa.decide, so the result shape matches every other
OPA-gated call. Evaluations are audit-logged in the gateway log, not in
policy_log, so experiments are not mixed with production decisions.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from ..auth import UserCtx, get_user
from .. import opa, ops_db
# The admin gate is the RBAC capability: no rego rule matches admin.* verbs, so
# opa.decide would deny under OPA_ENFORCE. opa.decide is used only to evaluate
# the requested policy input, possibly for a synthetic principal.
from .perm_admin import _require_capability

log = logging.getLogger("gateway.policy")
router = APIRouter(prefix="/api/policy", tags=["policy"])


@router.get("/recent")
async def recent_decisions(
        user: UserCtx = Depends(get_user),
        limit: int = Query(default=50, ge=1, le=500),
        decision: str | None = Query(default=None,
                                      description="filter: allow|deny")
        ) -> list[dict[str, Any]]:
    """Most recent OPA decisions from ops_audit.policy_log. Requires
    view_ops_console, so only operators see the policy trace."""
    await _require_capability(user, "view_ops_console")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        if decision in ("allow", "deny"):
            rows = await conn.fetch("""
                SELECT id, ts, kc_sub, action, resource, decision,
                       rule_hits, explanation
                FROM ops_audit.policy_log
                WHERE decision = $1
                ORDER BY ts DESC LIMIT $2;
            """, decision, limit)
        else:
            rows = await conn.fetch("""
                SELECT id, ts, kc_sub, action, resource, decision,
                       rule_hits, explanation
                FROM ops_audit.policy_log
                ORDER BY ts DESC LIMIT $1;
            """, limit)
    return [dict(r) for r in rows]


@router.post("/evaluate")
async def evaluate(
        user: UserCtx = Depends(get_user),
        # Evaluate as the caller, or pass a `principal` doc to evaluate as
        # someone else (admin only).
        action:    str = Body(..., min_length=1),
        resource:  str = Body(..., min_length=1),
        principal: dict[str, Any] | None = Body(default=None),
        ) -> dict[str, Any]:
    """Ad-hoc policy evaluation. Takes {action, resource, principal?}, queries
    OPA exactly as the enforcement path does and returns the full decision."""
    # Evaluating as someone else needs the admin capability; self-evaluation is
    # open to any authenticated user so the SQL and export pages can pre-check.
    if principal is not None:
        await _require_capability(user, "view_ops_console")
        # Synthetic UserCtx carrying the requested identity; only the fields
        # the OPA rules consume are taken from the principal doc.
        from ..auth import UserCtx as _UC
        target = _UC(
            kc_sub=str(principal.get("kc_sub") or principal.get("sub") or ""),
            username=str(principal.get("username") or ""),
            realm=str(principal.get("realm") or ""),
            primary_role=str(principal.get("primary_role") or
                              (principal.get("roles") or ["R-UNKNOWN"])[0]),
            roles=list(principal.get("roles") or []),
            geographies=list(principal.get("geographies") or []),
            programs=list(principal.get("programs") or []),
            purpose_of_use=str(principal.get("purpose_of_use") or "public-health"),
        )
    else:
        target = user

    decision = await opa.decide(target, action=action, resource=resource)
    # Audited in the gateway log, not in the production policy_log.
    await ops_db.log_gateway_query(
        user=user, method="POST", path="/api/policy/evaluate",
        mart_scope=["opa.evaluate"],
        filters={"action": action, "resource": resource,
                 "as_other": principal is not None},
        row_count=1, byte_count=None,
        latency_ms=0, http_status=200,
        policy_decision="allow")
    return {
        "allow":           decision.allow,
        "reason":          decision.reason,
        "rule_hits":       decision.rule_hits,
        "obligations":     decision.obligations,
        "masked_columns":  decision.masked_columns,
        "evaluated_for":   {
            "kc_sub":       target.kc_sub,
            "primary_role": target.primary_role,
            "roles":        target.roles,
            "geographies":  target.geographies,
            "programs":     target.programs,
        },
    }
