#!/usr/bin/env python3
"""collect-catalog.py: assemble the distributed per-dashboard catalog files into
one catalog.json for the security-seed loader.

Authored layout (composition flows downward; no central manifest):
  6_access-points/dashboards/<dash>/catalog/dashboard.yaml   id/title/description
  6_access-points/dashboards/<dash>/catalog/pages/*.yaml     page: id/title/ordering,
        filters: [filter ids the page's bar offers], sections: [section ids],
        widgets: [default-band placements {id,col_span,row_span,ordering}]
  6_access-points/dashboards/<dash>/catalog/sections/*.yaml  section: id/page_id/title/
        eyebrow/description/ordering/columns/dense,
        widgets: [{id,col_span,row_span,ordering,filters?:[widget_filter ids]}]
  6_access-points/dashboards/<dash>/ui/src/widgets/<id>/widget.yaml
        widget self-description: id/title/kind/placement/requires/description/reacts_to
  6_access-points/dashboards/<dash>/catalog/widgets.yaml     fallback for widgets with
        no SPA folder (e.g. demo-console)

Output row shapes are exactly what load_security_seed.py's upserts consume for
dashboards/pages/sections/widgets/page_widget/page_filter/widget_filter; the
loader prefers catalog.json over those seven flat YAMLs when present.

Usage: collect-catalog.py [--repo /path] [--out catalog.json]
Fails loudly on: duplicate widget ids, a section widget id with no widget
self-description, a page listing a section that names a different page.
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

import yaml

DASH_ROOT = "6_access-points/dashboards"


def load(p: Path):
    with p.open("rt", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--out", default="catalog.json")
    args = ap.parse_args()
    repo = Path(args.repo)

    dashboards, pages, sections, widgets = [], [], [], []
    page_widget, page_filter, widget_filter = [], [], []
    widget_ids: dict[str, str] = {}          # id -> source path (dup detection)
    errors: list[str] = []

    dash_dirs = sorted(d for d in (repo / DASH_ROOT).iterdir()
                       if (d / "catalog" / "dashboard.yaml").exists())
    for dd in dash_dirs:
        cat = dd / "catalog"
        dash = load(cat / "dashboard.yaml")
        dash_id = dash["id"]
        dashboards.append({"id": dash_id, "title": dash["title"],
                           "description": dash.get("description")})

        # widget self-descriptions: SPA folders first, then the fallback file
        wdir = dd / "ui" / "src" / "widgets"
        cand = sorted(wdir.rglob("widget.yaml")) if wdir.is_dir() else []
        fallback = cat / "widgets.yaml"
        fb_rows = load(fallback) if fallback.exists() else []
        for src, row in [(str(p), load(p)) for p in cand] + \
                        [(str(fallback), r) for r in (fb_rows or [])]:
            wid = row.get("id")
            if not wid:
                errors.append(f"{src}: widget.yaml missing id"); continue
            if wid in widget_ids:
                errors.append(f"duplicate widget id {wid!r}: {src} and {widget_ids[wid]}")
                continue
            widget_ids[wid] = src
            widgets.append(row)

        # pages (+ their filter offerings + default-band placements)
        for pf in sorted((cat / "pages").glob("*.yaml")) if (cat / "pages").is_dir() else []:
            page = load(pf)
            pages.append({"id": page["id"], "dashboard_id": dash_id,
                          "title": page["title"], "ordering": page.get("ordering", 0)})
            for i, fid in enumerate(page.get("filters") or []):
                page_filter.append({"page_id": page["id"], "filter_id": fid, "ordering": i})
            for w in page.get("widgets") or []:      # default band (no section)
                page_widget.append({"page_id": page["id"], "widget_id": w["id"],
                                    "section_id": None,
                                    "ordering": w.get("ordering", 0),
                                    "col_span": w.get("col_span", 6),
                                    "row_span": w.get("row_span", 1)})

        # sections (+ their widget placements + widget-filter offerings)
        for sf in sorted((cat / "sections").glob("*.yaml")) if (cat / "sections").is_dir() else []:
            sec = load(sf)
            sections.append({k: sec.get(k) for k in
                             ("id", "page_id", "title", "eyebrow", "description",
                              "ordering", "columns", "dense") if k in sec})
            for w in sec.get("widgets") or []:
                page_widget.append({"page_id": sec["page_id"], "widget_id": w["id"],
                                    "section_id": sec["id"],
                                    "ordering": w.get("ordering", 0),
                                    "col_span": w.get("col_span", 6),
                                    "row_span": w.get("row_span", 1)})
                for j, fid in enumerate(w.get("filters") or []):
                    widget_filter.append({"dashboard_id": dash_id,
                                          "page_id": sec["page_id"], "widget_id": w["id"],
                                          "filter_id": fid, "ordering": j})

    # referential sanity: every placed widget must be self-described
    placed = {r["widget_id"] for r in page_widget}
    for wid in sorted(placed - set(widget_ids)):
        errors.append(f"placed widget {wid!r} has no widget.yaml self-description")

    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1

    out = {"dashboards": dashboards, "pages": pages, "sections": sections,
           "widgets": widgets, "page_widget": page_widget,
           "page_filter": page_filter, "widget_filter": widget_filter}
    Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"catalog.json: {len(dashboards)} dashboards, {len(pages)} pages, "
          f"{len(sections)} sections, {len(widgets)} widgets, "
          f"{len(page_widget)} placements, {len(page_filter)}+{len(widget_filter)} filter offerings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
