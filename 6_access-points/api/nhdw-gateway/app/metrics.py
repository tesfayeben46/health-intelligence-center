"""Prometheus RED metrics (request rate, error rate, duration) for the gateway,
exposed at GET /metrics for the in-cluster Prometheus scrape job `nhdw-gateway`
(see the `prometheus-config` ConfigMap). The admin observability health widget
reads `up{job="nhdw-gateway"}` from this scrape.

Labels are low-cardinality by design. The `route` label is the first two path
segments (e.g. `/api/dq`, `/api/permissions`), never the raw path, so ids
embedded in URLs (`/api/permissions/widget/<id>/data`, export ids) cannot
multiply the series count. Method and status are small bounded sets and are
kept as is."""
from __future__ import annotations

from prometheus_client import Counter, Histogram

# Request rate and error rate (the status label carries 4xx/5xx).
REQUESTS = Counter(
    "nhdw_gateway_requests_total",
    "Total gateway HTTP requests handled, by method, collapsed route, and status.",
    ["method", "route", "status"],
)

# Duration in seconds. Buckets span sub-10 ms health probes to multi-second
# Trino and Postgres mart reads.
LATENCY = Histogram(
    "nhdw_gateway_request_duration_seconds",
    "Gateway request latency in seconds, by method and collapsed route.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


def route_label(path: str) -> str:
    """Collapse a request path to its router prefix (at most two segments) so
    the `route` label stays bounded regardless of ids in the URL."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "/"
    return "/" + "/".join(parts[:2])


def observe(method: str, path: str, status: int, elapsed_s: float) -> None:
    """Record one request. Callers wrap this so an instrumentation error never
    affects the response."""
    route = route_label(path)
    REQUESTS.labels(method=method, route=route, status=str(status)).inc()
    LATENCY.labels(method=method, route=route).observe(elapsed_s)
