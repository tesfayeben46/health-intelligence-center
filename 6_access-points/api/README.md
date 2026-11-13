# API access points

HTTP services through which dashboards, operators and external clients reach the
platform.

## Layout

| Directory | What it is | Deployed |
|---|---|---|
| `nhdw-gateway/` | FastAPI data API used by every dashboard and API client: Keycloak identity, OPA decisions, row filtering and column masking, Trino and `nhdw_ops` reads, audit logging. See `nhdw-gateway/README.md`. | Yes: `1_infrastructure/k8s/base/nhdw-gateway.yaml`, Helm `templates/gateway*.yaml` |
| `tool-gate/` | Generic role-aware OIDC reverse proxy placed in front of operator tools. One image, one Deployment per tool, configured by environment (`UPSTREAM_URL`, `TOOL_NAME`, `ALLOWED_ROLES`, `OIDC_*`). | Yes: `1_infrastructure/k8s/base/tool-gates.yaml` (MLflow, Prometheus, Alertmanager, Headlamp, SeaweedFS) |
| `openhim/` | OpenHIM interoperability layer for producer submissions: a FastAPI mediator (`mediator/app/main.py`) that validates HMIS and stock CSV and EPI FHIR Bundles and forwards FHIR transactions to HAPI, a channel bootstrap script (`scripts/bootstrap-channels.sh`) and console configuration. | No: not deployed by the manifests in `1_infrastructure/`. |
| `fhir-server/` | Configuration for a HAPI FHIR R4 server behind nginx serving a static SMART-on-FHIR configuration document (`smart-config/`), plus an example environment file. | No: not deployed by the manifests in `1_infrastructure/`. |

## tool-gate

Each tool gate authenticates the user against Keycloak (sessions from several
realms are accepted, so a user signed in to a dashboard is admitted without a
second login), checks on every request that the session holds a role in
`ALLOWED_ROLES` (403 otherwise), and proxies HTTP and WebSocket traffic to the
upstream. It can inject an upstream credential, such as a service-account
bearer for Headlamp, so the browser never holds it.

Tests in `tool-gate/tests/` are Playwright scripts that run against a live
deployment (`node tool-gate/tests/<file>.mjs`, with `SEED_PASSWORD` set for the
seeded test users).

## Notes

The FHIR source the ingestion pipelines read (the MFR facility registry) is a
separate replica under `0_external/sources/MFR/`, not the `fhir-server/`
configuration here.
