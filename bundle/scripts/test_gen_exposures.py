#!/usr/bin/env python3
"""Contract tests for the widget-binding parser (gen-exposures.py).

The parser feeds both lineage catalogs (dbt exposures and the OpenMetadata
dashboard connector, sync-om-dashboards.py), so a widget it cannot see has no
lineage anywhere. Pure-python, repo files only:

  registry    every FETCHERS entry, in all three forms (dict literal,
              FETCHERS["x"] = Fetcher(...), alias FETCHERS["a"] = FETCHERS["b"]),
              yields a binding
  aliases     an alias resolves to its target's binding
  routes      every mart a widget declares in a module's WIDGET_MARTS is
              actually queried by that module (declaration cannot drift)
  parity      a registry widget that joins other marts lists its declared mart
              first, and every mart it lists carries the declared mart's access
              attributes (mart_attrs.yaml), because OPA decides and masks on
              the declared mart only
  catalog     every Internal BI catalog widget is bound, or listed in
              READS_NO_MART with the reason; a new widget without a binding
              fails here instead of silently missing from lineage

Run:  python3 bundle/scripts/test_gen_exposures.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

import yaml

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
CATALOG = REPO / "6_access-points/dashboards/internal-bi/catalog"

# Widgets that legitimately read no mart (tools and per-user preferences).
READS_NO_MART = {
    "ex-templates", "ex-sql-editor", "ex-saved-queries", "ex-audit-trail",   # export tools: ad-hoc SQL / ops state
    "st-profile", "st-display", "st-alert-subs", "st-security",              # settings: per-user ops state
    # ML serving: reads a model's batch predictions (ops_ml.ml_predictions via the
    # gateway ML route), not a dbt mart; its lineage is the MLflow run, which the
    # exposures generator does not model yet.
    "rm-stockout-risk-kpi",
}

spec = importlib.util.spec_from_file_location("gen_exposures", HERE / "gen-exposures.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


def test_registry() -> None:
    src = gen.FETCHERS.read_text()
    declared = set(re.findall(r'^\s*"([a-z0-9-]+)":\s*Fetcher\(', src, re.M))
    declared |= set(re.findall(r'^FETCHERS\["([^"]+)"\]\s*=', src, re.M))
    bound = gen.widget_bindings()
    missing = sorted(declared - set(bound))
    assert not missing, f"registry entries the parser cannot bind: {missing}"
    print(f"  ok: registry: {len(declared)} entries, all bound")


def test_aliases() -> None:
    src = gen.FETCHERS.read_text()
    bound = gen.widget_bindings()
    pairs = re.findall(r'^FETCHERS\["([^"]+)"\]\s*=\s*FETCHERS\["([^"]+)"\]', src, re.M)
    for alias, target in pairs:
        assert bound.get(alias) == bound.get(target), f"alias {alias} != {target}"
    print(f"  ok: aliases: {len(pairs)} resolve to their targets")


def test_routes() -> None:
    for module in gen.ROUTE_MODULES:
        src = module.read_text()
        for widget, marts in gen.module_widget_marts(module).items():
            for _, table in marts:
                assert re.search(rf'\.{table}\b|\."{table}"|table="{table}"', src), \
                    f"{module.name}: {widget} declares {table} but the module never queries it"
    print(f"  ok: routes: {len(gen.route_widget_bindings())} widgets with declared marts, declarations match queries")


def test_parity() -> None:
    attrs_doc = yaml.safe_load((REPO / "bundle/seed-pack/app/security/mart_attrs.yaml").read_text())
    rows = attrs_doc if isinstance(attrs_doc, list) else attrs_doc.get("mart_attrs", [])
    attrs = {(r["schema_name"], r["table_name"]):
             (r["subject"], r["data_class"], r.get("granularity"), r.get("default_sensitivity", "default"))
             for r in rows}
    registry = gen.widget_bindings()
    joined = gen.module_widget_marts(gen.FETCHERS)
    for widget, marts in joined.items():
        assert marts[0] == registry.get(widget), \
            f"{widget}: WIDGET_MARTS must list the fetcher's declared mart first ({registry.get(widget)})"
        for mart in marts:
            assert mart in attrs, f"{widget}: {mart} has no mart_attrs row"
            assert attrs[mart] == attrs[marts[0]], \
                f"{widget}: {mart} access attributes {attrs[mart]} differ from the declared mart's {attrs[marts[0]]}"
    print(f"  ok: parity: {len(joined)} joining widget(s), every joined mart shares the declared mart's access attributes")


def test_catalog() -> None:
    bound = gen.widget_mart_bindings()
    widgets = {w["id"] for f in (CATALOG / "sections").glob("*.yaml")
               for w in (yaml.safe_load(f.read_text()).get("widgets") or [])}
    unbound = sorted(widgets - set(bound) - READS_NO_MART)
    assert not unbound, f"catalog widgets with no mart binding (no lineage): {unbound}"
    stale = sorted(READS_NO_MART - widgets)
    assert not stale, f"READS_NO_MART lists widgets no longer in the catalog: {stale}"
    print(f"  ok: catalog: {len(widgets)} widgets, {len(widgets) - len(READS_NO_MART)} bound, "
          f"{len(READS_NO_MART)} read no mart by design")


if __name__ == "__main__":
    for t in (test_registry, test_aliases, test_routes, test_parity, test_catalog):
        t()
    print("gen-exposures contract: OK")
