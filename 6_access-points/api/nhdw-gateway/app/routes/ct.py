"""/api/ct: Supply Control Tower plan.

GET /api/ct/plan?period=<period_id>

Returns one nested JSON object with the same shape as the exports of the
internal-BI CT page's data/plan.ts, sourced live from the bi_supply marts
(mart_ct_nodes / mart_ct_movements / mart_ct_vehicle_runs / mart_ct_plan_stats)
via Trino:

    { regions, regionNames, periods, currentPeriod, commodities, warehouses,
      nodes, movements, vehicleRuns, selectedId, periodStats, summary, baseline }

OPA is enforced here, because the CT React islands call this route rather than
the per-widget fetcher path: subject=PRD, data_class=DC.STK,
granularity=aggregate. The caller's scope is sent to OPA and the returned
geography row_filter is ANDed into the movement and run rows (region_id), as in
the supply fetchers. Warehouses are returned whole; facilities are limited to
the caller's scope (see below).
"""
from __future__ import annotations

import json
import re
import time
from uuid import UUID
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path as PathParam, Query

from ..auth import UserCtx, get_user
from .. import mart_attrs, masking, opa, ops_db, permissions, trino_client

router = APIRouter(prefix="/api/ct", tags=["ct"])

_SCHEMA = "marts.bi_supply"
_BARE_SCHEMA = _SCHEMA.split(".", 1)[1]
_OPA_ATTRS = {"data_class": "DC.STK", "granularity": "aggregate", "sensitivity": "default"}

# Widget-to-mart bindings for the CT widgets, which this route serves instead of
# the fetchers.py registry. Declaration only (serving uses the queries below):
# bundle/scripts/gen-exposures.py parses it statically so dbt exposures and the
# OpenMetadata dashboard connector see these widgets. It must be a pure literal
# and must list every mart that ct_plan() reads for each widget
# (bundle/scripts/test_gen_exposures.py enforces the second rule).
WIDGET_MARTS = {
    "ct-filters":  [("bi_supply", "mart_ct_nodes"), ("bi_supply", "mart_ct_movements"),
                    ("bi_supply", "mart_ct_plan_stats")],
    "ct-kpis":     [("bi_supply", "mart_ct_movements"), ("bi_supply", "mart_pct_kpi")],
    "ct-movement": [("bi_supply", "mart_ct_movements"), ("bi_supply", "mart_ct_nodes")],
    "ct-dispatch": [("bi_supply", "mart_ct_vehicle_runs"), ("bi_supply", "mart_ct_movements"),
                    ("bi_supply", "mart_ct_nodes")],
}


# Coercion helpers: Trino returns Decimal, int and str; plan.ts expects number and bool.
def _f(v):
    return None if v is None else float(v)


def _i(v):
    return None if v is None else int(v)


def _b(v):
    return None if v is None else bool(v)


def _j(v, default):
    if v is None:
        return default
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return default


def _sql_in(values: list[str]) -> str:
    safe = ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)
    return f"({safe})"


def _warehouse(r: dict) -> dict:
    return {"id": r["node_id"], "name": r["name"], "short": r.get("short_name"),
            "zone": r.get("zone"), "lng": _f(r.get("lng")), "lat": _f(r.get("lat")),
            "hub": _b(r.get("is_hub")), "vehicles": _i(r.get("vehicles")),
            "locationBasis": r.get("location_basis")}


def _node(r: dict) -> dict:
    return {"id": r["node_id"], "name": r["name"], "region": r.get("region"),
            "type": r.get("node_type"), "lng": _f(r.get("lng")), "lat": _f(r.get("lat")),
            "lineCount": _i(r.get("line_count")), "storeCap": _i(r.get("store_cap")),
            "regionId": r.get("region_id"), "locationBasis": r.get("location_basis")}


def _movement(r: dict) -> dict:
    return {
        "id": r["movement_id"], "period": r["period_id"], "fromId": r["from_hub_id"],
        "toId": r["to_node_id"], "drug": r.get("drug"), "drugShort": r.get("drug_short"),
        "commodityKey": r.get("commodity_key"), "units": _i(r.get("units")),
        "amc": _i(r.get("amc")), "mos": _f(r.get("mos")),
        "daysToStockout": _i(r.get("days_to_stockout")), "priority": _i(r.get("priority")),
        "laneKm": _i(r.get("lane_km")), "costUnits": _i(r.get("cost_units")),
        "sequenceIndex": _i(r.get("sequence_index")), "vehicleRunId": r.get("vehicle_run_id"),
        "fillRate": _f(r.get("fill_rate")), "nearestHubId": r.get("nearest_hub_id"),
        "altHubId": r.get("alt_hub_id"), "reason": r.get("reason"),
        "assignDeltaKm": _i(r.get("assign_delta_km")), "assignDeltaCost": _i(r.get("assign_delta_cost")),
        "baselineHubId": r.get("baseline_hub_id"), "baselineKm": _i(r.get("baseline_km")),
        "planned": _b(r.get("planned")), "repaired": _b(r.get("repaired")),
        "capped": _b(r.get("capped")), "why": _j(r.get("why_json"), {}),
        "atRisk": _b(r.get("at_risk")), "demandBasis": r.get("demand_basis"),
        "forecastP10": _i(r.get("forecast_p10")), "forecastP90": _i(r.get("forecast_p90")),
        "forecastVersion": r.get("forecast_model_version"), "forecastRun": r.get("forecast_mlflow_run_id"),
    }


def _run(r: dict) -> dict:
    return {
        "id": r["run_id"], "period": r["period_id"], "vehicleType": r.get("vehicle_type"),
        "capacityUnits": _i(r.get("capacity_units")), "originHubId": r.get("origin_hub_id"),
        "stops": _j(r.get("stops_json"), []), "path": _j(r.get("path_json"), []),
        "totalRoadKm": _i(r.get("total_road_km")), "totalUnits": _i(r.get("total_units")),
        "fillRate": _f(r.get("fill_rate")), "costTotal": _i(r.get("cost_total")),
        "costPerUnit": _f(r.get("cost_per_unit")),
        "kmSavedVsBaseline": _i(r.get("km_saved_vs_baseline")),
        "depart": r.get("depart"), "eta": r.get("eta"), "status": r.get("status"),
    }


def _period_stat(r: dict) -> dict:
    return {"period": r["period_id"], "label": r.get("label"),
            "movements": _i(r.get("movements")), "units": _i(r.get("units")),
            "runs": _i(r.get("runs")), "avgFill": _f(r.get("avg_fill")),
            "facilities": _i(r.get("facilities"))}


def _summary(r: dict) -> dict:
    return {"period": r["period_id"], "unitsAcrossLegs": _i(r.get("units")),
            "uncoveredNeeds": _i(r.get("uncovered_needs")),
            "atRiskFacilities": _i(r.get("at_risk_facilities")),
            "legs": _i(r.get("movements")), "facilities": _i(r.get("facilities")),
            "vehicleRuns": _i(r.get("runs")), "avgFillRate": _f(r.get("avg_fill")),
            "overflowLegs": _i(r.get("overflow_legs")), "minMonths": _i(r.get("min_months")),
            "maxMonths": _i(r.get("max_months"))}


def _baseline(r: dict) -> dict:
    return {"optimizedKm": _i(r.get("optimized_km")), "baselineKm": _i(r.get("baseline_km")),
            "kmSaved": _i(r.get("km_saved")), "kmSavedPct": _i(r.get("km_saved_pct")),
            "optimizedCost": _i(r.get("optimized_cost")), "baselineCost": _i(r.get("baseline_cost")),
            "costSaved": _i(r.get("cost_saved")), "costSavedPct": _i(r.get("cost_saved_pct")),
            "vehiclesUsed": _i(r.get("vehicles_used")), "naiveVehicles": _i(r.get("naive_vehicles")),
            "vehiclesSaved": _i(r.get("vehicles_saved"))}


# Plan decisions (ops_plan.plan_decision, init script 42)
# Decision makers approve or reject a planned line; the log is append-only and
# bitemporal like the MDM decision log, and each row snapshots the line.
DECIDER_ROLES = ("R-DEC", "R-SUPER")
_MOVEMENT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,40}:[A-Za-z0-9_.-]{1,40}$")


def _decide_gate(user: UserCtx) -> opa.Decision:
    allowed = any(r in DECIDER_ROLES for r in user.roles)
    return opa.Decision(
        allow=allowed,
        reason=f"plan decision gate: roles {user.roles} "
               f"{'allowed' if allowed else 'denied'} (decision makers only)")


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if v is None or isinstance(v, (str, bool, int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)


async def _current_decisions(pool, periods: list[str]) -> dict[tuple[str, str], dict]:
    """Current verdict per (period, movement). Empty when the store is absent."""
    if not periods:
        return {}
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT decision_id, plan_period, movement_id, decision, decided_by, recorded_from
                FROM ops_plan.plan_decision
                WHERE recorded_to IS NULL AND plan_period = ANY($1::text[])
            """, periods)
    except Exception:                                            # noqa: BLE001
        return {}
    return {(r["plan_period"], r["movement_id"]): {
                "id": str(r["decision_id"]), "decision": r["decision"],
                "by": r["decided_by"], "at": r["recorded_from"].isoformat()} for r in rows}


async def _read_scope(user: UserCtx) -> list[str]:
    """The plan's read gate: OPA on the plan marts plus the caller's scope."""
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as _c:
        scope = await permissions.effective_user_scope(_c, user)
    decision = await opa.decide(user, action="read", resource="PRD",
                                attributes=_OPA_ATTRS, scope=scope, columns={})
    if not decision.allow:
        raise HTTPException(403, "Supply Control Tower denied by policy: " + (decision.reason or ""))
    return decision.row_filter.get("geography") or []


@router.post("/plan/lines/{movement_id}/decide")
async def decide_line(movement_id: str = PathParam(...),
                      decision: str = Body(..., embed=True),
                      note: str | None = Body(default=None, embed=True),
                      user: UserCtx = Depends(get_user)) -> dict:
    """Approve or reject one planned line. Any current verdict on the line is
    superseded (recorded_to stamped), never updated in place."""
    gate = _decide_gate(user)
    if not gate.allow:
        raise HTTPException(403, gate.reason)
    if decision not in ("approve", "reject"):
        raise HTTPException(422, "decision must be approve or reject")
    if note is not None and len(note) > 500:
        raise HTTPException(422, "note is limited to 500 characters")
    if not _MOVEMENT_ID.match(movement_id):
        raise HTTPException(404, "not found")
    geo = await _read_scope(user)

    rows = (await trino_client.query(
        f"SELECT * FROM {_SCHEMA}.mart_ct_movements WHERE movement_id = ?", [movement_id])).get("rows") or []
    if not rows or (geo and rows[0].get("region_id") not in geo):
        raise HTTPException(404, "not found")
    mv = rows[0]
    if not _b(mv.get("planned")):
        raise HTTPException(409, "this line has no planned order to decide")
    facility, product = movement_id.split(":", 1)
    fc = (await trino_client.query(
        f"SELECT model_version, mlflow_run_id, CAST(predict_run_id AS varchar) AS predict_run_id, "
        f"CAST(predicted_at AS varchar) AS predicted_at, as_of_month, target_month, p10, p50, p90, "
        f"method, demand_class FROM {_SCHEMA}.mart_pct_forecast WHERE facility_code = ? AND product_code = ?",
        [facility, product])).get("rows") or []
    stat = (await trino_client.query(
        f"SELECT max_months FROM {_SCHEMA}.mart_ct_plan_stats WHERE period_id = ?",
        [mv["period_id"]])).get("rows") or []
    snapshot = _jsonable({
        "line": {k: mv.get(k) for k in ("movement_id", "period_id", "from_hub_id", "to_node_id", "region_id",
                                        "drug", "units", "amc", "mos", "days_to_stockout", "priority",
                                        "lane_km", "demand_basis", "at_risk")},
        "why": _j(mv.get("why_json"), {}),
        "forecast": fc[0] if fc else None,
        "policy": {"max_stock_months": stat[0]["max_months"] if stat else None,
                   "distance_basis": "straight_line", "hub_basis": "district_store_inferred"},
    })

    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                UPDATE ops_plan.plan_decision SET recorded_to = now()
                 WHERE plan_period = $1 AND movement_id = $2 AND recorded_to IS NULL
            """, mv["period_id"], movement_id)
            row = await conn.fetchrow("""
                INSERT INTO ops_plan.plan_decision
                    (plan_period, movement_id, facility_code, product_code, from_hub_id,
                     decision, units_recommended, snapshot, note, decided_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING decision_id, recorded_from
            """, mv["period_id"], movement_id, facility, product, mv["from_hub_id"],
                 decision, _i(mv.get("units")) or 0, snapshot, note, user.username)
    await ops_db.log_policy_decision(
        user=user, action="pct.plan.decide", resource=movement_id, decision="allow",
        rule_hits=gate.rule_hits, explanation={"verdict": decision, "period": mv["period_id"],
                                               "units": _i(mv.get("units")), "note": note})
    return {"id": str(row["decision_id"]), "movement": movement_id, "period": mv["period_id"],
            "decision": decision, "by": user.username, "at": row["recorded_from"].isoformat()}


@router.post("/decisions/{decision_id}/revert")
async def revert_decision(decision_id: str = PathParam(...),
                          note: str | None = Body(default=None, embed=True),
                          user: UserCtx = Depends(get_user)) -> dict:
    """Withdraw a current verdict: stamp recorded_to; the line is open again."""
    gate = _decide_gate(user)
    if not gate.allow:
        raise HTTPException(403, gate.reason)
    try:
        UUID(decision_id)
    except (ValueError, TypeError):
        raise HTTPException(404, "not found")
    geo = await _read_scope(user)
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        dec = await conn.fetchrow(
            "SELECT * FROM ops_plan.plan_decision WHERE decision_id = $1::uuid", decision_id)
        snap = dec["snapshot"] if dec is not None else None
        snap = json.loads(snap) if isinstance(snap, str) else (snap or {})
        if dec is None or (geo and snap.get("line", {}).get("region_id") not in geo):
            raise HTTPException(404, "not found")
        if dec["recorded_to"] is not None:
            raise HTTPException(409, "decision already superseded")
        await conn.execute(
            "UPDATE ops_plan.plan_decision SET recorded_to = now() WHERE decision_id = $1::uuid", decision_id)
    await ops_db.log_policy_decision(
        user=user, action="pct.plan.revert", resource=decision_id, decision="allow",
        rule_hits=gate.rule_hits, explanation={"reverted_verdict": dec["decision"],
                                               "movement": dec["movement_id"], "note": note})
    return {"id": decision_id, "reverted": True}


@router.get("/plan")
async def ct_plan(period: str | None = Query(None, description="planning period id, e.g. 2025-M11"),
                  user: UserCtx = Depends(get_user)) -> dict:
    t0 = time.perf_counter()
    pool = await ops_db.ensure_pool()

    # OPA mart-level gate (subject=PRD, DC.STK, aggregate) plus scope, giving the
    # row_filter. The nodes feed carries facility GPS (mart_ct_nodes.lat/lng,
    # classified restricted with mask class generalize); the classification is
    # sent so OPA decides whether this caller sees exact coordinates, and
    # masking is applied to the node rows below.
    async with pool.acquire() as _c:
        scope = await permissions.effective_user_scope(_c, user)
    # _SCHEMA is Trino-qualified for the queries below; the classification
    # store keys on the bare schema name.
    node_colclass = await mart_attrs.columns_for_mart(_BARE_SCHEMA, "mart_ct_nodes")
    decision = await opa.decide(user, action="read", resource="PRD",
                                attributes=_OPA_ATTRS, scope=scope,
                                columns={n: c.sensitivity for n, c in node_colclass.items()})
    if not decision.allow:
        await ops_db.log_gateway_query(
            user=user, method="GET", path="/api/ct/plan",
            mart_scope=[f"{_SCHEMA}.mart_ct_movements"], filters={}, row_count=None,
            byte_count=None, latency_ms=int((time.perf_counter() - t0) * 1000),
            http_status=403, policy_decision="deny", denied_reason=decision.reason)
        raise HTTPException(403, "Supply Control Tower denied by policy: " + (decision.reason or ""))

    geo = decision.row_filter.get("geography") or []
    geo_clause = f" WHERE region_id IN {_sql_in(geo)}" if geo else ""

    try:
        stats_rows = (await trino_client.query(
            f"SELECT * FROM {_SCHEMA}.mart_ct_plan_stats ORDER BY sort_order")).get("rows") or []
        node_rows = (await trino_client.query(
            f"SELECT * FROM {_SCHEMA}.mart_ct_nodes")).get("rows") or []
        if decision.masked_columns:
            node_rows, _ = masking.apply_masking(
                node_rows, node_colclass, decision.masked_columns)
        mv_rows = (await trino_client.query(
            f"SELECT * FROM {_SCHEMA}.mart_ct_movements{geo_clause}")).get("rows") or []
        # Runs follow the movement scope (a run referenced by an in-scope
        # movement); unscoped callers get every run.
        if geo:
            run_ids = sorted({m["vehicle_run_id"] for m in mv_rows if m.get("vehicle_run_id")})
            run_clause = f" WHERE run_id IN {_sql_in(run_ids)}" if run_ids else " WHERE 1 = 0"
        else:
            run_clause = ""
        run_rows = (await trino_client.query(
            f"SELECT * FROM {_SCHEMA}.mart_ct_vehicle_runs{run_clause}")).get("rows") or []
        # Stock-health KPIs at the latest reported month, per province, as
        # numerator/denominator so the cards pool exactly as the KPI definitions
        # say under any Region filter (same access attributes as the plan marts).
        kpi_rows = (await trino_client.query(
            f"WITH mx AS (SELECT max(month_id) AS m FROM {_SCHEMA}.mart_pct_kpi) "
            f"SELECT region_id, kpi_code, coalesce(category, '') AS category, month_id, "
            f"sum(numerator) AS numerator, sum(denominator) AS denominator, max(target) AS target "
            f"FROM {_SCHEMA}.mart_pct_kpi, mx "
            f"WHERE month_id IN (mx.m, format_datetime(date_add('month', -1, CAST(mx.m || '-01' AS date)), 'yyyy-MM'))"
            f"{geo_clause.replace(' WHERE ', ' AND ')} "
            f"AND kpi_code IN ('osa_s8', 'stockout_exposure', 'stockout_days_rate', 'mos_band') "
            f"GROUP BY region_id, kpi_code, coalesce(category, ''), month_id")).get("rows") or []
    except HTTPException:
        raise
    except Exception as exc:
        await ops_db.log_gateway_query(
            user=user, method="GET", path="/api/ct/plan", mart_scope=[_SCHEMA],
            filters={}, row_count=None, byte_count=None,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            http_status=500, policy_decision="error", denied_reason=str(exc))
        raise HTTPException(500, f"CT plan query failed: {exc}")

    if geo:
        # A region-scoped caller sees only in-scope facilities and the stores
        # that serve them, never the whole facility registry.
        in_scope_hubs = {m.get("from_hub_id") for m in mv_rows}
        node_rows = [r for r in node_rows
                     if (r.get("node_kind") == "facility" and r.get("region_id") in geo)
                     or (r.get("node_kind") == "hub" and r.get("node_id") in in_scope_hubs)]
    warehouses = [_warehouse(r) for r in node_rows if r.get("node_kind") == "hub"]
    nodes = [_node(r) for r in node_rows if r.get("node_kind") == "facility"]
    movements = [_movement(r) for r in mv_rows]
    runs = [_run(r) for r in run_rows]
    period_stats = [_period_stat(r) for r in stats_rows]
    periods = [{"id": r["period_id"], "label": r.get("label")} for r in stats_rows]

    # currentPeriod: the requested period if valid, else the latest planning period.
    stat_by_period = {r["period_id"]: r for r in stats_rows}
    latest = stats_rows[-1]["period_id"] if stats_rows else None
    current_period = period if (period and period in stat_by_period) else latest
    cur_stat = stat_by_period.get(current_period)

    # regionNames / commodities: derived from the returned reference data, ordered.
    region_names = sorted({n["region"] for n in nodes if n.get("region")})
    regions = [{"name": rn} for rn in region_names]
    commodities = sorted({m["drugShort"] for m in movements if m.get("drugShort")})

    # selectedId: the most urgent (highest priority) movement in the current period.
    cur_moves = [m for m in movements if m["period"] == current_period]
    selected_id = None
    if cur_moves:
        selected_id = max(cur_moves, key=lambda m: (bool(m.get("planned")), m.get("priority") or 0,
                                                    m.get("units") or 0, m["id"]))["id"]

    decisions = await _current_decisions(pool, sorted({m["period"] for m in movements}))
    for m in movements:
        m["decision"] = decisions.get((m["period"], m["id"]))

    await ops_db.log_gateway_query(
        user=user, method="GET", path="/api/ct/plan",
        mart_scope=[f"{_SCHEMA}.mart_ct_movements"],
        filters={"geography": geo} if geo else {},
        row_count=len(movements), byte_count=None,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        http_status=200, policy_decision="allow")

    return {
        "regions": regions,
        "regionNames": region_names,
        "periods": periods,
        "currentPeriod": current_period,
        "commodities": commodities,
        "warehouses": warehouses,
        "nodes": nodes,
        "movements": movements,
        "vehicleRuns": runs,
        "selectedId": selected_id,
        "periodStats": period_stats,
        "summary": _summary(cur_stat) if cur_stat else None,
        "baseline": _baseline(cur_stat) if cur_stat else None,
        "canDecide": _decide_gate(user).allow,
        "kpis": [{"regionId": r.get("region_id"), "kpi": r.get("kpi_code"), "category": r.get("category"),
                  "month": r.get("month_id"),
                  "numerator": _f(r.get("numerator")), "denominator": _f(r.get("denominator")),
                  "target": _f(r.get("target"))} for r in kpi_rows],
    }
