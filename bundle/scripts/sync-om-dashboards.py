#!/usr/bin/env python3
"""sync-om-dashboards.py: publish an access point's structure to OpenMetadata.

Publishes an access point's structure to the catalog as metadata only, so OM
renders full lineage from every dashboard page and individual widget back
through mart -> vault -> staging -> lake -> source system:

    dashboard service  = the access point           (internal-bi)
    dashboards         = its catalog PAGES
    charts             = its WIDGETS (attached to their page)
    lineage edges      = mart table -> page dashboard and -> widget chart,
                         derived from the gateway's fetcher registry (the
                         same source gen-exposures.py uses).

Guarantees (apply to any access point that uses this script):
  * no effect on how dashboards operate and no contact with access control:
    the script only reads repo files and writes OM metadata;
  * fail-safe: silent no-op without OM_API/OM_JWT; any API failure is one
    warning line and the sync continues (nothing depends on OM being up);
  * idempotent: createOrUpdate PUTs; re-runs converge, never duplicate;
  * the source of truth is the repo (fetchers.py and the SPA's catalog),
    parsed statically; nothing is imported from the gateway.

Run after widget/page changes (alongside gen-exposures.py) and at seed time
(seed-openmetadata.sh). Env: OM_API, OM_JWT, optional OM_DASH_URL_BASE
(default the dev-convention public base). --dry-run prints the plan only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import ssl
import urllib.parse
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
CATALOG = REPO / "6_access-points/dashboards/internal-bi/catalog"
SERVICE = "internal-bi"
URL_BASE = os.environ.get("OM_DASH_URL_BASE", "https://internal-bi.127.0.0.1.nip.io")
TABLE_FQN = "nhdw-vault.nhdw_vault.{schema}.{table}"

_CTX = ssl.create_default_context(); _CTX.check_hostname = False; _CTX.verify_mode = ssl.CERT_NONE


def widget_bindings() -> dict[str, list[tuple[str, str]]]:
    # gen-exposures.py owns the widget-binding parser (registry + route-declared
    # WIDGET_MARTS); its name has a dash, so load it by path rather than
    # duplicating the AST walk here.
    spec = importlib.util.spec_from_file_location(
        "gen_exposures", REPO / "bundle/scripts/gen-exposures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.widget_mart_bindings()


def load_yaml(path: pathlib.Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text())


def catalog_pages() -> list[dict]:
    """[{id, title, widgets:[{id}]}] from the SPA's own catalog, in the
    catalog's own `ordering` (pages, sections, widgets) so OM mirrors the UI."""
    sections: dict[str, list[dict]] = {}
    for f in (CATALOG / "sections").glob("*.yaml"):
        s = load_yaml(f)
        sections.setdefault(s["page_id"], []).append(s)
    pages = []
    for f in (CATALOG / "pages").glob("*.yaml"):
        p = load_yaml(f)
        widgets = []
        for s in sorted(sections.get(p["id"], []), key=lambda s: s.get("ordering", 0)):
            widgets += [{"id": w["id"]}
                        for w in sorted(s.get("widgets", []), key=lambda w: w.get("ordering", 0))]
        pages.append({"id": p["id"], "title": p.get("title", p["id"]),
                      "ordering": p.get("ordering", 0), "widgets": widgets})
    return sorted(pages, key=lambda p: p["ordering"])


class Om:
    def __init__(self, api: str, jwt: str, dry: bool):
        self.api, self.jwt, self.dry = api.rstrip("/"), jwt, dry
        self.warnings = 0

    def call(self, method: str, path: str, payload: dict | None = None) -> dict | None:
        if self.dry:
            print(f"  [dry] {method} {path} {json.dumps(payload)[:110] if payload else ''}")
            return {"id": "(dry)"}   # placeholder id so the lineage plan prints too
        req = urllib.request.Request(
            f"{self.api}{path}",
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method,
            headers={"Authorization": f"Bearer {self.jwt}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15, context=_CTX) as r:
                return json.loads(r.read() or b"{}")
        except Exception as exc:                                # noqa: BLE001
            self.warnings += 1
            print(f"  ! {method} {path}: {exc}")
            return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    api, jwt = os.environ.get("OM_API", ""), os.environ.get("OM_JWT", "")
    if not (api and jwt) and not args.dry_run:
        print("OM_API / OM_JWT unset: nothing to do (fail-safe no-op).")
        return 0
    om = Om(api, jwt, args.dry_run)
    bindings = widget_bindings()
    pages = catalog_pages()
    print(f"access point '{SERVICE}': {len(pages)} pages, "
          f"{sum(len(p['widgets']) for p in pages)} widgets, "
          f"{len(bindings)} mart bindings")

    om.call("PUT", "/v1/services/dashboardServices", {
        "name": SERVICE, "serviceType": "CustomDashboard",
        "connection": {"config": {"type": "CustomDashboard"}},
        "description": "Internal BI: the governed dashboard SPA. Pages and "
                       "widgets are published by the access-point emission "
                       "standard (sync-om-dashboards.py); lineage derives from "
                       "the gateway's widget bindings (fetcher registry + "
                       "route-declared WIDGET_MARTS)."})

    tables: dict[str, str] = {}   # fqn -> id cache
    for page in pages:
        chart_fqns = []
        for w in page["widgets"]:
            om.call("PUT", "/v1/charts", {
                "name": w["id"], "displayName": w["id"], "service": SERVICE,
                "chartType": "Other", "sourceUrl": URL_BASE + "/",
                "description": f"Internal BI widget on the '{page['title']}' page."})
            chart_fqns.append(f"{SERVICE}.{w['id']}")
        slug = page["id"].split("/")[-1]
        dash = om.call("PUT", "/v1/dashboards", {
            "name": slug, "displayName": page["title"], "service": SERVICE,
            "charts": chart_fqns, "sourceUrl": URL_BASE + "/",
            "description": f"Internal BI page '{page['title']}' "
                           f"({len(chart_fqns)} widgets)."})
        # lineage: every widget with a mart binding links mart -> chart and
        # mart -> page dashboard (rolled-up view)
        for w in page["widgets"]:
            if w["id"] not in bindings:
                continue
            chart = None
            for schema, table in bindings[w["id"]]:
                fqn = TABLE_FQN.format(schema=schema, table=table)
                if fqn not in tables:
                    got = om.call("GET", f"/v1/tables/name/{urllib.parse.quote(fqn, safe='')}")
                    tables[fqn] = (got or {}).get("id", "")
                if not tables[fqn]:
                    continue
                if chart is None:
                    chart = om.call("GET", f"/v1/charts/name/{urllib.parse.quote(SERVICE + '.' + w['id'], safe='')}")
                for ent_type, ent in (("chart", chart), ("dashboard", dash)):
                    if ent and ent.get("id"):
                        om.call("PUT", "/v1/lineage", {"edge": {
                            "fromEntity": {"id": tables[fqn], "type": "table"},
                            "toEntity": {"id": ent["id"], "type": ent_type},
                            "lineageDetails": {"description":
                                f"widget {w['id']} reads {schema}.{table} through the governed gateway"}}})
    print(f"done: warnings: {om.warnings} (each was skipped, never fatal)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
