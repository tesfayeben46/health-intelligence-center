# workshop

The community workshop for researchers and analysts in the `moh-staff`
Keycloak realm. Users create projects with a file space and an MLflow
experiment, open JupyterLab on a project, build and share datasets, browse and
run published models, work in groups, and submit projects for public review
(reviewed in the admin console). Pages: Workspace, Projects, Datasets, Models,
Help and Settings.

## Layout

```
app/main.py           FastAPI BFF: serves the SPA, /auth/*, and proxies /gw/* to the gateway
catalog/              dashboard.yaml, pages/workshop--*.yaml, sections/workshop--*.yaml
static/               icon sprite, images, base CSS
ui/                   React + Vite SPA
  src/runtime/        PageRenderer, app shell (master-detail + modal host), registries,
                      filter bus, entity data hook
  src/widgets/<id>/   one module per catalog widget (widget.ts + widget.yaml)
  src/modals/<id>/    modal flows: project-create, project-submit, dataset-detail,
                      compute-request
  src/api/            typed clients for projects, artifacts, ML, groups, spec and auth
Dockerfile            two-stage build: Vite bundle, then the FastAPI runtime
```

## How pages are assembled

`catalog/pages/*.yaml` list each page's sections, `catalog/sections/*.yaml`
place widgets on a 12-column grid, and each `ui/src/widgets/<id>/widget.yaml`
describes its widget. `bundle/scripts/collect-catalog.py` combines these with
the other dashboards' catalogs into `catalog.json` for the security-seed
loader, which writes the `ops_perm` tables.

At runtime the SPA fetches `/gw/permissions/dashboard/workshop/spec`, which the
gateway resolves for the caller's role. `sections/CatalogPage.tsx` passes the
resolved page to `renderAppShell` (`runtime/AppShell.ts`), which draws the
section grid and hosts the master-detail and modal layers; the contracts are
described in `internal-bi/ui/src/runtime/APP-SHELL.md`, whose runtime this one
copies. Widgets read and write ops entities (projects, datasets, models,
groups) with `authFetch` against gateway REST routes that enforce their own
access rules, and coordinate through the filter bus (for example `ws_project`
and `ws_model` for master-detail selection).

## Gateway access

The browser talks only to the BFF. `app/main.py` exposes `/auth/me` and
`/auth/config` (Keycloak settings for the PKCE flow in `api/useAuth.ts`) and
forwards `/gw/{path}` to `nhdw-gateway` at `/api/{path}` with the caller's
bearer token. With `DEV_AUTH=true` it relays a mock identity (`DEV_MOCK_USER`,
`DEV_MOCK_ROLE`) instead of validating a Keycloak token.

## Development

From `ui/`, the scripts in `package.json`:

| Command | Effect |
|---|---|
| `npm run dev` | Vite dev server |
| `npm run build` | type-check (`tsc -b`) and build into `../dist` |
| `npm run preview` | serve the built bundle |

Configuration is by environment variable; `.env.example` lists them
(`GATEWAY_URL`, `DEV_AUTH`, `DEV_MOCK_USER`, `DEV_MOCK_ROLE`, `SESSION_SECRET`,
`KEYCLOAK_*`, `OIDC_*`, `APP_BASE`); `SESSION_SECRET` is listed but not read by
the current app. `DEV_AUTH` defaults to `true` in
`app/main.py`, so deployments must set it to `false`.

## Tests

The workshop has no test directory of its own. The cross-dashboard login test
`../tests/e2e-oidc-login.js` checks that a workshop visit reuses the
internal-bi `moh-staff` session.
