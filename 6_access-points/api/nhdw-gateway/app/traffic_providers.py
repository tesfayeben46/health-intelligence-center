"""Traffic provider registry for the topology graph.

Each provider maps a time window to ``dict[edge_id -> traffic_block]``. The graph
route attaches the highest-precedence block (providers earlier in ``PROVIDERS``
win) to the matching edge; edges with no provider data keep ``traffic = None``.

Two providers exist:
  * the gateway's append-only audit log (``ops_audit.gateway_log``), which
    records path, latency, status, policy decision and bytes for every request,
    so a windowed GROUP BY over a path-to-edge map gives request rate, error
    rate, p95 and bytes for the gateway-centric edges;
  * Cilium Hubble flow metrics from Prometheus, for every other edge.

A new provider returns the same block shape keyed by the same ``edge_id`` and is
added to ``PROVIDERS``; the graph route, edge contract and widget do not change.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import httpx

from . import ops_db
from .topology_graph import edge_id  # shared helper: provider ids equal graph ids

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")

log = logging.getLogger("gateway.topology.traffic")

_GW = "nhdw-gateway"

# Path LIKE pattern -> edge_id; first match wins. Two families:
#   * gateway to downstream backend (the path identifies the backend the gateway
#     called): marts and sql to Trino, exports and dq to the SeaweedFS filer,
#     ml to MLflow.
#   * admin dashboard to gateway: admin-only endpoints (audit, topology,
#     permissions, access requests, api-tokens, groups) are attributed to the
#     admin-to-gateway edge.
# Paths shared by several dashboards (/api/me, /api/projects, /api/reports, ...)
# are left unmapped rather than mis-attributed.
PATH_TO_EDGE: list[tuple[str, str]] = [
    ("/api/marts/%",          edge_id(_GW, "nhdw-trino")),
    ("/api/sql%",             edge_id(_GW, "nhdw-trino")),
    ("/api/exports/%",        edge_id(_GW, "seaweedfs-filer")),
    ("/api/dq/%",             edge_id(_GW, "seaweedfs-filer")),
    ("/api/ml/%",             edge_id(_GW, "mlflow")),
    # /api/chat calls an LLM outside the cluster (host.k3d.internal). The
    # topology is an in-cluster view with no node for it, so it is unmapped.
    ("/api/audit%",           edge_id("admin", _GW)),
    ("/api/topology/%",       edge_id("admin", _GW)),
    ("/api/permissions%",     edge_id("admin", _GW)),
    ("/api/perm%",            edge_id("admin", _GW)),
    ("/api/access-request%",  edge_id("admin", _GW)),
    ("/api/api-tokens%",      edge_id("admin", _GW)),
    ("/api/groups%",          edge_id("admin", _GW)),
]

_WINDOW_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.I)
_UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_window(window: str | None) -> tuple[int, str]:
    """Parse '5m', '2h', '30d' or '300s' into (seconds, normalised label).
    Invalid input yields 5m."""
    m = _WINDOW_RE.match(window or "")
    if not m:
        return 300, "5m"
    n, unit = int(m.group(1)), m.group(2).lower()
    secs = n * _UNIT_S[unit]
    return (secs, f"{n}{unit}") if secs > 0 else (300, "5m")


def _case_sql() -> str:
    # Patterns are static literals (no user input), so inlining them is safe.
    whens = "\n".join(
        f"          WHEN path LIKE '{pat}' THEN '{eid}'" for pat, eid in PATH_TO_EDGE
    )
    return f"CASE\n{whens}\n          ELSE NULL\n        END"


async def gateway_log_traffic(window: str | None) -> dict[str, dict[str, Any]]:
    """One query over ops_audit.gateway_log within the window, grouped by the
    path-to-edge map. Returns {edge_id: traffic block}. Shared by the provider
    and GET /api/topology/traffic so both return the same contract."""
    secs, label = parse_window(window)
    sql = f"""
        SELECT edge_id,
               count(*)                                              AS samples,
               count(*)::float / $1                                  AS req_rate,
               avg((COALESCE(http_status, 0) >= 500
                    OR policy_decision = 'deny')::int)::float        AS err_rate,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
               COALESCE(sum(byte_count), 0)                           AS bytes
        FROM (
          SELECT {_case_sql()} AS edge_id,
                 http_status, policy_decision, latency_ms, byte_count
          FROM ops_audit.gateway_log
          WHERE ts > now() - make_interval(secs => $1)
        ) t
        WHERE edge_id IS NOT NULL
        GROUP BY edge_id;
    """
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, secs)
        series = await _gateway_log_series(conn, secs)

    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        eid = r["edge_id"]
        out[eid] = {
            "reqRate": round(float(r["req_rate"]), 5),
            "errRate": round(float(r["err_rate"] or 0.0), 4),
            "p95Ms":   int(r["p95_ms"]) if r["p95_ms"] is not None else None,
            "bytes":   int(r["bytes"] or 0),
            "window":  label,
            "samples": int(r["samples"]),
            "source":  "gateway_log",
            "series":  series.get(eid),   # per-bucket req/s over the window
        }
    return out


_SERIES_BUCKETS = 12


async def _gateway_log_series(conn: Any, secs: int) -> dict[str, list[float]]:
    """Per-edge req/s sparkline: split the window into _SERIES_BUCKETS equal
    time buckets and return each edge's req/s per bucket, oldest first, from the
    same gateway_log rows. Edges with no rows get no series, and the widget
    shows an empty state."""
    bucket_secs = max(1.0, secs / _SERIES_BUCKETS)
    sql = f"""
        SELECT edge_id,
               floor(extract(epoch from (now() - ts)) / $2)::int AS b,
               count(*) AS n
        FROM (
          SELECT {_case_sql()} AS edge_id, ts
          FROM ops_audit.gateway_log
          WHERE ts > now() - make_interval(secs => $1)
        ) t
        WHERE edge_id IS NOT NULL
        GROUP BY edge_id, b;
    """
    rows = await conn.fetch(sql, secs, bucket_secs)
    out: dict[str, list[float]] = {}
    for r in rows:
        b = int(r["b"])
        if b < 0 or b >= _SERIES_BUCKETS:
            continue
        arr = out.setdefault(r["edge_id"], [0.0] * _SERIES_BUCKETS)
        # b counts age (0 = most recent); place the newest on the right.
        arr[_SERIES_BUCKETS - 1 - b] = round(int(r["n"]) / bucket_secs, 5)
    return out


class GatewayLogTrafficProvider:
    """Per-edge RED metrics from the gateway's own audit log."""
    source = "gateway_log"

    async def load(self, window: str | None) -> dict[str, dict[str, Any]]:
        try:
            return await gateway_log_traffic(window)
        except Exception as exc:   # an unavailable audit DB must not break the graph
            log.warning("gateway_log traffic provider failed: %s", exc)
            return {}


async def _prom_query(client: httpx.AsyncClient, expr: str) -> list[dict[str, Any]]:
    """One Prometheus instant query; [] on any non-success response."""
    r = await client.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": expr})
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "success":
        return []
    return body.get("data", {}).get("result", [])


# Hubble (Cilium) flow provider.
# CNI-level flow metrics from the Cilium agents' Hubble exporter (Prometheus job
# `hubble`, :9965). source and destination labels are the workload name with a
# pod-name fallback, so pods without a controller (the KubeSpawner JupyterLab
# singletons) appear under their pod name, which is their topology node id.
# verdict and drop metrics give the error signal. Hubble flow metrics carry no
# byte counters, so bytes are reported as 0.

_POD_SUFFIX = re.compile(r"^(.+)-[a-f0-9]{8,10}-[a-z0-9]{5}$")


def _hubble_node(name: str) -> str:
    """Map a Hubble workload-or-pod name to a topology node id.

    Hubble sometimes reports the raw pod name for Deployment pods (e.g.
    prometheus-bb5498d96-g55bf); strip the ReplicaSet hash and random suffix so
    the edge id matches the declared node (prometheus). Bare pods such as
    jupyter-<user> have no such suffix and pass through unchanged, since their
    pod name is the node id."""
    m = _POD_SUFFIX.match(name)
    return m.group(1) if m else name


async def hubble_flow_traffic(window: str | None) -> dict[str, dict[str, Any]]:
    """Hubble flow, TCP and drop metrics as {edge_id: traffic block}."""
    secs, label = parse_window(window)
    w = f"{secs}s"
    ns = 'source_namespace="nhdw",destination_namespace="nhdw"'
    by = "by (source, destination)"
    async with httpx.AsyncClient(timeout=15.0) as client:
        flows, syns, drops, drop_reasons = await asyncio.gather(
            _prom_query(client, f'sum {by} (rate(hubble_flows_processed_total{{{ns},verdict="FORWARDED"}}[{w}]))'),
            _prom_query(client, f'sum {by} (rate(hubble_tcp_flags_total{{{ns},flag="SYN"}}[{w}]))'),
            _prom_query(client, f'sum {by} (rate(hubble_drop_total{{{ns}}}[{w}]))'),
            # Per-edge drop breakdown by reason, for the edge drill-down.
            _prom_query(client, f'sum by (source, destination, reason) (rate(hubble_drop_total{{{ns}}}[{w}]))'),
        )

    def edge_of(metric: dict) -> str | None:
        src = _hubble_node(metric.get("source") or "")
        dst = _hubble_node(metric.get("destination") or "")
        if not src or not dst or src == dst:
            return None
        return edge_id(src, dst)

    agg: dict[str, dict[str, float]] = {}

    def add(rows: list[dict[str, Any]], key: str) -> None:
        for s in rows:
            eid = edge_of(s.get("metric", {}))
            if not eid:
                continue
            try:
                v = float(s["value"][1])
            except (KeyError, IndexError, ValueError, TypeError):
                continue
            d = agg.setdefault(eid, {})
            d[key] = d.get(key, 0.0) + v

    add(flows, "flow_rate")
    add(syns, "conn_rate")
    add(drops, "drop_rate")

    # per-edge drop reasons (nonzero only): {edge_id: {reason: rate}}
    reasons: dict[str, dict[str, float]] = {}
    for s in drop_reasons:
        m = s.get("metric", {})
        eid = edge_of(m)
        if not eid:
            continue
        try:
            v = float(s["value"][1])
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        if v > 0:
            reasons.setdefault(eid, {})[m.get("reason", "unknown")] = round(v, 5)

    out: dict[str, dict[str, Any]] = {}
    for eid, d in agg.items():
        flow_rate = d.get("flow_rate", 0.0)
        conn_rate = d.get("conn_rate", 0.0)
        drop_rate = d.get("drop_rate", 0.0)
        if flow_rate <= 0.0 and conn_rate <= 0.0 and drop_rate <= 0.0:
            continue                       # silent edge stays structural
        out[eid] = {
            "reqRate": round(conn_rate, 5),          # new TCP connections / sec
            "errRate": round(drop_rate, 5),          # drops (policy or error verdicts)
            "p95Ms": None,
            "bytes": 0,                              # hubble flow metrics carry no bytes
            "window": label,
            "samples": int(round(flow_rate * secs)),
            "activeConns": None,
            "bytesRate": 0.0,
            "flowRate": round(flow_rate, 3),         # Hubble-specific: L3/L4 flows per second
            "dropReasons": reasons.get(eid),         # {reason: rate} or None
            "source": "hubble",
            "series": None,
        }
    return out


class HubbleFlowProvider:
    """CNI-level flows from Cilium/Hubble metrics (Prometheus job `hubble`)."""
    source = "hubble"

    async def load(self, window: str | None) -> dict[str, dict[str, Any]]:
        try:
            return await hubble_flow_traffic(window)
        except Exception as exc:   # an unavailable Prometheus must not break the graph
            log.warning("hubble flow provider failed: %s", exc)
            return {}


# Precedence: first entry wins per edge (the route uses setdefault). gateway_log
# comes first so the gateway-centric edges keep HTTP RED metrics (error rate and
# p95 from status codes); Hubble fills every other edge, including
# controller-less JupyterLab pods.
PROVIDERS = [GatewayLogTrafficProvider(), HubbleFlowProvider()]
