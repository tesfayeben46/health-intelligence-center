"""NHDW gateway: the HTTP data API behind every dashboard and direct API
client (notebooks, BI tools, dataset generators).

Routers live in `app/routes/` and are mounted under `/api/*` (see the
include_router list below). Core modules:
   auth.py          credential resolution (Keycloak JWT, personal access token, dev header)
   opa.py           OPA decision client (fails closed under OPA_ENFORCE=true)
   permissions.py   permission resolver for dashboard specs and widget data
   masking.py       column masking applied to query results
   ops_db.py        asyncpg pool for the nhdw_ops database (audit, exports, workflow state)
   mart_attrs.py    mart catalog attributes for OPA input, plus the startup self-tests
   minio_client.py  S3 client for the exports bucket (SeaweedFS; module name is historical)
   trino_client.py  federated read engine

See docs/architecture.md for the system-level picture.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import mart_attrs, metrics, opa, ops_db
from .routes import (access_requests, alerts, api_tokens, artifacts, audit,
                      chat, classification, ct, datasets, demo, docs, dq, exports,
                      graduation, groups, marts,
                      mdm, mdm_approvals, mdm_workspace, me, ml, observability, perm_admin,
                      permissions as permissions_route,
                      policy, profile, projects, public_articles, queries,
                      reports, sql_lab, topology, workspace)
from .settings import settings

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
log = logging.getLogger("gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("nhdw-gateway boot, ops_db=%s, trino=%s:%s, dev_auth=%s, opa_enforce=%s",
             settings.OPS_PG_HOST, settings.TRINO_HOST, settings.TRINO_PORT,
             settings.DEV_AUTH, settings.OPA_ENFORCE)
    # Eagerly open the ops_db pool so health checks pass deterministically.
    await ops_db.ensure_pool()
    # Startup OPA self-test: walk a known (role, mart) matrix and check that
    # OPA agrees with the mart_attrs catalog. Fails the boot under
    # OPA_ENFORCE=true; logs a warning otherwise.
    try:
        results = await mart_attrs.run_self_test(opa)
        failures = [(c, r) for c, observed, r in results
                    if observed != c.expect_allow]
        for case, observed, reason in results:
            ok = (observed == case.expect_allow)
            log.log(logging.INFO if ok else logging.WARNING,
                    "opa self-test %s: role=%s mart=%s.%s expect=%s observed=%s, %s",
                    "PASS" if ok else "FAIL",
                    case.role, case.schema, case.table,
                    case.expect_allow, observed, reason)
        if failures and settings.OPA_ENFORCE:
            raise RuntimeError(
                f"OPA self-test failed for {len(failures)} case(s) under OPA_ENFORCE=true; "
                f"refusing to start. Check mart_attrs catalog + role policies.")

        # Masking self-test: check that the live OPA's masked_columns match the
        # per-role reveal sets, so reveal grants and the deployed Rego cannot drift.
        mask_results = await mart_attrs.run_mask_self_test(opa)
        mask_failures = [(c, obs) for c, obs, _r in mask_results
                         if obs != sorted(c.expect_masked)]
        for case, observed, reason in mask_results:
            ok = (observed == sorted(case.expect_masked))
            log.log(logging.INFO if ok else logging.WARNING,
                    "mask self-test %s: role=%s trust=%s expect=%s observed=%s, %s",
                    "PASS" if ok else "FAIL", case.role, case.idp_trust,
                    sorted(case.expect_masked), observed, case.note)
        if mask_failures and settings.OPA_ENFORCE:
            raise RuntimeError(
                f"masking self-test failed for {len(mask_failures)} case(s) under "
                f"OPA_ENFORCE=true; refusing to start. Check role reveal sets + "
                f"the deployed common.rego.")
    except Exception as e:                                       # noqa: BLE001
        if settings.OPA_ENFORCE:
            raise
        log.warning("opa self-test setup error (enforce off, continuing): %s", e)
    yield
    await ops_db.close_pool()


app = FastAPI(title="NHDW Gateway", version="0.1.0", lifespan=lifespan)


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    """Prometheus scrape target (job `nhdw-gateway`). Unauthenticated by design:
    it exposes only aggregate RED counters (no row data, no identifiers) and is
    in the audit-skip set below."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# Paths that would only add noise to the access log (probes, API docs, static).
_AUDIT_SKIP_PREFIXES = (
    "/health", "/metrics", "/docs", "/redoc", "/openapi.json",
    "/favicon.ico", "/static",
)


def _sniff_principal(request: Request) -> tuple[str | None, str | None]:
    """Best-effort actor id for the catch-all audit row, without running the
    auth dependency (the middleware sits above route resolution).

    Returns (kc_sub_uuid_or_None, auth_hint). The Bearer JWT is decoded
    *unverified* and used only to label the audit row, never to authorise.
    PAT and mock principals carry no JWT sub, so they leave a short marker
    instead. Never raises: any parse failure yields (None, hint)."""
    try:
        authz = request.headers.get("authorization") or ""
        if authz.lower().startswith("bearer "):
            token = authz.split(" ", 1)[1].strip()
            if token.startswith("nhdw_pat_"):
                return None, "pat"
            try:
                from jose import jwt
                sub = jwt.get_unverified_claims(token).get("sub")
                if isinstance(sub, str):
                    try:
                        return str(uuid.UUID(sub)), None   # real kc_sub
                    except ValueError:
                        return None, "jwt"                 # sub isn't a uuid
                return None, "jwt"
            except Exception:                              # noqa: BLE001
                return None, "jwt"
        mock = request.headers.get("x-mock-user")
        if mock:
            try:
                return str(uuid.UUID(mock)), "mock"        # dev mock kc_sub
            except ValueError:
                return None, f"mock:{mock[:8]}"
        return None, None                                  # anonymous / public
    except Exception:                                      # noqa: BLE001
        return None, None


@app.middleware("http")
async def audit_all_requests(request: Request, call_next):
    """Catch-all access log: one ops_audit.gateway_log row per request.

    Covers every endpoint, including 401s that never reach a handler. Handlers
    that call log_gateway_query() write a richer row and set a request-scoped
    marker (ops_db); this middleware then skips its base row, so each request
    yields exactly one row. Logging is best-effort and never affects the
    response. ops_audit.gateway_log is append-only and has no retention job in
    this service."""
    path = request.url.path
    if path.startswith(_AUDIT_SKIP_PREFIXES):
        return await call_next(request)

    marker = ops_db.new_request_log_marker()
    t0 = time.perf_counter()
    status = 500  # default covers an unhandled exception in call_next
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        # RED metrics for every request, including handlers that write their own
        # audit row. Best-effort; never affects the response.
        try:
            metrics.observe(request.method, path, status,
                            time.perf_counter() - t0)
        except Exception:                                  # noqa: BLE001
            log.debug("metrics observe failed for %s", path, exc_info=True)
        try:
            if not marker["logged"]:
                kc_sub, hint = _sniff_principal(request)
                decision = ("error" if status >= 500
                            else "deny" if status in (401, 403)
                            else "allow")
                await ops_db.log_gateway_request(
                    kc_sub=kc_sub, method=request.method, path=path,
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                    http_status=status, policy_decision=decision,
                    client_ip=(request.client.host if request.client else None),
                    user_agent=request.headers.get("user-agent"),
                    auth_hint=hint)
        except Exception:                                  # noqa: BLE001
            # Audit logging must never break the request/response cycle.
            log.debug("audit middleware failed for %s", path, exc_info=True)


app.include_router(me.router)
# The country profile is unauthenticated by design: it carries the masthead and
# the map, which the public dashboard needs before sign-in.
app.include_router(profile.router)
# Demo control: /health is open so a console can show status before sign-in;
# everything that mutates requires DEMO_CONTROL_TOKEN.
app.include_router(demo.router)
app.include_router(marts.router)
app.include_router(ct.router)
app.include_router(exports.router)
app.include_router(audit.router)
app.include_router(observability.router)
app.include_router(ml.router)
app.include_router(topology.router)
app.include_router(docs.router)
app.include_router(workspace.router)
app.include_router(graduation.router)
app.include_router(mdm.router)
app.include_router(mdm_workspace.router)   # MDM workspace: read side of the stewardship console
app.include_router(mdm_approvals.router)   # MDM maker-checker (proposals -> approval)
app.include_router(classification.router)
app.include_router(access_requests.public_router)
app.include_router(access_requests.admin_router)
app.include_router(public_articles.public_router)
app.include_router(queries.router)
app.include_router(reports.router)
app.include_router(projects.router)
app.include_router(sql_lab.router)
app.include_router(datasets.router)
app.include_router(policy.router)
app.include_router(alerts.router)
app.include_router(dq.router)
app.include_router(artifacts.router)
app.include_router(groups.router)
app.include_router(permissions_route.router)
app.include_router(perm_admin.router)
app.include_router(api_tokens.router)
app.include_router(chat.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "nhdw-gateway"}
