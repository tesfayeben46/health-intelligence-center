"""NHDW Public Dashboard: anonymous, read-only.

No authentication and no PHI. Data reaches this app only through the NHDW
gateway as R-PUB-ANON, which exposes a curated set of public marts; the marts
enforce k-anonymity (k >= 5) in SQL. See app/marts.py for the gateway reads.

`facility-counts` reads from bi_public.bi_facility_distribution_public.
Slugs without a backing mart, or whose mart is empty, return an empty series.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.staticfiles import StaticFiles

from app import marts as marts_db
from app.opa_client import opa_check

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
log = logging.getLogger("public-dashboard")

# Container layout:
#   /app/dist/index.html           Vite/React SPA build output
#   /app/dist/assets/              JS/CSS bundles emitted by Vite
#   /app/static/{css,js,assets}/   assets the SPA references by /static/ path
#                                  (logos, icons, background images, geojson)
DIST_DIR   = Path("/app/dist")
STATIC_DIR = Path("/app/static")
LOKI_URL   = os.environ.get("LOKI_URL", "http://loki:3100")

# Fayda verification
# Fayda verifies a non-registered visitor's identity on the access-request form
# with an email-OTP round-trip. The external Fayda replica (0_external/fayda,
# host port 8096) owns the whole flow: registry check, OTP issue, email through
# its own SMTP, and code confirmation. This server only proxies the two browser
# calls, same-origin, to the replica's /verify/* endpoints. It is not a login:
# there is no Keycloak "sign in with Fayda". The replica is reached over the k3d
# host route; production points FAYDA_INTERNAL_BASE at the real Fayda service.
FAYDA_INTERNAL_BASE = os.environ.get(
    "FAYDA_INTERNAL_BASE", "http://host.k3d.internal:8096").rstrip("/")

app = FastAPI(title="NHDW Public Dashboard", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=False), name="static")
# Vite-built JS/CSS bundles live under /app/dist/assets/<hash>.js etc.
app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")


@app.get("/", response_class=HTMLResponse)
async def root(request: Request) -> HTMLResponse:
    """Serve the Vite/React SPA. Section switching (home | data | articles |
    article) is client-side state."""
    # no-cache: index.html references hash-named bundles that change every build;
    # a cached copy would point at missing bundles after a redeploy.
    return HTMLResponse((DIST_DIR / "index.html").read_text(),
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# Fayda email-OTP verification (same-origin proxy to the Fayda replica)
#
# Two-step email OTP, owned by the external Fayda replica:
#   1. POST /api/proxy/fayda/challenge {fan}: Fayda confirms the FAN exists,
#      mails a 6-digit code to the registered email and returns
#      {ok, masked_email}.
#   2. POST /api/proxy/fayda/verify {fan, code}: Fayda checks the code and
#      returns {ok, fayda_id, name, ...} on success.
# The server-side hop keeps the browser same-origin (no CORS, no direct Fayda
# exposure) and does not involve the NHDW gateway.

@app.post("/api/proxy/fayda/challenge")
async def proxy_fayda_challenge(request: Request) -> JSONResponse:
    payload = await request.body()
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{FAYDA_INTERNAL_BASE}/verify/challenge",
                             content=payload,
                             headers={"Content-Type": "application/json"})
    except httpx.RequestError as e:
        return JSONResponse({"ok": False, "reason": "service_unavailable",
                             "detail": f"fayda unreachable: {e}"}, status_code=502)
    return JSONResponse(r.json() if r.headers.get("content-type", "")
                        .startswith("application/json") else {"raw": r.text},
                        status_code=r.status_code)


@app.post("/api/proxy/fayda/verify")
async def proxy_fayda_verify(request: Request) -> JSONResponse:
    payload = await request.body()
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{FAYDA_INTERNAL_BASE}/verify/confirm",
                             content=payload,
                             headers={"Content-Type": "application/json"})
    except httpx.RequestError as e:
        return JSONResponse({"ok": False, "reason": "service_unavailable",
                             "detail": f"fayda unreachable: {e}"}, status_code=502)
    return JSONResponse(r.json() if r.headers.get("content-type", "")
                        .startswith("application/json") else {"raw": r.text},
                        status_code=r.status_code)


@app.get("/api/dashboards")
async def dashboards() -> JSONResponse:
    """Static catalog of public dashboards. All entries map to k-anon views."""
    return JSONResponse([
        {"id": "national-coverage", "title": "National Programme Coverage", "description": "EPI, MNCH, TB, HIV: quarterly aggregates by region."},
        {"id": "facility-counts",   "title": "Health Facility Distribution", "description": "Number of facilities by region and woreda."},
        {"id": "stock-availability","title": "Commodity Stock Availability", "description": "% of facilities with key drugs in stock by region."},
    ])


# Access-point registry
#
# Registry of the NHDW surfaces the portal links to. Each entry pins a dev URL
# (127.0.0.1:<port>) and a production subdomain. `NHDW_BASE_DOMAIN` selects:
#   - unset: dev URLs on localhost
#   - set:   https://<sub>.<base><path>, matching the Kubernetes Ingress hosts
# so one env var retargets every access-point card.

NHDW_BASE_DOMAIN = os.environ.get("NHDW_BASE_DOMAIN", "").rstrip(".")


def _url(dev_url: str, sub: str, path: str = "/") -> str:
    if NHDW_BASE_DOMAIN:
        return f"https://{sub}.{NHDW_BASE_DOMAIN}{path}"
    return dev_url


ACCESS_POINTS_TEMPLATE = [
    {
        "id": "public-stats", "title": "Public Health Data", "tier": "access", "icon": "chart-pie",
        "dev_url": "/data", "sub": "portal", "path": "/data",
        "tagline": "Aggregate national indicators, open to the public.",
        "description": "Anonymous read-only view of k-anonymous health indicators "
                       "(facility distribution, programme coverage, stock availability). "
                       "The marts layer enforces k >= 5 at the SQL level: no row-level "
                       "data leaves this surface.",
        "use_when": "You need a citation-quality figure for a public report.",
        "roles": ["R-PUB-ANON"], "realm": None,
    },
    {
        "id": "fhir-api", "title": "FHIR R4 API", "tier": "api", "icon": "globe",
        "dev_url": "http://127.0.0.1:8093/fhir/metadata", "sub": "fhir", "path": "/fhir/metadata",
        "tagline": "HL7 FHIR R4 endpoint backed by HAPI + Postgres.",
        "description": "Read-write FHIR API with a SMART-on-FHIR discovery document. "
                       "Read paths are anonymous in development; production requires a "
                       "Bearer token obtained through the moh-staff realm. Write paths "
                       "go through the OpenHIM mediator.",
        "use_when": "You're building an external system that consumes Patient, "
                    "Observation, or Immunization resources.",
        "roles": ["R-PUB-ANON (dev)", "Bearer (prod)"], "realm": None,
    },
    {
        "id": "internal-bi", "title": "Internal BI", "tier": "access", "icon": "chart-bar",
        "dev_url": "http://127.0.0.1:8087", "sub": "internal-bi", "path": "/",
        "tagline": "Curated facility marts with column-level lineage.",
        "description": "Role-scoped dashboards built on the postgres-vault marts. "
                       "Decision-makers see governed indicators; analysts get the full "
                       "build plus ad-hoc query; stewards see metadata, lineage, and "
                       "the consent gate's effect on PHI marts.",
        "use_when": "You need a national or regional indicator backed by a versioned mart.",
        "roles": ["R-DEC", "R-CONS-ANA", "R-STEW"], "realm": "moh-staff",
    },
    {
        "id": "submission", "title": "Submission Portal", "tier": "access", "icon": "upload",
        "dev_url": "http://127.0.0.1:8089", "sub": "submit", "path": "/",
        "tagline": "Upload HMIS, Stock, or EPI submissions.",
        "description": "Drop a CSV (HMIS or Stock) or a FHIR Bundle (EPI) and the "
                       "OpenHIM mediator validates it, transforms CSV to FHIR Observations, "
                       "and forwards to HAPI. Every submission gets an OpenHIM transaction "
                       "ID you can reference in audit reports.",
        "use_when": "You're a facility focal point or regional officer pushing your "
                    "monthly returns into NHDW.",
        "roles": ["R-PROD-SUBMIT"], "realm": "moh-staff",
    },
    {
        "id": "dbt-docs", "title": "dbt Documentation", "tier": "access", "icon": "book",
        "dev_url": "http://127.0.0.1:8097", "sub": "dbt-docs", "path": "/",
        "tagline": "Generated lineage and column documentation for every mart.",
        "description": "The dbt project's static documentation site published behind OIDC. "
                       "Click through any mart to see its sources, dependencies, column "
                       "descriptions, and the latest CI run status. The same docs power "
                       "Internal BI's Lineage tab.",
        "use_when": "You're an analyst asking 'where does this column come from?'",
        "roles": ["R-CONS-ANA", "R-STEW"], "realm": "moh-staff",
    },
    {
        "id": "jupyterhub", "title": "Jupyter Workspace", "tier": "access", "icon": "terminal",
        "dev_url": "http://127.0.0.1:8096/hub/login", "sub": "jupyter", "path": "/hub/login",
        "tagline": "Notebooks with OpenBao tokens auto-injected.",
        "description": "Multi-user JupyterHub spawning per-user notebook servers. Each "
                       "notebook receives an OpenBao token in its environment so the user "
                       "can mint short-TTL Postgres credentials directly from the kernel "
                       "and read marts without long-lived secrets.",
        "use_when": "You're a research scientist doing exploratory analysis.",
        "roles": ["R-CONS-SCI"], "realm": "moh-staff",
    },
    {
        "id": "ops-hub", "title": "Operator Hub", "tier": "admin", "icon": "gauge",
        "dev_url": "http://127.0.0.1:8085", "sub": "ops-hub", "path": "/",
        "tagline": "Cross-stack operator landing page.",
        "description": "Internal landing page for the three operator roles. Links to "
                       "every administrative console with a status indicator next to each. "
                       "Sign-in is via the privileged-ops realm, separate from the staff "
                       "realm so audit trails don't cross.",
        "use_when": "You're the on-call operator and want one tab open to everything.",
        "roles": ["R-OPS-ADM", "R-OPS-ENG", "R-OPS-SEC"], "realm": "privileged-ops",
    },
    {
        "id": "cloudbeaver", "title": "Database Workbench", "tier": "admin", "icon": "database",
        "dev_url": "http://127.0.0.1:8086", "sub": "cloudbeaver", "path": "/",
        "tagline": "Web SQL workbench against vault and staging.",
        "description": "CloudBeaver behind an oauth2-proxy. Each user gets a session-scoped "
                       "OpenBao-minted database role with a TTL of one hour. The session "
                       "ID is recorded as the SQL user, so every query traces back to "
                       "a named human in the audit log.",
        "use_when": "You need to write ad-hoc SQL against the warehouse safely.",
        "roles": ["R-OPS-ENG", "R-CONS-ANA"], "realm": "privileged-ops",
    },
    {
        "id": "grafana", "title": "Grafana", "tier": "monitoring", "icon": "monitor",
        "dev_url": "http://127.0.0.1:3000", "sub": "grafana", "path": "/",
        "tagline": "Audit, performance, and ML-serving observability.",
        "description": "Dashboards over Prometheus and Loki. The Audit Events board shows "
                       "every read, write, and policy decision streamed as FHIR AuditEvent "
                       "records. The ML Serving board tracks predictions/second, p95 "
                       "latency, and feature-drift per model version.",
        "use_when": "You need to investigate an incident or watch a model in production.",
        "roles": ["R-OPS-SEC", "R-OPS-ADM"], "realm": "privileged-ops",
    },
    {
        "id": "prometheus", "title": "Prometheus", "tier": "monitoring", "icon": "activity",
        "dev_url": "http://127.0.0.1:9090", "sub": "prometheus", "path": "/",
        "tagline": "Raw time-series metrics and scrape target status.",
        "description": "The Prometheus query UI. Useful for ad-hoc PromQL when a Grafana "
                       "panel doesn't quite answer the question, or for checking whether "
                       "a target is actually being scraped. Read-only: config is managed "
                       "in 4_monitoring/prometheus.yml.",
        "use_when": "You want raw metrics rather than a curated dashboard.",
        "roles": ["R-OPS-SEC", "R-OPS-ENG"], "realm": None,
    },
    {
        "id": "openhim", "title": "OpenHIM Console", "tier": "admin", "icon": "shuffle",
        "dev_url": "http://127.0.0.1:8095", "sub": "openhim", "path": "/",
        "tagline": "Health interoperability mediator console.",
        "description": "Channel management, transaction inspection, and per-route latency "
                       "for HMIS, Stock, and EPI submission flows. Inspect any failed "
                       "submission to see the raw payload and the mediator's validation "
                       "verdict before the data reached HAPI.",
        "use_when": "A submission failed and you need to know why.",
        "roles": ["R-OPS-ADM"], "realm": None,
    },
    {
        "id": "airflow", "title": "Apache Airflow", "tier": "admin", "icon": "workflow",
        "dev_url": "https://airflow.127.0.0.1.nip.io", "sub": "airflow", "path": "/",
        "tagline": "Orchestration of scheduled ML batch inference.",
        "description": "One model-agnostic DAG (ml_batch_predict): every enabled "
                       "batch row in ops_ml.model_deployment becomes a weekly "
                       "ml-runner predict-batch pod. Ingestion and dbt builds run "
                       "as Kubernetes Jobs, not Airflow DAGs.",
        "use_when": "An ML batch run failed, or you need to re-trigger predictions.",
        "roles": ["R-OPS-ENG", "R-OPS-ADM"], "realm": None,
    },
    {
        "id": "minio", "title": "MinIO Console", "tier": "admin", "icon": "archive",
        "dev_url": "http://127.0.0.1:9101", "sub": "minio", "path": "/",
        "tagline": "S3-compatible object store: data lake and ML artifacts.",
        "description": "Hosts the raw landing buckets for source extracts and the MLflow "
                       "artifact store. IAM policies are scoped per service account "
                       "(ingestor, transformer, reader, ML); credentials rotate every "
                       "24 hours via the rotation scripts.",
        "use_when": "You need to inspect a raw extract or an MLflow artifact.",
        "roles": ["R-OPS-ENG", "R-OPS-ADM"], "realm": None,
    },
    {
        "id": "keycloak", "title": "Keycloak", "tier": "admin", "icon": "key",
        "dev_url": "http://127.0.0.1:8090/admin/", "sub": "keycloak", "path": "/admin/",
        "tagline": "Identity provider: realms, users, clients, scopes.",
        "description": "Two staff realms: moh-staff (analysts, decision-makers, stewards) "
                       "and privileged-ops (operators). The nhdw-flat-roles client scope "
                       "emits a top-level roles claim so OpenBao bound_claims works. Fayda "
                       "is a verification-only integration on the public access-request "
                       "form, not a login realm.",
        "use_when": "You need to add a user, rotate a client secret, or inspect a token.",
        "roles": ["KC master admin"], "realm": None,
    },
]


# Sibling surfaces the public SPA links to directly: the three staff sign-in
# destinations (Internal BI, Admin, Workshop) and Keycloak. The SPA cannot read
# env at runtime, so it fetches these resolved bases from /api/access-points.
# The subdomain mapping matches the Ingress hosts and the keycloak-seed Job's
# DASH_BASE_* values, and resolves from NHDW_BASE_DOMAIN like the registry.
def _sibling_bases() -> dict[str, str]:
    return {
        "internal-bi": _url("http://127.0.0.1:8087", "internal-bi", ""),
        "admin":       _url("http://127.0.0.1:8086", "admin",       ""),
        "workshop":    _url("http://127.0.0.1:8085", "workshop",    ""),
        "keycloak":    _url("http://127.0.0.1:8090", "keycloak",    ""),
    }


def _build_registry() -> list[dict]:
    out = []
    for ap in ACCESS_POINTS_TEMPLATE:
        out.append({
            "id":          ap["id"],
            "title":       ap["title"],
            "tier":        ap["tier"],
            "icon":        ap["icon"],
            "tagline":     ap["tagline"],
            "description": ap["description"],
            "use_when":    ap["use_when"],
            "roles":       ap["roles"],
            "realm":       ap["realm"],
            "url":         _url(ap["dev_url"], ap["sub"], ap["path"]),
        })
    return out


# proxy: access-request submissions
# Browser POST to public dashboard to nhdw-gateway. The server-side hop keeps
# the gateway behind one trust boundary (no CORS) and forwards the real client
# IP and user agent into the audit row.
@app.post("/api/proxy/access-requests")
async def proxy_access_request(request: Request) -> JSONResponse:
    from app import marts as _m                              # for GATEWAY_URL
    payload = await request.body()
    headers = {
        "Content-Type": "application/json",
        # Forward original client IP + UA so the gateway can record them.
        "X-Forwarded-For": request.headers.get(
            "x-forwarded-for", request.client.host if request.client else ""),
        "User-Agent":      request.headers.get("user-agent", ""),
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{_m.GATEWAY_URL}/api/public/access-requests",
                              content=payload, headers=headers)
    except httpx.RequestError as e:
        return JSONResponse({"detail": f"gateway unreachable: {e}"},
                             status_code=502)
    return JSONResponse(r.json() if r.headers.get("content-type", "")
                        .startswith("application/json") else {"raw": r.text},
                        status_code=r.status_code)


@app.get("/api/proxy/access-requests/{tracking_id}")
async def proxy_access_request_status(tracking_id: str) -> JSONResponse:
    """Anonymous status lookup, proxied so the browser stays same-origin."""
    from app import marts as _m
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                f"{_m.GATEWAY_URL}/api/public/access-requests/{tracking_id}")
    except httpx.RequestError as e:
        return JSONResponse({"detail": f"gateway unreachable: {e}"},
                             status_code=502)
    return JSONResponse(r.json() if r.headers.get("content-type", "")
                        .startswith("application/json") else {"raw": r.text},
                        status_code=r.status_code)


@app.get("/api/proxy/widget-data/{widget_id}")
async def proxy_widget_data(widget_id: str) -> JSONResponse:
    """Anonymous widget-data fetch, proxied so the browser stays same-origin.

    Forwards to the gateway's /api/permissions/widget/{id}/data with no auth
    header; the gateway maps that to R-PUB-ANON and enforces the widget grant
    (dashboard `public`) and the OPA mart gate before any mart is queried.
    The SPA falls back to its bundled data on any non-200."""
    from app import marts as _m
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                f"{_m.GATEWAY_URL}/api/permissions/widget/{widget_id}/data",
                params={"dashboard_id": "public"})
    except httpx.RequestError as e:
        return JSONResponse({"detail": f"gateway unreachable: {e}"},
                             status_code=502)
    return JSONResponse(r.json() if r.headers.get("content-type", "")
                        .startswith("application/json") else {"raw": r.text},
                        status_code=r.status_code)


# country profile
# The masthead, the map and the geography vocabulary come from the active
# country profile, proxied same-origin. Each route degrades to an empty object
# rather than an error, so the page falls back to the bundled English masthead
# instead of failing.
@app.get("/api/proxy/profile")
async def proxy_profile() -> JSONResponse:
    from app import marts as _m
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(f"{_m.GATEWAY_URL}/api/profile")
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:                                       # noqa: BLE001
        return JSONResponse({"_unavailable": str(e)}, status_code=200)


@app.get("/api/proxy/profile/catalog")
async def proxy_profile_catalog() -> JSONResponse:
    """The active profile's catalog disable list, so the page renderer can drop
    widgets the country turns off (same mechanism internal-bi uses). Read-only."""
    from app import marts as _m
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(f"{_m.GATEWAY_URL}/api/profile/catalog")
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:                                       # noqa: BLE001
        return JSONResponse({"_unavailable": str(e)}, status_code=200)


@app.get("/api/proxy/profile/copy/{surface}")
async def proxy_profile_copy(surface: str) -> JSONResponse:
    from app import marts as _m
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(f"{_m.GATEWAY_URL}/api/profile/copy/{surface}")
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:                                       # noqa: BLE001
        return JSONResponse({"_unavailable": str(e)}, status_code=200)


@app.get("/api/proxy/profile/geo/{name}")
async def proxy_profile_geo(name: str) -> Response:
    """Boundary geometry. Streamed through rather than redirected so the browser
    sees one origin, and cache headers are preserved so the 240 KB world mask is
    fetched once per profile."""
    from app import marts as _m
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(f"{_m.GATEWAY_URL}/api/profile/geo/{name}")
    except httpx.RequestError as e:
        return JSONResponse({"detail": f"gateway unreachable: {e}"}, status_code=502)
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/geo+json"),
                    headers={"Cache-Control": r.headers.get("Cache-Control", "no-store")})


@app.get("/api/proxy/articles")
async def proxy_articles(category: str | None = None, limit: int = 50) -> JSONResponse:
    """Anonymous newsroom/articles feed, proxied same-origin to the gateway's
    /api/public/articles (R-PUB-ANON). The SPA falls back to its bundled
    articles on any non-200."""
    from app import marts as _m
    params: dict[str, str] = {"limit": str(limit)}
    if category:
        params["category"] = category
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{_m.GATEWAY_URL}/api/public/articles", params=params)
    except httpx.RequestError as e:
        return JSONResponse({"detail": f"gateway unreachable: {e}"}, status_code=502)
    return JSONResponse(r.json() if r.headers.get("content-type", "")
                        .startswith("application/json") else {"raw": r.text},
                        status_code=r.status_code)


@app.get("/api/proxy/articles/{article_id}")
async def proxy_article(article_id: str) -> JSONResponse:
    from app import marts as _m
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{_m.GATEWAY_URL}/api/public/articles/{article_id}")
    except httpx.RequestError as e:
        return JSONResponse({"detail": f"gateway unreachable: {e}"}, status_code=502)
    return JSONResponse(r.json() if r.headers.get("content-type", "")
                        .startswith("application/json") else {"raw": r.text},
                        status_code=r.status_code)


# Open-Data catalogue and dataset preview, read from the anonymous
# nhdw-static bucket on the SeaweedFS S3 API (the MINIO_* names are historical).
# Downloads use the public object URL in the manifest; this same-origin proxy
# means the browser needs no object-store or CORS access.
MINIO_INTERNAL = os.environ.get(
    "NHDW_MINIO_INTERNAL", "http://seaweedfs-s3.nhdw.svc.cluster.local:8333").rstrip("/")


@app.get("/api/proxy/catalogue")
async def proxy_catalogue() -> JSONResponse:
    """The public dataset catalogue manifest (nhdw-static/catalogue.json)."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{MINIO_INTERNAL}/nhdw-static/catalogue.json")
    except httpx.RequestError as e:
        return JSONResponse({"detail": f"catalogue unavailable: {e}"}, status_code=502)
    if r.status_code != 200:
        return JSONResponse({"datasets": [], "count": 0}, status_code=r.status_code)
    return JSONResponse(r.json(), status_code=200)


@app.get("/api/proxy/datasets/{dataset_id}/preview")
async def proxy_dataset_preview(dataset_id: str, rows: int = 20) -> JSONResponse:
    """First N rows of a published dataset CSV (for the in-page preview)."""
    import csv as _csv
    import io as _io
    key = f"datasets/{os.path.basename(dataset_id)}.csv"
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(f"{MINIO_INTERNAL}/nhdw-static/{key}")
    except httpx.RequestError as e:
        return JSONResponse({"detail": f"dataset unavailable: {e}"}, status_code=502)
    if r.status_code != 200:
        return JSONResponse({"detail": "dataset not found"}, status_code=404)
    reader = _csv.reader(_io.StringIO(r.text))
    all_rows = list(reader)
    header = all_rows[0] if all_rows else []
    body = all_rows[1:1 + max(1, min(rows, 200))]
    return JSONResponse({"columns": header, "rows": body,
                         "total_rows": max(0, len(all_rows) - 1)})


@app.get("/api/access-points")
async def access_points() -> JSONResponse:
    """Public registry of NHDW surfaces, used by the portal home page.

    `bases` gives the SPA the URLs of the three staff dashboards and Keycloak
    for its sign-in and admin-queue links. NHDW_BASE_DOMAIN swaps localhost
    URLs for Ingress hostnames (https://<sub>.<base>/).
    """
    return JSONResponse({
        "base_domain": NHDW_BASE_DOMAIN or None,
        "bases": _sibling_bases(),
        "items": _build_registry(),
    })


@app.get("/api/series/{slug}")
async def series(slug: str, request: Request) -> JSONResponse:
    """Returns ECharts-shaped series data for one dashboard.

    Every call routes through OPA. R-PUB-ANON's Rego policy allows only
    PRG/FAC subjects at aggregate granularity for purpose=public-health; an
    unmapped slug returns 403 in enforce mode.

    Each slug reads its mart through the gateway and returns an empty
    series when the mart is empty or unavailable.
    """
    client_ip = request.client.host if request.client else "unknown"

    # 1. OPA decision before any data access.
    decision = await opa_check(slug=slug, client_ip=client_ip)
    if not decision.allow:
        await _audit(action="R", resource=f"public-dashboard/{slug} [denied: {decision.reason}]",
                     client_ip=client_ip, outcome="4")
        return JSONResponse({"error": "forbidden", "reason": decision.reason},
                            status_code=403)

    # 2. Audit successful read.
    await _audit(action="R", resource=f"public-dashboard/{slug}", client_ip=client_ip)

    # 3. Serve from the gateway-backed builder; an empty mart or a failed call
    # returns an empty series and the chart shows its empty state.
    handlers = {
        "facility-counts":    marts_db.facility_counts,
        "national-coverage":  marts_db.national_coverage,
        "stock-availability": marts_db.stock_availability,
    }
    if slug in handlers:
        try:
            payload = await handlers[slug]()
            if payload.get("series"):  # only return if the gateway returned non-empty
                return JSONResponse(payload)
            log.info("slug=%s returned empty from gateway", slug)
        except Exception as e:
            log.warning("slug=%s gateway call failed: %s", slug, e)
    return JSONResponse({"x": [], "series": []})


async def _audit(*, action: str, resource: str, client_ip: str, outcome: str = "0") -> None:
    """Push an anonymous, indexable FHIR AuditEvent for a public read to Loki.

    outcome "0" = success, "4" = minor failure / denied (FHIR AuditEvent code).
    """
    payload = {
        "streams": [{
            "stream": {"app": "public-dashboard", "realm": "public",
                       "user": "anonymous", "role": "R-PUB-ANON", "purpose": "public-health",
                       "client_ip": client_ip, "outcome": outcome},
            "values": [[
                str(int(time.time() * 1_000_000_000)),
                json.dumps({
                    "resourceType": "AuditEvent", "action": action, "outcome": outcome,
                    "recorded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "agent": [{"who": {"display": "anonymous"}, "altId": client_ip}],
                    "source": {"observer": {"display": "public-dashboard"}},
                    "entity": [{"what": {"display": resource}}],
                }),
            ]],
        }]
    }
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            await c.post(f"{LOKI_URL}/loki/api/v1/push", json=payload)
    except Exception as e:
        log.warning("audit push failed: %s", e)
