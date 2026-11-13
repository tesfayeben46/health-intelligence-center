"""Public Dashboard read layer over the NHDW gateway.

Marts are read through the gateway's /api/marts endpoint, which is the single
point for OPA, audit and export logic; the dashboard holds no database
credentials. NHDW_GATEWAY_URL is the only connection setting. When the gateway
fails, the caller in app/main.py returns an empty series.

Each builder returns an ECharts-shaped {x, series} payload for one dashboard
slug, e.g. ``await facility_counts()``. Add one builder per new public slug.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger("public-dashboard.marts")

GATEWAY_URL = os.environ.get("NHDW_GATEWAY_URL", "http://nhdw-gateway:8000")
GATEWAY_TIMEOUT_S = float(os.environ.get("NHDW_GATEWAY_TIMEOUT_S", "5"))


async def _gw_get_mart(schema: str, table: str,
                       columns: list[str] | None = None,
                       order_by: list[str] | None = None,
                       limit: int = 500) -> list[dict[str, Any]]:
    """GET /api/marts/{schema}/{table} from the gateway. Returns rows."""
    params: dict[str, Any] = {"_limit": str(limit)}
    if columns:
        params["_columns"] = ",".join(columns)
    if order_by:
        params["_orderby"] = ",".join(order_by)
    async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT_S) as c:
        # Anonymous read: no auth header, the gateway maps that to R-PUB-ANON.
        r = await c.get(f"{GATEWAY_URL}/api/marts/{schema}/{table}", params=params)
        r.raise_for_status()
        return (r.json() or {}).get("rows", [])


# Series builders, one per dashboard slug


async def facility_counts() -> dict[str, Any]:
    """National Facility Distribution: n_facilities by region, from
    `bi_public.bi_facility_distribution_public` (k >= 5 enforced in the mart).
    """
    rows = await _gw_get_mart(
        schema="bi_public", table="bi_facility_distribution_public",
        columns=["region_name", "n_facilities"],
        order_by=["n_facilities DESC"], limit=20)
    return {
        "x": [r["region_name"] for r in rows],
        "series": [{
            "name": "Facilities",
            "data": [int(r["n_facilities"] or 0) for r in rows],
        }],
    }


async def national_coverage() -> dict[str, Any]:
    """National Programme Coverage: coverage_pct by programme and month from
    the k-anonymous public summary mart.
    """
    rows = await _gw_get_mart(
        schema="bi_public", table="mart_public_summary",
        columns=["programme_id", "month_id", "coverage_pct"],
        order_by=["month_id", "programme_id"], limit=500)
    # Pivot into ECharts shape: x=months, one series per programme.
    months: list[str] = sorted({r["month_id"] for r in rows if r.get("month_id")})
    programmes: list[str] = sorted({r["programme_id"] for r in rows if r.get("programme_id")})
    series = []
    for p in programmes:
        data = []
        for m in months:
            v = next((r["coverage_pct"] for r in rows
                      if r["programme_id"] == p and r["month_id"] == m), None)
            data.append(float(v) if v is not None else None)
        series.append({"name": p, "data": data})
    return {"x": months, "series": series}


async def stock_availability() -> dict[str, Any]:
    """Commodity stock availability by region from
    `bi_public.mart_supply_stockout_public`. Returns an empty payload when the
    mart is unavailable; the route then returns an empty series.
    """
    try:
        rows = await _gw_get_mart(
            schema="bi_public", table="mart_supply_stockout_public",
            columns=["region_name", "stocked_pct"],
            order_by=["region_name"], limit=20)
    except Exception as exc:
        log.warning("stock_availability mart not available yet: %s", exc)
        return {"x": [], "series": []}
    return {
        "x": [r["region_name"] for r in rows],
        "series": [{
            "name": "% facilities stocked",
            "data": [float(r["stocked_pct"] or 0) for r in rows],
        }],
    }
