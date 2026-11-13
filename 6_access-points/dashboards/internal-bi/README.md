# internal-bi

The analytics dashboard for Ministry staff (the `moh-staff` Keycloak realm):
decision-makers, programme and regional managers, analysts and partners. Pages
cover Overview, Disease surveillance, Resource (supply), Service delivery, the
Supply Control Tower, Export (reports, saved queries, SQL editor, export audit)
and Settings, plus a chat assistant. Every figure comes from governed marts
through the gateway; the data in this repository's configuration is
demonstration data (see `docs/data-provenance.md`).

## Layout

```
app/main.py           FastAPI BFF: serves the SPA, /auth/*, and proxies /gw/* to the gateway
catalog/              dashboard.yaml, pages/, sections/, widgets.yaml (chrome widgets without a folder)
static/               icon sprite, background images, region GeoJSON, Control Tower map tiles
ui/                   React + Vite SPA
  src/runtime/        PageRenderer, widget/detail/modal registries, filter bus, reactivity,
                      app shell (see runtime/APP-SHELL.md)
  src/widgets/<id>/   one module per catalog widget (widget.ts + widget.yaml); _shared/ holds
                      chart, table, geography and reference helpers
  src/ct/             Supply Control Tower (React components, MapLibre map, store)
  src/sections/       shell furniture: Sidebar, Topbar, CatalogPage, chat panel
  src/api/            spec client, auth hook, ML client
tests/                Playwright and Python regressions against a deployment
Dockerfile            two-stage build: Vite bundle, then the FastAPI runtime
```

## How pages are assembled

The catalog is authored as data. `catalog/pages/*.yaml` list each page's
sections and global filters, `catalog/sections/*.yaml` place widgets on a
12-column grid (`col_span`, `row_span`, `ordering`), and each
`ui/src/widgets/<id>/widget.yaml` describes its widget (title, kind,
requirements, the filters it reacts to). `bundle/scripts/collect-catalog.py`
combines these into `catalog.json` for the security-seed loader, which writes
the `ops_perm` tables.

At runtime the SPA fetches `/gw/permissions/dashboard/internal-bi/spec`. The
gateway resolves it for the caller's role: only granted pages, sections and
widgets, each widget with its `data_url`; pages with no visible content are
dropped. `sections/CatalogPage.tsx` hands the resolved page to
`runtime/PageRenderer.ts`, which draws the section bands and widget grid.
`runtime/registry.ts` maps each catalog widget id to its `widget.ts` module
(discovered at build time); an id with no module renders a visible "no
renderer" placeholder. Widgets fetch their rows through `ctx.fetchData()`,
re-render when the global filters they declare in `reactsTo` change, and show
an explicit empty state when the gateway returns nothing.

The Control Tower page uses the same mechanism: its `ct-*` widgets mount the
React components in `src/ct/`, which share one store and load the plan from
`/gw/ct/plan`, which reads the `mart_ct_*` marts built from the eLMIS
planning position. `src/ct/data/plan.ts` supplies the types; its data arrays
are a precomputed dispatch plan that the SPA does not render, and that
`synthesize_dispatch.py` also loads into the HCMIS replica as a planning
fixture (see `docs/data-provenance.md`).

## Gateway access

The browser talks only to the BFF. `app/main.py` exposes `/auth/me` and
`/auth/config` (Keycloak settings for the PKCE flow in `api/useAuth.ts`) and
forwards `/gw/{path}` to `nhdw-gateway` at `/api/{path}` with the caller's
bearer token. Public country-profile reads (masthead, map geometry, branding)
pass through without a token; every other path needs a session. Chat and SQL
calls get a 240 s timeout, everything else 20 s. With `DEV_AUTH=true` the BFF
relays a mock identity (`DEV_MOCK_USER`, `DEV_MOCK_ROLE`) instead of
validating a Keycloak token.

## Development

From `ui/`, the scripts in `package.json`:

| Command | Effect |
|---|---|
| `npm run dev` | Vite dev server |
| `npm run build` | type-check (`tsc -b`) and build into `../dist` |
| `npm run preview` | serve the built bundle |

Environment variables read by `app/main.py`: `GATEWAY_URL`, `DEV_AUTH`
(default `false`), `DEV_MOCK_USER`, `DEV_MOCK_ROLE`, `KEYCLOAK_BASE`,
`KEYCLOAK_PUBLIC_BASE`, `OIDC_REALM`, `OIDC_CLIENT_ID`, `OIDC_REDIRECT_URI`,
`APP_BASE`.

## Tests

`tests/` holds end-to-end checks that sign in through Keycloak and run against
a deployment: Playwright scripts (`*.cjs`, `*.mjs`) per page and role, and
`test_oidc_roles.py` for the OIDC login chain and role-differentiated
rendering. Each file's header states what it covers and how to run it
(typically `NODE_PATH=<playwright-install>/node_modules node tests/<file>`,
with `SEED_PASSWORD` set).
