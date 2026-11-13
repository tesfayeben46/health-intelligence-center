#!/usr/bin/env python3
"""gen-exposures.py: derive dbt exposures from the gateway's widget registry.

`app/fetchers.py` (read-only here; it belongs to the gateway) is the
authoritative widget -> mart binding. This script parses it statically (AST,
no gateway imports/deps) and writes
`2_pipelines/marts/models/_exposures.yml`, so the marts->dashboard hop shows
up in the dbt manifest and, through the OpenMetadata dbt connector, in the
catalog's lineage graph.

Only marts that exist as models/seeds in the marts project become exposures
(a dbt exposure must `ref()` something dbt knows); the rest are listed in the
generated file's header comment. Rerun after adding widgets/fetchers, then rebuild
the marts image:  python bundle/scripts/gen-exposures.py
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
FETCHERS = REPO / "6_access-points/api/nhdw-gateway/app/fetchers.py"
# Modules that declare a module-level WIDGET_MARTS: route modules serving widgets
# outside the registry, and the registry itself for fetchers that read more than
# their declared mart.
ROUTE_MODULES = [REPO / "6_access-points/api/nhdw-gateway/app/routes/ct.py", FETCHERS]
MARTS = REPO / "2_pipelines/marts"
# The one supplied host base, same contract as everywhere else (ingress, OIDC
# redirect URIs, the keycloak seed, the wildcard certificate). A deployment on a
# different host regenerates these exposures rather than shipping a link into
# someone else's cluster.
HOST_BASE = os.environ.get("NHDW_HOSTBASE", "127.0.0.1.nip.io")
OUT = MARTS / "models/_exposures.yml"


def dbt_names() -> set[str]:
    names: set[str] = set()
    for pat in ("models/**/*.sql", "seeds/**/*.csv", "seeds/*.csv"):
        names |= {p.stem for p in MARTS.glob(pat)}
    return names


def _fetcher_target(call: ast.expr) -> tuple[str, str] | None:
    """(schema, table) from a `Fetcher(schema=..., table=...)` call with literal kwargs."""
    if not isinstance(call, ast.Call):
        return None
    kw = {a.arg: a.value for a in call.keywords}
    schema, table = kw.get("schema"), kw.get("table")
    if isinstance(schema, ast.Constant) and isinstance(table, ast.Constant):
        return (str(schema.value), str(table.value))
    return None


def _fetchers_key(target: ast.expr) -> str | None:
    """'x' for the target/expression `FETCHERS["x"]`, else None."""
    if (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
            and target.value.id == "FETCHERS" and isinstance(target.slice, ast.Constant)):
        return str(target.slice.value)
    return None


def widget_bindings() -> dict[str, tuple[str, str]]:
    """widget id -> (schema, table), parsed from the FETCHERS registry in all three
    forms it uses: the `FETCHERS = {...}` literal, later `FETCHERS["x"] = Fetcher(...)`
    statements, and aliases `FETCHERS["a"] = FETCHERS["b"]` (resolved last)."""
    tree = ast.parse(FETCHERS.read_text())
    out: dict[str, tuple[str, str]] = {}
    aliases: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AnnAssign, ast.Assign)):
            continue
        targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
        value = node.value
        for t in targets:
            if isinstance(t, ast.Name) and t.id == "FETCHERS" and isinstance(value, ast.Dict):
                for k, v in zip(value.keys, value.values):
                    hit = _fetcher_target(v)
                    if isinstance(k, ast.Constant) and hit:
                        out[str(k.value)] = hit
            key = _fetchers_key(t)
            if key is None:
                continue
            hit = _fetcher_target(value)
            if hit:
                out[key] = hit
            elif (src := _fetchers_key(value)) is not None:
                aliases.append((key, src))
    for key, src in aliases:
        if src in out:
            out[key] = out[src]
    return out


def module_widget_marts(module: pathlib.Path) -> dict[str, list[tuple[str, str]]]:
    """The module-level `WIDGET_MARTS` literal of one module (empty if none)."""
    out: dict[str, list[tuple[str, str]]] = {}
    for node in ast.parse(module.read_text()).body:
        if (isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "WIDGET_MARTS" for t in node.targets)):
            for widget, marts in ast.literal_eval(node.value).items():
                out[widget] = [tuple(m) for m in marts]
    return out


def route_widget_bindings() -> dict[str, list[tuple[str, str]]]:
    """Widgets whose marts are declared in a `WIDGET_MARTS` literal: widgets served
    by a dedicated route, and registry widgets whose fetcher joins other marts."""
    out: dict[str, list[tuple[str, str]]] = {}
    for module in ROUTE_MODULES:
        out.update(module_widget_marts(module))
    return out


def widget_mart_bindings() -> dict[str, list[tuple[str, str]]]:
    """Every widget -> the marts it reads: registry-served and route-served."""
    out = {w: [b] for w, b in widget_bindings().items()}
    out.update(route_widget_bindings())
    return out


def main() -> int:
    known = dbt_names()
    bindings = widget_mart_bindings()
    if not bindings:
        print("no FETCHERS bindings found: fetchers.py layout changed?", file=sys.stderr)
        return 1
    included: dict[str, list[tuple[str, str]]] = {}
    skipped: dict[str, list[str]] = {}
    for widget, marts in sorted(bindings.items()):
        ok = [(s, t) for s, t in marts if t in known]
        missing = [t for _, t in marts if t not in known]
        if ok:
            included[widget] = ok
        if missing:
            skipped[widget] = missing

    lines = [
        "# GENERATED by bundle/scripts/gen-exposures.py from the gateway's widget",
        "# bindings: the fetchers.py registry (authoritative widget->mart binding) plus",
        "# the WIDGET_MARTS declarations of route-served widgets (routes/ct.py).",
        "# Do not edit by hand: rerun the generator, then rebuild the marts image.",
    ]
    if skipped:
        lines.append("# Tables that are not dbt models/seeds in this project (no exposure edge):")
        for w, ts in sorted(skipped.items()):
            lines.append(f"#   {w} -> {', '.join(ts)}")
    lines += ["", "version: 2", "", "exposures:"]
    for widget, marts in sorted(included.items()):
        reads = ", ".join(f"{s}.{t}" for s, t in marts)
        lines += [
            f"  - name: widget_{widget.replace('-', '_')}",
            "    type: dashboard",
            f"    label: \"{widget} (Internal BI widget)\"",
            "    maturity: high",
            f"    url: https://internal-bi.{HOST_BASE}/",
            f"    description: \"Internal BI widget `{widget}` reads {reads} through the governed gateway (Trino + OPA).\"",
            "    depends_on:",
            *[f"      - ref('{t}')" for _, t in marts],
            "    owner:",
            "      name: platform",
            "",
        ]
    OUT.write_text("\n".join(lines).rstrip() + "\n")
    print(f"wrote {OUT.relative_to(REPO)}: {len(included)} exposures, {len(skipped)} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
