"""OPA decision client for the Public Dashboard.

Every /api/series/{slug} call routes through `opa_check()` before any
mart query runs. The role is always R-PUB-ANON; the resource subject is
mapped per slug in SERIES_SUBJECT. Decisions come from the shared
nhdw/authz/decide endpoint.

Enforcement behaviour:
- OPA_ENFORCE=true: an OPA deny returns 403; OPA unreachable also
  denies unless OPA_FAIL_OPEN=true.
- OPA_ENFORCE=false: denies are logged to the audit stream but data
  still flows. Used only for local bootstrap.
"""
from __future__ import annotations

import logging
import os

import httpx

OPA_URL = os.environ.get("OPA_URL", "http://opa:8181")
OPA_TIMEOUT_S = float(os.environ.get("OPA_TIMEOUT_S", "2.5"))
OPA_ENFORCE = os.environ.get("OPA_ENFORCE", "false").lower() == "true"
OPA_FAIL_OPEN = os.environ.get("OPA_FAIL_OPEN", "false").lower() == "true"

log = logging.getLogger("opa")

# Per-slug resource subject. An unmapped slug is sent as subject=UNKNOWN and
# is denied (403 in enforce mode), so a new public slug cannot be served
# without being added here explicitly.
SERIES_SUBJECT: dict[str, str] = {
    "national-coverage":   "PRG",
    "facility-counts":     "FAC",
    "stock-availability":  "PRG",
}


class OpaDecision:
    __slots__ = ("allow", "reason", "degraded")

    def __init__(self, allow: bool, reason: str, degraded: bool = False) -> None:
        self.allow = allow
        self.reason = reason
        self.degraded = degraded

    def __bool__(self) -> bool:
        return self.allow


async def opa_check(*, slug: str, client_ip: str) -> OpaDecision:
    """Ask OPA whether the public dashboard can serve `slug` to an anon."""
    subject = SERIES_SUBJECT.get(slug, "UNKNOWN")
    payload = {
        "input": {
            "user":     {"role": "R-PUB-ANON", "name": "anonymous", "realm": "public"},
            "action":   {"verb": "read"},
            "resource": {"subject": subject, "granularity": "aggregate",
                         "sensitivity": "default", "data_class": "DC.PRO"},
            "context":  {"purpose_of_use": "public-health", "client_ip": client_ip},
        }
    }
    try:
        async with httpx.AsyncClient(timeout=OPA_TIMEOUT_S) as c:
            resp = await c.post(f"{OPA_URL}/v1/data/nhdw/authz/decide", json=payload)
            if resp.status_code != 200:
                log.warning("opa http %s: failing degraded", resp.status_code)
                return OpaDecision(OPA_FAIL_OPEN, f"opa http {resp.status_code}", degraded=True)
            body = (resp.json() or {}).get("result", {})
            decision = OpaDecision(
                allow=bool(body.get("allow", False)),
                reason=str(body.get("reason", "")),
            )
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
        log.warning("opa unreachable (%s): failing degraded", e)
        return OpaDecision(OPA_FAIL_OPEN, f"opa unreachable: {e}", degraded=True)

    if not decision.allow and not OPA_ENFORCE:
        log.warning("OPA would deny slug=%s subject=%s reason=%s: enforce off, letting through",
                    slug, subject, decision.reason)
        return OpaDecision(True, decision.reason + " [enforce-off]")
    return decision
