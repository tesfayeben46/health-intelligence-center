"""/api/alerts: alerting endpoints.

Backs the admin dashboard's "Alert routing" section:
  GET /api/alerts/active   active alerts from Prometheus' /api/v1/alerts
                            endpoint (firing + pending).
  GET/POST/PATCH/DELETE /api/alerts/routing  admin-managed routing rules
                            (severity + match_label -> channel/recipient),
                            backed by ops_governance.alert_routing.

It also serves per-user alert subscriptions, the alert evaluator and fired
notifications (sections below). Admin routes are gated by the view_ops_console
capability; every request is recorded in ops_audit.gateway_log.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
from asyncpg.exceptions import UndefinedTableError
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..auth import UserCtx, get_user
from ..settings import settings
from ..fetchers import _ml_latest_run
from .. import ops_db, trino_client, mailer
# Admin routes are gated by RBAC capability, not opa.decide: there is no Rego
# rule for admin.* verbs, so opa.decide would deny them under OPA_ENFORCE.
from .perm_admin import _require_capability

log = logging.getLogger("gateway.alerts")
router = APIRouter(prefix="/api/alerts", tags=["alerts"])

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
ALERTMANAGER_URL = os.environ.get("ALERTMANAGER_URL", "")

# Alert-routing CRUD on ops_governance.alert_routing (29_alert_routing.sql).
# A rule maps an alert selector (severity plus a match_label such as
# `service=gateway` or `kind=dq.test`, matched against the alert's labels) to a
# delivery channel and recipient. Reads and writes require view_ops_console
# (held by R-OPS-ADM, R-OPS-SEC and R-SUPER). Writes set managed_by='runtime' so
# a cold-boot re-seed of the platform defaults never overwrites an operator's
# edit.

class RoutingRuleIn(BaseModel):
    severity:    str = Field(pattern="^(critical|warning|info)$")
    match_label: str = Field(min_length=1, max_length=120)
    channel:     str = Field(min_length=1, max_length=120)
    recipient:   str = Field(min_length=1, max_length=200)
    note:        str = Field(default="", max_length=400)
    enabled:     bool = True


class RoutingRulePatch(BaseModel):
    severity:    str | None = Field(default=None, pattern="^(critical|warning|info)$")
    match_label: str | None = Field(default=None, min_length=1, max_length=120)
    channel:     str | None = Field(default=None, min_length=1, max_length=120)
    recipient:   str | None = Field(default=None, min_length=1, max_length=200)
    note:        str | None = Field(default=None, max_length=400)
    enabled:     bool | None = None


def _shape_routing(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id":          r["id"],
        "severity":    r["severity"],
        "match_label": r["match_label"],
        "channel":     r["channel"],
        "recipient":   r["recipient"],
        "note":        r["note"],
        "enabled":     r["enabled"],
        "managed_by":  r["managed_by"],
        "updated_at":  r["updated_at"].isoformat() if r.get("updated_at") else None,
    }


@router.get("/active")
async def active_alerts(user: UserCtx = Depends(get_user),
                        limit: int = Query(default=50, ge=1, le=500)
                        ) -> dict[str, Any]:
    """Active alerts from Prometheus /api/v1/alerts. Returns the raw shape
    (group, labels, annotations, state, firing since) plus a short summary
    table the admin UI renders directly."""
    await _require_capability(user, "view_ops_console")

    alerts: list[dict[str, Any]] = []
    err: str | None = None
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{PROMETHEUS_URL}/api/v1/alerts")
        if r.status_code == 200:
            data = (r.json() or {}).get("data") or {}
            alerts = data.get("alerts") or []
    except httpx.RequestError as e:
        err = str(e)

    summary = []
    for a in alerts[:limit]:
        labels = a.get("labels") or {}
        ann = a.get("annotations") or {}
        summary.append({
            "name":          labels.get("alertname") or "(unknown)",
            "severity":      labels.get("severity") or "info",
            "service":       labels.get("service") or labels.get("job") or "",
            "state":         a.get("state"),
            "active_since":  a.get("activeAt") or a.get("startsAt"),
            "summary":       ann.get("summary") or ann.get("description") or "",
        })

    await ops_db.log_gateway_query(
        user=user, method="GET", path="/api/alerts/active",
        mart_scope=["prometheus.alerts"], filters={},
        row_count=len(summary), byte_count=None,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        http_status=200 if err is None else 502,
        policy_decision="allow", denied_reason=err)
    return {"alerts": summary, "raw_count": len(alerts),
            "source": PROMETHEUS_URL, "error": err}


@router.get("/routing")
async def routing_rules(user: UserCtx = Depends(get_user)
                        ) -> list[dict[str, Any]]:
    """All alert-routing rules from ops_governance.alert_routing, ordered by
    severity (critical, warning, info) then id."""
    await _require_capability(user, "view_ops_console")
    try:
        pool = await ops_db.ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, severity, match_label, channel, recipient, note,
                       enabled, managed_by, updated_at
                FROM ops_governance.alert_routing
                ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1
                                       ELSE 2 END, id;
            """)
    except UndefinedTableError:
        # 29_alert_routing.sql not applied yet (cold boot): return an empty
        # rule set so the widget shows "no rules" instead of an error.
        log.warning("ops_governance.alert_routing missing; returning empty routing set")
        return []
    return [_shape_routing(dict(r)) for r in rows]


@router.post("/routing")
async def create_routing_rule(
        user: UserCtx = Depends(get_user),
        body: RoutingRuleIn = Body(...)) -> dict[str, Any]:
    """Create an operator-authored routing rule (managed_by='runtime')."""
    await _require_capability(user, "view_ops_console")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO ops_governance.alert_routing
                (severity, match_label, channel, recipient, note, enabled, managed_by)
            VALUES ($1, $2, $3, $4, $5, $6, 'runtime')
            RETURNING id, severity, match_label, channel, recipient, note,
                      enabled, managed_by, updated_at;
        """, body.severity, body.match_label, body.channel, body.recipient,
             body.note, body.enabled)
    return _shape_routing(dict(row))


@router.patch("/routing/{rule_id}")
async def update_routing_rule(
        rule_id: str, user: UserCtx = Depends(get_user),
        body: RoutingRulePatch = Body(...)) -> dict[str, Any]:
    """Edit a routing rule. Any edit sets managed_by='runtime' so the cold-boot
    seed upsert leaves the row alone."""
    await _require_capability(user, "view_ops_console")
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(400, "no fields to update")
    cols = list(fields.keys())
    set_sql = ", ".join(f"{c} = ${i+2}" for i, c in enumerate(cols))
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"""
            UPDATE ops_governance.alert_routing
               SET {set_sql}, managed_by = 'runtime', updated_at = now()
             WHERE id = $1
            RETURNING id, severity, match_label, channel, recipient, note,
                      enabled, managed_by, updated_at;
        """, rule_id, *[fields[c] for c in cols])
    if row is None:
        raise HTTPException(404, "routing rule not found")
    return _shape_routing(dict(row))


@router.delete("/routing/{rule_id}")
async def delete_routing_rule(
        rule_id: str, user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    """Delete a routing rule (seed or runtime)."""
    await _require_capability(user, "view_ops_console")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        gone = await conn.fetchval("""
            DELETE FROM ops_governance.alert_routing WHERE id = $1 RETURNING id;
        """, rule_id)
    if gone is None:
        raise HTTPException(404, "routing rule not found")
    return {"id": gone, "deleted": True}


# Alert subscriptions: per-user CRUD on ops_workspace.subscription.
# A subscription is a self-subscription: "notify me when <metric> for
# <disease, geography> crosses <threshold>". The recipient is always the owner's
# own email, looked up by the evaluator, never an arbitrary address, so the
# feature cannot send mail to unverified recipients. The disease widgets'
# "Subscribe" button and the Settings alert manager (st-alert-subs) use these
# routes. Fired notifications are written to ops_workspace.alert_notification.

class AlertSubIn(BaseModel):
    disease:   str   = Field(default="all", max_length=60)
    geography: str   = Field(default="all", max_length=80)
    metric:    str   = Field(default="alarm_probability", max_length=60)
    op:        str   = Field(default=">=", pattern=r"^(>=|>|<=|<)$")
    threshold: float = Field(ge=0, le=1)
    cadence:   str   = Field(default="realtime",
                             pattern="^(realtime|daily|weekly)$")


class AlertSubPatch(BaseModel):
    status: str = Field(pattern="^(active|paused)$")


def _signature(disease: str, geography: str, metric: str) -> str:
    """Stable identity for deduplication: one subscription per (owner, disease,
    geography, metric). Re-subscribing the same scope updates the row."""
    return f"disease:{disease}|geo:{geography}|metric:{metric}"


def _shape_sub(r: dict[str, Any]) -> dict[str, Any]:
    import json as _json
    spec = r.get("trigger_spec")
    if isinstance(spec, str):
        try: spec = _json.loads(spec)
        except Exception: spec = {}
    spec = spec or {}
    return {
        "id":            str(r["id"]),
        "disease":       spec.get("disease", "all"),
        "geography":     spec.get("geography", "all"),
        "metric":        spec.get("metric", "alarm_probability"),
        "op":            spec.get("op", ">="),
        "threshold":     spec.get("threshold"),
        "cadence":       spec.get("cadence", "realtime"),
        "channels":      list(r.get("channels") or ["email"]),
        "status":        r["status"],
        "created_at":    r["created_at"].isoformat() if r.get("created_at") else None,
        "last_fired_at": r["last_fired_at"].isoformat() if r.get("last_fired_at") else None,
        "fire_count":    r.get("fire_count", 0),
    }


@router.get("/subscriptions")
async def list_my_subscriptions(
        user: UserCtx = Depends(get_user),
        limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
    """The caller's own alert subscriptions (owner = kc_sub), never another
    user's. Returns [] for an anonymous session rather than 401, so the
    Settings widget shows an empty state."""
    if not user.kc_sub:
        return []
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, trigger_spec, channels, status,
                   created_at, last_fired_at, fire_count
            FROM ops_workspace.subscription
            WHERE owner = $1::uuid
            ORDER BY created_at DESC
            LIMIT $2;
        """, user.kc_sub, limit)
    return [_shape_sub(dict(r)) for r in rows]


@router.post("/subscriptions")
async def create_subscription(
        user: UserCtx = Depends(get_user),
        body: AlertSubIn = Body(...)) -> dict[str, Any]:
    if not user.kc_sub:
        raise HTTPException(401, "authenticated session required")
    import json as _json
    sig = _signature(body.disease, body.geography, body.metric)
    trigger = {"disease": body.disease, "geography": body.geography,
               "metric": body.metric, "op": body.op,
               "threshold": body.threshold, "cadence": body.cadence}
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        # Upsert on (owner, widget_signature): re-subscribing the same scope
        # updates threshold and cadence and reactivates the row.
        row = await conn.fetchrow("""
            INSERT INTO ops_workspace.subscription
                (owner, widget_signature, trigger_spec, channels, status, created_at)
            VALUES ($1::uuid, $2, $3::jsonb, '{email}', 'active', now())
            ON CONFLICT (owner, widget_signature) DO UPDATE
              SET trigger_spec = EXCLUDED.trigger_spec, status = 'active'
            RETURNING id, trigger_spec, channels, status,
                      created_at, last_fired_at, fire_count;
        """, user.kc_sub, sig, _json.dumps(trigger))
    return _shape_sub(dict(row))


@router.patch("/subscriptions/{sub_id}")
async def set_subscription_status(
        sub_id: str, user: UserCtx = Depends(get_user),
        body: AlertSubPatch = Body(...)) -> dict[str, Any]:
    """Pause or resume an alert (owner-scoped)."""
    if not user.kc_sub:
        raise HTTPException(401, "authenticated session required")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        st = await conn.fetchval("""
            UPDATE ops_workspace.subscription
               SET status = $3
             WHERE id = $1::uuid AND owner = $2::uuid
             RETURNING status;
        """, sub_id, user.kc_sub, body.status)
    if st is None:
        raise HTTPException(404, "not found or not yours")
    return {"id": sub_id, "status": st}


@router.delete("/subscriptions/{sub_id}")
async def delete_subscription(
        sub_id: str, user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    if not user.kc_sub:
        raise HTTPException(401, "authenticated session required")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        n = await conn.fetchval("""
            DELETE FROM ops_workspace.subscription
             WHERE id = $1::uuid AND owner = $2::uuid
             RETURNING 1;
        """, sub_id, user.kc_sub)
    if n is None:
        raise HTTPException(404, "not found or not yours")
    return {"id": sub_id, "deleted": True}


# Alert evaluation and fired notifications.
# For each (disease_code, region_id) the evaluator takes the peak alarm
# probability at the latest forecast week, the same signal the dz-* disease
# widgets render (max alarm_prob over the region's woredas, so any woreda in
# alarm flags the region). Each active subscription is matched against it; a
# crossing writes an owner-scoped ops_workspace.alert_notification (idempotent
# per forecast week through the uq_alert_notification_dedup unique index) and
# emails the owner, best-effort. The alert_evaluation Airflow DAG runs it after
# ml_batch_predict; admins can also trigger it on demand.
#
# Vocabulary mapping (widget, model and subscription keys differ):
#   subscription.disease    UI key ('cholera', 'malaria', ...) or mart code -> mart code
#   subscription.geography  region name ('Oromia', ...) or region_id -> region_id
#   mart_idsr_disease_weekly maps woreda_uid to region_id; ml_predictions keys
#   alarm_prob by (disease=code, district=woreda_uid).

# UI disease key -> mart disease_code (only diseases with a surveillance forecast).
_DISEASE_CODE = {
    "cholera": "CHOL", "awd": "CHOL", "measles": "MEA", "malaria": "MAL",
    "meningitis": "MEN", "dengue": "DENG", "yellowfever": "YF",
    "typhoid": "TYPH", "tetanus": "NNT",
}
# mart disease_code -> display label (notification message).
_CODE_LABEL = {
    "CHOL": "Cholera (AWD)", "MEA": "Measles", "MAL": "Malaria",
    "MEN": "Meningitis", "DENG": "Dengue fever", "YF": "Yellow fever",
    "TYPH": "Typhoid fever", "NNT": "Neonatal tetanus",
    "TB": "Tuberculosis", "RELF": "Relapsing fever",
}
# SPA region name -> mart region_id (two-letter org-unit code).
_REGION_ID = {
    "Addis Ababa": "AA", "Afar": "AF", "Amhara": "AM", "Benishangul Gumuz": "BG",
    "Central Ethiopia": "CE", "Dire Dawa": "DD", "Gambella": "GA",
    "Harari": "HA", "Oromia": "OR", "Sidama": "SI", "Somali": "SO",
    "South Ethiopia": "SE", "South West Ethiopia": "SW", "Tigray": "TI",
}
_REGION_NAME = {v: k for k, v in _REGION_ID.items()}


def _resolve_codes(token: str, present: set[str]) -> list[str]:
    """Map a subscription disease token to the mart disease_code(s) that have a
    signal: 'all' gives every disease present; a UI key or raw code gives that
    code; an unmappable token gives [], so the subscription never fires."""
    if not token or token == "all":
        return sorted(present)
    code = _DISEASE_CODE.get(token) or (
        token.upper() if token.upper() in _CODE_LABEL else None)
    return [code] if code and code in present else []


def _resolve_regions(token: str, present: set[str]) -> list[str]:
    if not token or token == "all":
        return sorted(present)
    reg = _REGION_ID.get(token) or (
        token.upper() if token.upper() in _REGION_NAME else None)
    return [reg] if reg and reg in present else []


def _cmp(op: str, value: float, thr: float) -> bool:
    return {">=": value >= thr, ">": value > thr,
            "<=": value <= thr, "<": value < thr}.get(op, value >= thr)


async def _alarm_signal() -> tuple[dict[tuple[str, str], tuple[float, int]],
                                   set[str], set[str]]:
    """Latest-week peak alarm probability per (disease_code, region_id), read
    through Trino across the marts and ops catalogs, as the dz-* fetchers do.
    Returns (cells, diseases_present, regions_present)."""
    model = settings.ALERT_EVAL_MODEL or "ears"
    sql = (
        f"WITH ml AS ({_ml_latest_run(model)}), "
        f"mlx AS (SELECT max(year * 100 + week) AS yw FROM ml), "
        f"scope AS (SELECT DISTINCT woreda_uid, region_id, disease_code "
        f'FROM marts.bi_idsr."mart_idsr_disease_weekly") '
        f"SELECT s.disease_code AS disease_code, s.region_id AS region_id, "
        f"max(m.alarm_prob) AS alarm_prob, "
        f"max(m.year * 100 + m.week) AS signal_period "
        f"FROM ml m CROSS JOIN mlx "
        f"JOIN scope s ON s.woreda_uid = m.district AND s.disease_code = m.disease "
        f"WHERE m.year * 100 + m.week = mlx.yw GROUP BY 1, 2")
    res = await trino_client.query(sql)
    cells: dict[tuple[str, str], tuple[float, int]] = {}
    diseases: set[str] = set()
    regions: set[str] = set()
    for r in res.get("rows", []):
        code, reg = r.get("disease_code"), r.get("region_id")
        if code is None or reg is None:
            continue
        prob = float(r["alarm_prob"]) if r.get("alarm_prob") is not None else 0.0
        period = int(r["signal_period"]) if r.get("signal_period") is not None else 0
        cells[(code, reg)] = (prob, period)
        diseases.add(code)
        regions.add(reg)
    return cells, diseases, regions


async def _run_evaluation() -> dict[str, Any]:
    """Match every active subscription against the latest alarm signal and
    write notifications, idempotent per forecast week, with a best-effort
    email per notification."""
    import json as _json
    cells, diseases, regions = await _alarm_signal()
    evaluated = fired = emailed = 0
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        subs = await conn.fetch("""
            SELECT s.id, s.owner, s.trigger_spec, s.channels,
                   u.email, u.full_name
            FROM ops_workspace.subscription s
            JOIN ops_identity."user" u ON u.kc_sub = s.owner
            WHERE s.status = 'active';
        """)
        for s in subs:
            evaluated += 1
            spec = s["trigger_spec"]
            if isinstance(spec, str):
                try: spec = _json.loads(spec)
                except Exception: spec = {}
            spec = spec or {}
            thr = spec.get("threshold")
            if thr is None:
                continue
            thr = float(thr)
            op = spec.get("op", ">=")
            codes = _resolve_codes(spec.get("disease", "all"), diseases)
            regs = _resolve_regions(spec.get("geography", "all"), regions)
            want_email = "email" in list(s["channels"] or ["email"])
            any_new = False
            for code in codes:
                for reg in regs:
                    cell = cells.get((code, reg))
                    if not cell:
                        continue
                    prob, period = cell
                    if not _cmp(op, prob, thr):
                        continue
                    dlabel = _CODE_LABEL.get(code, code)
                    rname = _REGION_NAME.get(reg, reg)
                    yr, wk = period // 100, period % 100
                    msg = (f"{dlabel} alarm probability reached {round(prob * 100)}% "
                           f"in {rname} (threshold {op} {round(thr * 100)}%), "
                           f"forecast week {yr}-W{wk:02d}.")
                    row = await conn.fetchrow("""
                        INSERT INTO ops_workspace.alert_notification
                          (subscription_id, owner, disease, geography, metric,
                           metric_value, threshold, signal_period, message)
                        VALUES ($1, $2, $3, $4, 'alarm_probability',
                                $5, $6, $7, $8)
                        ON CONFLICT (subscription_id, disease, geography, signal_period)
                        DO NOTHING
                        RETURNING id;
                    """, s["id"], s["owner"], code, reg, prob, thr, period, msg)
                    if row is None:
                        continue                       # already fired this week
                    fired += 1
                    any_new = True
                    if want_email and s["email"]:
                        ok = await mailer.send(
                            s["email"],
                            f"[NHDW alert] {dlabel}; {rname}",
                            f"Hello {s['full_name'] or ''},\n\n{msg}\n\n"
                            f"You are receiving this because you subscribed to "
                            f"disease-surveillance alerts on the NHDW dashboard.\n")
                        if ok:
                            emailed += 1
                            await conn.execute(
                                "UPDATE ops_workspace.alert_notification "
                                "SET emailed = true WHERE id = $1;", row["id"])
            if any_new:
                await conn.execute("""
                    UPDATE ops_workspace.subscription
                       SET fire_count = fire_count + 1, last_fired_at = now()
                     WHERE id = $1;
                """, s["id"])
    return {"subscriptions_evaluated": evaluated, "cells": len(cells),
            "notifications_fired": fired, "emails_sent": emailed}


def _service_authorized(x_eval_token: str | None) -> bool:
    tok = settings.ALERT_EVAL_TOKEN
    return bool(tok) and x_eval_token == tok


@router.post("/evaluate")
async def evaluate_alerts(
        request: Request,
        x_eval_token: str | None = Header(default=None, alias="X-Eval-Token")
        ) -> dict[str, Any]:
    """Evaluate every active subscription against the latest forecast and write
    notifications. Authorised by the service token (the Airflow
    alert_evaluation DAG, no bearer) or by an admin bearer with the
    view_ops_console capability. Idempotent per forecast week."""
    if not _service_authorized(x_eval_token):
        # No valid service token: require an admin bearer with view_ops_console.
        # A 401 from get_user and a 403 from _require_capability both become
        # one 403.
        try:
            user = await get_user(
                request,
                authorization=request.headers.get("authorization"),
                x_mock_user=request.headers.get("x-mock-user"),
                x_mock_role=request.headers.get("x-mock-role"))
            await _require_capability(user, "view_ops_console")
        except HTTPException:
            raise HTTPException(403, "admin or valid service token required")
    t0 = time.perf_counter()
    summary = await _run_evaluation()
    log.info("alert evaluation %s", summary)
    return {**summary, "latency_ms": int((time.perf_counter() - t0) * 1000)}


def _shape_notif(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id":            str(r["id"]),
        "disease":       r.get("disease"),
        "disease_label": _CODE_LABEL.get(r.get("disease"), r.get("disease")),
        "geography":     r.get("geography"),
        "region_name":   _REGION_NAME.get(r.get("geography"), r.get("geography")),
        "metric":        r.get("metric"),
        "value":         float(r["metric_value"]) if r.get("metric_value") is not None else None,
        "threshold":     float(r["threshold"]) if r.get("threshold") is not None else None,
        "signal_period": r.get("signal_period"),
        "message":       r.get("message"),
        "fired_at":      r["fired_at"].isoformat() if r.get("fired_at") else None,
        "read_at":       r["read_at"].isoformat() if r.get("read_at") else None,
        "read":          r.get("read_at") is not None,
        "emailed":       r.get("emailed", False),
    }


@router.get("/notifications")
async def list_notifications(
        user: UserCtx = Depends(get_user),
        unread_only: bool = Query(default=False),
        limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    """The caller's fired alert notifications (owner = kc_sub) and the unread
    count for the topbar bell. Anonymous sessions get an empty result."""
    if not user.kc_sub:
        return {"notifications": [], "unread_count": 0}
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        unread = await conn.fetchval(
            "SELECT count(*) FROM ops_workspace.alert_notification "
            "WHERE owner = $1::uuid AND read_at IS NULL;", user.kc_sub)
        q = ("SELECT id, disease, geography, metric, metric_value, threshold, "
             "signal_period, message, fired_at, read_at, emailed "
             "FROM ops_workspace.alert_notification WHERE owner = $1::uuid ")
        if unread_only:
            q += "AND read_at IS NULL "
        q += "ORDER BY fired_at DESC LIMIT $2;"
        rows = await conn.fetch(q, user.kc_sub, limit)
    return {"notifications": [_shape_notif(dict(r)) for r in rows],
            "unread_count": int(unread or 0)}


@router.post("/notifications/{notif_id}/read")
async def mark_notification_read(
        notif_id: str, user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    if not user.kc_sub:
        raise HTTPException(401, "authenticated session required")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        n = await conn.fetchval("""
            UPDATE ops_workspace.alert_notification
               SET read_at = now()
             WHERE id = $1::uuid AND owner = $2::uuid AND read_at IS NULL
             RETURNING 1;
        """, notif_id, user.kc_sub)
    return {"id": notif_id, "read": True, "changed": n is not None}


@router.post("/notifications/read-all")
async def mark_all_notifications_read(
        user: UserCtx = Depends(get_user)) -> dict[str, Any]:
    if not user.kc_sub:
        raise HTTPException(401, "authenticated session required")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        status = await conn.execute("""
            UPDATE ops_workspace.alert_notification
               SET read_at = now()
             WHERE owner = $1::uuid AND read_at IS NULL;
        """, user.kc_sub)
    marked = int(status.split()[-1]) if status else 0
    return {"marked": marked}
