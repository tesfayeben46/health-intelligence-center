"""OPA decision client: one call to the nhdw.authz decide rule.

Denials are advisory (logged and allowed) unless OPA_ENFORCE=true.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from .auth import UserCtx
from .settings import settings

log = logging.getLogger("gateway.opa")


@dataclass
class Decision:
    allow: bool
    reason: str = ""
    degraded: bool = False
    masked_columns: list[str] = field(default_factory=list)
    rule_hits: list[str] = field(default_factory=list)
    obligations: dict[str, Any] = field(default_factory=dict)
    row_filter: dict[str, Any] = field(default_factory=dict)   # {dimension: [allowed values]}

    def __bool__(self) -> bool:
        return self.allow


async def decide(user: UserCtx, *, action: str, resource: str,
                 attributes: dict[str, Any] | None = None,
                 scope: dict[str, list[str]] | None = None,
                 columns: dict[str, str] | None = None) -> Decision:
    """Ask OPA for an authorization decision.

    `scope` is the caller's granted data scope from ops_identity.user_scope as
    {kind: [values]}, kind being geography, programme or facility. It is passed
    in the OPA context so policies return a `row_filter` obligation, which the
    enforcement point ANDs into the actual query. This makes granted scope a
    row-level control rather than a UI filter.

    `columns` is the queried relation's classified columns as
    {name: sensitivity}, from mart_attrs.columns_for_mart. Unclassified columns
    are never sent and never masked. The policy returns the columns the caller
    may not see unmasked in `masked_columns`; the enforcement point transforms
    each one by its mask_class (app/masking.py). If `columns` is omitted, no
    column reaches OPA and nothing is masked, so callers reading classified
    marts must pass it.
    """
    sc = scope or {}
    # user_scope values may be sets, which are not JSON-serialisable; normalise
    # every scope dimension to a sorted list.
    def _lst(v) -> list[str]:
        if v is None:
            return []
        return sorted(v) if isinstance(v, (set, frozenset)) else list(v)
    payload = {
        "input": {
            "user":   {"role": user.primary_role, "name": user.username,
                       "realm": user.realm, "sub": user.kc_sub,
                       # Trust tier: the OPA trust guard denies sensitive
                       # actions to low-trust federated identities.
                       "idp": user.idp, "idp_trust": user.idp_trust},
            "action": {"verb": action},
            "resource": {"subject": resource, **(attributes or {}),
                         "columns": [{"name": n, "sensitivity": s}
                                     for n, s in sorted((columns or {}).items())]},
            "context": {
                "purpose_of_use": user.purpose_of_use,
                "geographies":    _lst(sc.get("geography")) or _lst(user.geographies),
                "programs":       _lst(sc.get("programme")) or _lst(user.programs),
                "facilities":     _lst(sc.get("facility")),
            },
        }
    }
    try:
        async with httpx.AsyncClient(timeout=settings.OPA_TIMEOUT_S) as c:
            resp = await c.post(f"{settings.OPA_URL}/v1/data/nhdw/authz/decide",
                                json=payload)
        if resp.status_code != 200:
            log.warning("opa http %s; fail %s", resp.status_code,
                        "open" if settings.OPA_FAIL_OPEN else "closed")
            return Decision(allow=settings.OPA_FAIL_OPEN,
                            reason=f"opa http {resp.status_code}", degraded=True)
        body = (resp.json() or {}).get("result", {})
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError,
            httpx.ConnectTimeout, httpx.PoolTimeout) as e:
        # OPA unreachable: fail closed unless OPA_FAIL_OPEN=true, independent
        # of OPA_ENFORCE.
        log.warning("opa unreachable (%s); fail %s", e,
                    "open" if settings.OPA_FAIL_OPEN else "closed")
        return Decision(allow=settings.OPA_FAIL_OPEN,
                        reason=f"opa unreachable: {e}", degraded=True)

    obligations = dict(body.get("obligations") or {})
    d = Decision(
        allow=bool(body.get("allow", False)),
        reason=str(body.get("reason", "")),
        masked_columns=list(body.get("masked_columns") or []),
        rule_hits=list(body.get("rule_hits") or []),
        obligations=obligations,
        row_filter=dict(obligations.get("row_filter") or {}),
    )
    # Advisory mode (OPA_ENFORCE=false, for local development): a deny is logged
    # as a warning and turned into an allow. Deployed gateways set
    # OPA_ENFORCE=true, so denials become 403s.
    if not d.allow and not settings.OPA_ENFORCE:
        log.warning("OPA DENY -> ALLOW (advisory; OPA_ENFORCE=false) "
                    "role=%s action=%s resource=%s reason=%s",
                    user.primary_role, action, resource, d.reason)
        d.allow = True
        d.reason = d.reason + " [advisory]"
    return d
