# admin

The operator console for platform administrators, stewards and reviewers. It
authenticates against the `privileged-ops` Keycloak realm (separate from the
`moh-staff` realm the other dashboards share) and covers infrastructure health
and topology, alert routing, data quality and lineage, MDM stewardship, column
classification, model monitoring and project review, users and access requests,
roles and permissions, SSO identity sources, dashboard control and operator
documentation.

## Layout

```
app/main.py          FastAPI BFF: serves the SPA, /auth/*, and proxies /gw/* to the gateway
catalog/             dashboard.yaml and one pages/admin--<page>.yaml per page
static/              icon sprite (assets/icons.html), brand images, base CSS
ui/                  React + Vite SPA
  src/runtime/       PageRenderer, widget registry, fallback page map
  src/widgets/<id>/  one module per catalog widget (index.tsx + widget.yaml)
  src/topology/      framework-agnostic Cytoscape renderer for the topology page
  src/api/           typed fetch wrappers for the gateway routes
tests/               Playwright regressions against a deployment (see tests/README.md)
Dockerfile           two-stage build: Vite bundle, then the FastAPI runtime
```

## How pages are assembled

Each admin page holds a single widget (`admin--backups` is declared with none
yet). `catalog/pages/*.yaml` name the page and the widget it places; each `ui/src/widgets/<id>/widget.yaml` describes the
widget. `bundle/scripts/collect-catalog.py` combines these into `catalog.json`,
which the security-seed loader writes to the `ops_perm` tables.

At runtime the SPA fetches its resolved spec from
`/gw/permissions/dashboard/admin/spec`: the gateway returns only the pages and
widgets the caller's role is granted. `runtime/registry.ts` discovers every
`src/widgets/<id>/index.tsx` at build time, and `runtime/PageRenderer.tsx`
renders the active page's granted widget ids. While the spec is loading or
unavailable, the page falls back to `runtime/pages.ts`; an id with no module
renders a visible placeholder. The Sidebar shows catalog-gated nav items only
when the role holds the page's widget.

## Gateway access

The browser talks only to the BFF. `app/main.py` exposes `/auth/me` and
`/auth/config` (Keycloak settings for the SPA's PKCE flow in `api/useAuth.ts`)
and forwards `/gw/{path}` to `nhdw-gateway` at `/api/{path}` with the caller's
bearer token. With `DEV_AUTH=true` it skips Keycloak and relays a mock
identity (`DEV_MOCK_USER`, `DEV_MOCK_ROLE`) for local development.

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

`tests/` holds Playwright walkthroughs that sign in through Keycloak and run
against a deployed console; `tests/README.md` lists them and how to run them.
