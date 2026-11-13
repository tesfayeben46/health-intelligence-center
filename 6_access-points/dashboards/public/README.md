# public

The anonymous public portal: home page with headline indicators and news, an
open-data catalogue with dataset previews, an articles newsroom, a dashboards
launcher and the public access-request form (with optional Fayda email-OTP
identity verification). There is no sign-in and no patient-level data: reads go
to the gateway as `R-PUB-ANON`, which exposes a curated set of public marts
that enforce k-anonymity (k >= 5) in SQL. All content in this repository's
configuration is demonstration data (see `docs/data-provenance.md`).

## Layout

```
app/main.py          FastAPI BFF: serves the SPA and the /api/* routes the SPA calls
app/marts.py         gateway reads for the public marts
app/opa_client.py    OPA check for the series endpoints
catalog/             dashboard.yaml, pages/public--*.yaml, widgets.yaml (widgets without a folder)
static/assets/       icon sprite and images
ui/                  React + Vite SPA
  src/runtime/       page map (pages.ts), PageRenderer, widget registry
  src/widgets/<id>/  one module per widget (index.tsx, optional widget.yaml)
  src/hooks/         data hooks (widget data, datasets, articles, profile), charts, hero globe
  src/data/          bundled fallback articles and dataset catalogue
tests/               test_access_form.py (Playwright)
Dockerfile           two-stage build: Vite bundle, then the FastAPI runtime
```

## How pages are assembled

The public SPA has no per-user spec. `ui/src/runtime/pages.ts` names, for each
page, the widgets rendered edge-to-edge (`fullBleed`) and those wrapped in the
shared content well (`content`); the GovBar and footer are persistent chrome and
the access and dashboards modals sit outside the page flow.
`runtime/registry.ts` discovers every `src/widgets/<id>/index.tsx` at build
time, and `PageRenderer.tsx` renders the active page's widgets by id; an
unknown id renders a visible placeholder. The active country profile's
`catalog.yaml` can disable widgets (for example `pub-insights` or
`pub-zm-supply`, depending on the country), and a page whose widgets are all
disabled shows a short note.

`catalog/` describes the same pages and widgets as data. It is collected by
`bundle/scripts/collect-catalog.py` with the other dashboards' catalogs, so the
gateway can grant the public widgets to `R-PUB-ANON`.

## Gateway access

The browser calls only this app. `app/main.py` serves the SPA and exposes
`/api/proxy/*` routes (widget data, profile, articles, catalogue, dataset
previews, access requests, Fayda challenge and verify), `/api/dashboards`,
`/api/access-points` and `/api/series/{slug}`. Server-side, data calls go to the
gateway (`NHDW_GATEWAY_URL`, default `http://nhdw-gateway:8000`) and the Fayda
calls go to the Fayda replica (`FAYDA_INTERNAL_BASE`), so the browser stays
same-origin with no CORS. `/api/series/{slug}` asks OPA
first (`OPA_URL`); with `OPA_ENFORCE=false` (the default) a deny is logged but
the data is still served.

Where the gateway returns nothing usable, widgets fall back to bundled sample
content (`src/data/`, or headline figures from the profile copy deck) so the
page still renders.

## Development

From `ui/`, the scripts in `package.json`:

| Command | Effect |
|---|---|
| `npm run dev` | Vite dev server; proxies `/api` and `/static` to the BFF on port 8088 |
| `npm run build` | type-check (`tsc -b`) and build into `../dist` |
| `npm run preview` | serve the built bundle |

## Tests

`tests/test_access_form.py` drives the access-request form with Playwright:
inline validation, the Fayda email-OTP flow against the Fayda replica (host
port 8096), form reset and the success view. It needs a running system; run
`python3 test_access_form.py`.
