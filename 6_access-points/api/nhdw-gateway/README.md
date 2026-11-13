# nhdw-gateway

FastAPI service that is the single HTTP data API of the platform. Every
dashboard (public, internal BI, admin, workshop) and every direct client
(notebooks, BI tools, scripts holding a personal access token) reads warehouse
data and governed platform state through it. It holds no data of its own: it
authenticates the caller, asks OPA for a decision, applies the row filter and
column masking that decision carries, reads through Trino or the `nhdw_ops`
Postgres database, and writes an audit row for every request.

## Layout

```
app/
  main.py              app factory, router registration, audit and metrics middleware, startup self-tests
  settings.py          environment-driven settings (pydantic-settings)
  auth.py              credential resolution: Keycloak JWT, personal access token, mock headers
  opa.py               client for the nhdw.authz decide rule
  permissions.py       dashboard/page/widget/filter resolver over the ops_perm grant tables
  mart_attrs.py        mart catalog (OPA input attributes) and column classification; startup self-tests
  dimensions.py        scope dimensions: dimension -> mart columns, filter -> dimension, dimension -> OPA key
  masking.py           column masking (drop, hash, partial, tokenize, generalize)
  fetchers.py          widget data fetchers (one per mart-backed widget)
  trino_client.py      Trino access with a statement allowlist
  ops_db.py            asyncpg pool for nhdw_ops: audit log, export registry, identity cache
  minio_client.py      S3 client for the SeaweedFS object store (module name is historical)
  keycloak_admin.py    Keycloak Admin API calls used by access-request provisioning
  mailer.py            SMTP mailer for notifications and onboarding
  metrics.py           Prometheus RED metrics
  ml_integration.py    integration of an approved project's model into the ML catalogue
  topology_graph.py, traffic_providers.py   live Kubernetes topology graph and per-edge traffic
  routes/              one module per router (see below)
  routes/chat_configs/ tool catalogue, intents and system prompt for the internal-BI assistant
tests/                 unit, integration and live-deployment tests
Dockerfile             image nhdw/gateway (python:3.12-slim, uvicorn on port 8000)
requirements.txt       pinned Python dependencies
```

## How a request is authorised

1. **Identity** (`auth.get_user`). A Bearer token starting `nhdw_pat_` is a
   personal access token, looked up by prefix and SHA-256 hash in
   `ops_perm.api_token`; its declared scope can only narrow the owner's scope.
   Any other Bearer token is a Keycloak access token: the `iss` claim must be in
   `KEYCLOAK_ALLOWED_ISSUERS`, the signature is verified against that realm's
   JWKS and the audience must be `KEYCLOAK_AUDIENCE` or one of
   `KEYCLOAK_EXTRA_AUDIENCES`. Roles come from `realm_access.roles` or the flat
   `roles` claim; `idp_trust` marks low-trust federated logins. `X-Mock-User`
   and `X-Mock-Role` headers are honoured only with `DEV_AUTH=true` (local
   development) or with `GATEWAY_DEMO_IMPERSONATION=true` plus a matching
   `X-Mock-Secret`. A request with no credential becomes the anonymous
   `R-PUB-ANON` principal when `GATEWAY_PUBLIC_ANON` (or `DEV_AUTH`) is set and
   is rejected with 401 otherwise.
2. **Grants** (`permissions.py`). Role assignments come only from
   `ops_perm.user_role`; a token's role claim is identity, not a grant. The
   resolver returns only the dashboards, pages, widgets and filter values the
   caller's roles grant, capped by role scope (`ops_perm.role_scope`)
   intersected with user scope (`ops_identity.user_scope`). Admin routes check
   RBAC capabilities (`ops_perm.role_capability`) directly.
3. **OPA decision** (`opa.decide`). For mart reads the gateway looks up the
   mart's subject, data class, granularity and sensitivity in the catalog
   (`mart_attrs`; uncatalogued marts are denied), adds the caller's role, trust
   tier and data scope and the relation's classified columns, and posts the
   input to `POST /v1/data/nhdw/authz/decide`. With `OPA_ENFORCE=true` a deny is
   a 403; if OPA is unreachable the request fails closed unless
   `OPA_FAIL_OPEN=true`.
4. **Row filter and masking.** The decision's `row_filter` obligation
   (`{dimension: [values]}`) is ANDed into the query's WHERE clause through the
   dimension-to-column map in `dimensions.py`. Its `masked_columns` are
   transformed by each column's mask class (`masking.py`); every path fails
   closed (unknown class or missing `MASKING_HMAC_KEY` means the column is
   dropped). The free-form SQL editor and `/api/marts/{schema}/{table}` cannot
   row-filter arbitrary SQL, so they refuse callers whose scope is restricted.
5. **Trino.** Queries run through `trino_client`, which admits only
   statements starting with SELECT, SHOW, DESCRIBE, EXPLAIN or WITH.
6. **Audit.** Middleware writes one `ops_audit.gateway_log` row per request
   (including 401s); handlers that log a richer row suppress the base row.

At startup the gateway runs two self-tests against the live OPA: a role-by-mart
allow/deny matrix and a masking matrix. Under `OPA_ENFORCE=true` a mismatch
stops the process from starting.

## Route groups

All routers are mounted in `app/main.py`; prefixes are defined in each module.

| Prefix | Module | Purpose |
|---|---|---|
| `/api/me` | `me.py` | caller identity, password reset, sessions |
| `/api/permissions` | `permissions.py` | dashboard spec, filter values, widget data |
| `/api/marts` | `marts.py` | catalogued mart list and direct mart reads |
| `/api/sql` | `sql_lab.py` | governed SQL editor execution |
| `/api/exports`, `/api/reports` | `exports.py`, `reports.py` | mart exports and templated reports with presigned downloads |
| `/api/queries` | `queries.py` | saved queries |
| `/api/ct` | `ct.py` | Supply Control Tower plan and plan decisions |
| `/api/profile` | `profile.py` | country profile (copy, geometry, branding, reference data); public |
| `/api/ml`, `/api/graduation` | `ml.py`, `graduation.py` | model registry, predictions, runner Jobs; project review and model integration |
| `/api/projects`, `/api/datasets`, `/api/artifacts`, `/api/groups`, `/api/workspace` | workshop modules | projects and files, dataset builder, artifact catalogue, groups, JupyterHub labs |
| `/api/mdm` | `mdm.py`, `mdm_workspace.py`, `mdm_approvals.py` | master-data stewardship, read views, maker-checker approvals |
| `/api/classification` | `classification.py` | column classification and unclassified-column detector |
| `/api/dq` | `dq.py` | data-quality summary, lineage, flags and detector |
| `/api/alerts` | `alerts.py` | alert routing, subscriptions, evaluation, notifications |
| `/api/observability`, `/api/topology`, `/api/audit` | ops modules | Prometheus and Loki proxy, topology graph, audit log |
| `/api/perm-admin`, `/api/api-tokens`, `/api/policy` | admin modules | permission model CRUD, personal access tokens, OPA decision log and what-if evaluator |
| `/api/public/access-requests`, `/api/admin/access-requests` | `access_requests.py` | public access requests and admin provisioning |
| `/api/public/articles`, `/api/docs` | `public_articles.py`, `docs.py` | public newsroom articles (anonymous); operator documentation from the object store |
| `/api/chat` | `chat.py` | internal-BI assistant (LLM tool calling) |
| `/api/demo` | `demo.py` | demo-control contract (mutations need `DEMO_CONTROL_TOKEN`) |
| `/health`, `/metrics` | `main.py` | liveness and Prometheus metrics |

FastAPI serves the OpenAPI description at `/docs` and `/openapi.json`.

## Configuration

Settings are environment variables read by `app/settings.py`; values are set
in the Kubernetes manifests and Secrets, never in the image.

| Group | Settings |
|---|---|
| Ops database | `OPS_PG_HOST`, `OPS_PG_PORT`, `OPS_PG_DATABASE`, `OPS_PG_USER`, `OPS_PG_PASSWORD` |
| Object store | `MINIO_ENDPOINT`, `MINIO_PUBLIC_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `EXPORTS_BUCKET`, `ARTIFACTS_BUCKET`, `EXPORT_PRESIGN_TTL_S` (the `MINIO_*` names are historical; the store is SeaweedFS) |
| Trino | `TRINO_HOST`, `TRINO_PORT`, `TRINO_USER` |
| OPA | `OPA_URL`, `OPA_ENFORCE`, `OPA_FAIL_OPEN`, `OPA_TIMEOUT_S` |
| Keycloak | `KEYCLOAK_ALLOWED_ISSUERS`, `KEYCLOAK_PUBLIC_BASE`, `KEYCLOAK_INTERNAL_BASE`, `KEYCLOAK_AUDIENCE`, `KEYCLOAK_EXTRA_AUDIENCES`, `KEYCLOAK_ADMIN_USER`, `KEYCLOAK_ADMIN_PASSWORD`, `KEYCLOAK_PROVISION_REALM` |
| Access modes | `DEV_AUTH`, `GATEWAY_PUBLIC_ANON`, `GATEWAY_DEMO_IMPERSONATION`, `GATEWAY_IMPERSONATION_SECRET` |
| Email and alerts | `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_TLS`, `SMTP_USER`, `SMTP_PASS`, `ALERT_EVAL_MODEL`, `ALERT_EVAL_TOKEN` |
| Service tokens | `DQ_DETECT_TOKEN`, `DEMO_CONTROL_TOKEN` |

Some modules read further variables directly, including `MASKING_HMAC_KEY`
(masking), `MLFLOW_URL` and `ML_RUNNER_IMAGE` (ML), `PROMETHEUS_URL` and
`LOKI_URL` (observability), `JUPYTERHUB_URL` and `JUPYTERHUB_API_TOKEN`
(workspace), `OLLAMA_URL` and `NHDW_LLM_MODEL` (chat), `NHDW_PROFILE_DIR` and
`NHDW_PROFILE` (country profile), and `LOG_LEVEL`. `DEV_AUTH` must never be
true on an exposed gateway.

## Tests

| File | Kind | How to run |
|---|---|---|
| `tests/test_masking.py` | unit tests of the masking primitives; no database or OPA | `python3 tests/test_masking.py` from the gateway directory |
| `tests/test_mdm_workspace.py`, `tests/test_mdm_approvals.py` | MDM read routes and maker-checker flow against throwaway Postgres 16 and Trino containers seeded with synthetic fixtures (`tests/fixtures/`) | `bash 6_access-points/api/nhdw-gateway/tests/run_mdm_tests.sh` (needs Docker; `PG_PORT` and `TRINO_PORT` override the ports) |
| `tests/test_masking_e2e.sh` | masking negative path against a live deployment, as several identities | `NHDW_HOSTBASE=<host base> 6_access-points/api/nhdw-gateway/tests/test_masking_e2e.sh` |
| `tests/test_public_access_request.sh` | public access-request endpoint against a running cluster (port-forward and psql) | `bash 6_access-points/api/nhdw-gateway/tests/test_public_access_request.sh` |

CI (`.github/workflows/test.yml`) installs `requirements.txt`, checks that
`app.main` imports, runs `test_masking.py` and runs `run_mdm_tests.sh`. The OPA
policy the gateway calls is tested separately with
`opa test 7_security/opa/policies`.

## Deployment

The image is built from this directory. The build copies
`2_pipelines/ml/registry/validators.py` into the build context first, because
the Dockerfile bakes it in for the graduation validators. Kubernetes manifests
are `1_infrastructure/k8s/base/nhdw-gateway.yaml` (Deployment and Service on
port 8000, ServiceAccount `nhdw-gateway`), `nhdw-gateway-hpa.yaml` (one to six
replicas) and `gateway-rbac.yaml` (namespace Role used to read pods, services, ConfigMaps,
Secrets, logs and events for the topology views, scale Deployments and StatefulSets, and
create and delete runner Jobs); the Helm chart carries the same objects under
`1_infrastructure/helm/nhdw/templates/gateway*.yaml`. The ingress publishes the
gateway at `gateway.<host base>`. Deployed manifests set `OPA_ENFORCE=true`,
`OPA_FAIL_OPEN=false`, `DEV_AUTH=false`, `GATEWAY_PUBLIC_ANON=true` and
`GATEWAY_DEMO_IMPERSONATION=true` (view-as through `X-Mock-*` headers, accepted
only with the `IMPERSONATION_SECRET` from `nhdw-gateway-secrets`; set it to
`false` for any deployment that is not a demonstration), and take credentials
from the `nhdw-gateway-secrets`, `keycloak-secrets`, `minio-secrets`,
`smtp-secrets`, `alert-eval-secrets`, `dq-detect-secrets`,
`demo-control-secrets` and `jupyterhub-secrets` Secrets. See
`docs/deployment.md` for the platform deployment procedure.
