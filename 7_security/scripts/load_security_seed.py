#!/usr/bin/env python3
"""
Permission seed loader.

Reads the YAML in `bundle/seed-pack/app/security/` (and the role permissions in
`bundle/roles/`) and idempotently upserts them into the `nhdw_ops` database.
Run by `3_storage/postgres/init/18_security_seed.sh` in a local container and
by the security seed Job in Kubernetes; re-runnable against an existing DB.

All inserts are wrapped in a single transaction. Catches FK violations
loudly so misordered seeds fail with an actionable error.

Usage:
    python3 load_security_seed.py
        [--seed-dir /path/to/seeds]
        [--pg-host HOST] [--pg-port PORT]
        [--pg-user USER] [--pg-db DB]

Environment:
    NHDW_SEED_DIR    overrides --seed-dir
    NHDW_ROLES_DIR   overrides --roles-dir
    NHDW_USERS_SEED  users file name (see resolve_users_file)
    POSTGRES_HOST    pg host (default: localhost)
    POSTGRES_PORT    pg port (default: 5432)
    POSTGRES_USER    pg user (default: postgres)
    OPS_DB_NAME      ops db name (default: nhdw_ops)
    PGPASSWORD       pg password (read from env; not a CLI flag)
    SERVICE_PAT      optional platform service-account token to register
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import yaml
import psycopg
from psycopg.types.json import Json

log = logging.getLogger("load_security_seed")


# ordered seed file list
# Order matters: each file's FKs are resolved by earlier files.
# Which users file the active country profile wants. The appsec layer of a
# profile is country-specific (its provinces, its seeded users, roles scoped to
# its regions), so it is not always users.yaml: zm-v1's profile.yaml declares
# `layers.appsec.users: seed-pack/app/security/users.zm.yaml`.
#
# Resolution order, first hit wins:
#   --users <name>          explicit, for a one-off load
#   $NHDW_USERS_SEED        what the bootstrap passes
#   the active profile's layers.appsec.users basename
#   users.yaml              the default
def resolve_users_file(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("NHDW_USERS_SEED", "").strip()
    if env:
        return Path(env).name
    try:
        pdir = Path(os.environ.get("NHDW_PROFILE_DIR", "/opt/nhdw-profiles"))
        state = Path(os.environ.get("NHDW_PROFILE_STATE_DIR", "/opt/nhdw-state"))
        key = ""
        for pointer in (state / "ACTIVE", pdir / "ACTIVE"):
            if pointer.is_file():
                key = pointer.read_text(encoding="utf-8").strip()
                break
        key = key or os.environ.get("NHDW_PROFILE", "").strip()
        if key:
            man = yaml.safe_load((pdir / key / "profile.yaml").read_text(encoding="utf-8"))
            declared = (((man or {}).get("layers") or {}).get("appsec") or {}).get("users")
            if declared:
                return Path(declared).name
    except Exception as exc:                                    # noqa: BLE001
        # A profile that cannot be read must not stop the seed; fall back to
        # the default users file.
        print(f"  (profile appsec layer unreadable, using users.yaml: {exc})")
    return "users.yaml"


USERS_FILE = resolve_users_file()

SEED_FILES_IN_ORDER = [
    "dashboards.yaml",
    "pages.yaml",
    "sections.yaml",
    "widgets.yaml",
    "page_widget.yaml",
    "filters.yaml",
    "page_filter.yaml",
    "widget_filter.yaml",
    "mart_attrs.yaml",
    "column_classification.yaml",
    "identity_sources.yaml",
    "roles.yaml",
    "role_grants.yaml",
    USERS_FILE,
]


# helpers
def load_yaml(path: Path) -> Any:
    """Read a YAML file. Returns the parsed Python structure."""
    log.info("  reading %s", path.name)
    with path.open("rt", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def upsert_dashboards(cur, rows: list[dict]) -> int:
    if not rows:
        return 0
    cur.executemany("""
        INSERT INTO ops_perm.dashboard (id, title, description)
        VALUES (%(id)s, %(title)s, %(description)s)
        ON CONFLICT (id) DO UPDATE
          SET title       = EXCLUDED.title,
              description = EXCLUDED.description
    """, rows)
    return len(rows)


def upsert_pages(cur, rows: list[dict]) -> int:
    if not rows:
        return 0
    cur.executemany("""
        INSERT INTO ops_perm.page (id, dashboard_id, title, ordering)
        VALUES (%(id)s, %(dashboard_id)s, %(title)s, %(ordering)s)
        ON CONFLICT (id) DO UPDATE
          SET dashboard_id = EXCLUDED.dashboard_id,
              title        = EXCLUDED.title,
              ordering     = EXCLUDED.ordering
    """, rows)
    return len(rows)


def upsert_sections(cur, rows: list[dict]) -> int:
    """Layout bands within a page (ops_perm.section).

    Provenance-safe: no blanket DELETE, which would remove runtime bands
    created by the admin Dashboard-Control editor or catalog reconciler
    (managed_by='runtime') on every re-seed. Upsert the YAML rows and let
    sync_provenance() remove only orphaned seed sections (ops_perm.section is
    in PROVENANCE_TABLES), as for upsert_widgets / upsert_page_widget. FK
    page_widget.section_id is ON DELETE SET NULL, so a removed band moves its
    widgets to the page's default band."""
    for i, r in enumerate(rows):
        cur.execute("""
            INSERT INTO ops_perm.section (id, page_id, title, eyebrow, description, ordering, columns, dense)
            VALUES (%(id)s, %(page_id)s, %(title)s, %(eyebrow)s, %(description)s, %(ordering)s, %(columns)s, %(dense)s)
            ON CONFLICT (id) DO UPDATE
              SET page_id=EXCLUDED.page_id, title=EXCLUDED.title, eyebrow=EXCLUDED.eyebrow,
                  description=EXCLUDED.description, ordering=EXCLUDED.ordering,
                  columns=EXCLUDED.columns, dense=EXCLUDED.dense
        """, {"id": r["id"], "page_id": r["page_id"], "title": r["title"],
              "eyebrow": r.get("eyebrow"), "description": r.get("description"),
              "ordering": r.get("ordering", i * 10),
              "columns": r.get("columns", 12), "dense": r.get("dense", True)})
    return len(rows)


def upsert_widgets(cur, rows: list[dict]) -> int:
    if not rows:
        return 0
    # Soft-orphan retired core widgets: a widget missing from the authored
    # catalog is stamped orphaned_at, not deleted, so its role_widget grants and
    # page_widget
    # placements survive until an explicit admin purge (manage_catalog). The
    # resolver hides orphaned widgets; re-adding the widget to the catalog
    # revives it (orphaned_at=NULL in the upsert below) with grants intact.
    # Runtime plugin widgets (source != 'core') are never touched.
    ids = [r["id"] for r in rows]
    cur.execute(
        "ALTER TABLE ops_perm.widget ADD COLUMN IF NOT EXISTS orphaned_at timestamptz")
    cur.execute("""
        UPDATE ops_perm.widget SET orphaned_at = now()
        WHERE source = 'core' AND orphaned_at IS NULL AND id <> ALL(%s)
    """, (ids,))
    norm = [
        {
            "id":          r["id"],
            "title":       r["title"],
            "kind":        r["kind"],
            "placement":   r.get("placement", "page.section"),
            "requires":    Json(r["requires"]) if r.get("requires") else None,
            "description": r.get("description"),
            # Plugin fields (core widgets omit them and get the defaults).
            "source":         r.get("source", "core"),
            "component_type": r.get("component_type"),
            "reacts_to":      Json(r["reacts_to"]) if r.get("reacts_to") else None,
            "bundle_key":     r.get("bundle_key"),
            "data_binding":   Json(r["data_binding"]) if r.get("data_binding") else None,
        } for r in rows
    ]
    cur.executemany("""
        INSERT INTO ops_perm.widget
            (id, title, kind, placement, requires, description,
             source, component_type, reacts_to, bundle_key, data_binding)
        VALUES (%(id)s, %(title)s, %(kind)s, %(placement)s,
                %(requires)s, %(description)s,
                %(source)s, %(component_type)s, %(reacts_to)s,
                %(bundle_key)s, %(data_binding)s)
        ON CONFLICT (id) DO UPDATE
          SET title          = EXCLUDED.title,
              kind           = EXCLUDED.kind,
              placement      = EXCLUDED.placement,
              requires       = EXCLUDED.requires,
              description    = EXCLUDED.description,
              source         = EXCLUDED.source,
              component_type = EXCLUDED.component_type,
              reacts_to      = EXCLUDED.reacts_to,
              bundle_key     = EXCLUDED.bundle_key,
              data_binding   = EXCLUDED.data_binding,
              orphaned_at    = NULL          -- revive on catalog re-add
    """, norm)
    return len(rows)


def upsert_filters(cur, rows: list[dict]) -> int:
    if not rows:
        return 0
    norm = [
        {
            "id":            r["id"],
            "title":         r["title"],
            "source":        r["source"],
            "source_ref":    r.get("source_ref"),
            "static_values": Json(r["static_values"]) if r.get("static_values") else None,
        } for r in rows
    ]
    cur.executemany("""
        INSERT INTO ops_perm.filter (id, title, source, source_ref, static_values)
        VALUES (%(id)s, %(title)s, %(source)s, %(source_ref)s, %(static_values)s)
        ON CONFLICT (id) DO UPDATE
          SET title         = EXCLUDED.title,
              source        = EXCLUDED.source,
              source_ref    = EXCLUDED.source_ref,
              static_values = EXCLUDED.static_values
    """, norm)
    return len(rows)


def upsert_mart_attrs(cur, rows: list[dict]) -> int:
    if not rows:
        return 0
    cur.executemany("""
        INSERT INTO ops_perm.mart_attrs (schema_name, table_name, subject, data_class,
                                          granularity, default_sensitivity, description)
        VALUES (%(schema_name)s, %(table_name)s, %(subject)s, %(data_class)s,
                %(granularity)s, %(default_sensitivity)s, %(description)s)
        ON CONFLICT (schema_name, table_name) DO UPDATE
          SET subject             = EXCLUDED.subject,
              data_class          = EXCLUDED.data_class,
              granularity         = EXCLUDED.granularity,
              default_sensitivity = EXCLUDED.default_sensitivity,
              description         = EXCLUDED.description
    """, [
        {**r, "description": r.get("description"),
         "default_sensitivity": r.get("default_sensitivity", "default")}
        for r in rows
    ])
    return len(rows)


def upsert_column_classification(cur, rows: list[dict]) -> int:
    """Per-column sensitivity/masking rows (ops_perm.column_classification).

    Three authors write this table (managed_by = seed | steward | openmetadata),
    so it is not in PROVENANCE_TABLES: the generic delete-sync
    would remove steward/OpenMetadata rows. Seed rows are reconciled here instead:
    upsert everything in the YAML as managed_by='seed', then delete only
    managed_by='seed' rows the YAML no longer contains."""
    cur.executemany("""
        INSERT INTO ops_perm.column_classification
              (schema_name, table_name, column_name, sensitivity, mask_class,
               data_class, pii, purpose_unmask, managed_by, note, updated_at)
        VALUES (%(schema_name)s, %(table_name)s, %(column_name)s, %(sensitivity)s,
                %(mask_class)s, %(data_class)s, %(pii)s, %(purpose_unmask)s,
                'seed', %(note)s, now())
        ON CONFLICT (schema_name, table_name, column_name) DO UPDATE
          SET sensitivity    = EXCLUDED.sensitivity,
              mask_class     = EXCLUDED.mask_class,
              data_class     = EXCLUDED.data_class,
              pii            = EXCLUDED.pii,
              purpose_unmask = EXCLUDED.purpose_unmask,
              note           = EXCLUDED.note,
              updated_at     = now()
          WHERE ops_perm.column_classification.managed_by = 'seed'
    """, [
        {"schema_name": r["schema_name"], "table_name": r["table_name"],
         "column_name": r["column_name"],
         "sensitivity": r.get("sensitivity", "restricted"),
         "mask_class": r.get("mask_class", "drop"),
         "data_class": r.get("data_class"), "pii": bool(r.get("pii", False)),
         "purpose_unmask": r.get("purpose_unmask", []) or [],
         "note": r.get("note")}
        for r in rows
    ] if rows else [])
    keys = [(r["schema_name"], r["table_name"], r["column_name"]) for r in rows]
    if keys:
        # psycopg3 does not adapt a tuple-of-tuples for IN; build an explicit
        # parametrized VALUES list instead.
        placeholders = ", ".join(["(%s, %s, %s)"] * len(keys))
        cur.execute(
            "DELETE FROM ops_perm.column_classification"
            " WHERE managed_by = 'seed'"
            f" AND (schema_name, table_name, column_name) NOT IN ({placeholders})",
            [v for k in keys for v in k])
    else:
        cur.execute("DELETE FROM ops_perm.column_classification WHERE managed_by = 'seed'")
    return len(rows)


def upsert_identity_sources(cur, rows: list[dict]) -> int:
    if not rows:
        return 0
    norm = [
        {
            "code":            r["code"],
            "name":            r["name"],
            "kind":            r["kind"],
            "protocol":        r.get("protocol"),
            "status":          r.get("status", "available"),
            "config_json":     Json(r["config_json"]) if r.get("config_json") else None,
            "keycloak_alias":  r.get("keycloak_alias"),
        } for r in rows
    ]
    cur.executemany("""
        INSERT INTO ops_identity.identity_source
            (code, name, kind, protocol, status, config_json, keycloak_alias)
        VALUES
            (%(code)s, %(name)s, %(kind)s, %(protocol)s, %(status)s,
             %(config_json)s, %(keycloak_alias)s)
        ON CONFLICT (code) DO UPDATE
          SET name           = EXCLUDED.name,
              kind           = EXCLUDED.kind,
              protocol       = EXCLUDED.protocol,
              status         = EXCLUDED.status,
              config_json    = EXCLUDED.config_json,
              keycloak_alias = EXCLUDED.keycloak_alias,
              updated_at     = now()
    """, norm)
    return len(rows)


def upsert_roles(cur, rows: list[dict]) -> dict[str, int]:
    """Upsert ops_perm.role and ops_perm.role_capability.
    Returns {role_code: role_id} for downstream grant linking."""
    if not rows:
        return {}
    # Upsert role rows.
    cur.executemany("""
        INSERT INTO ops_perm.role (code, name, description, combine_strategy)
        VALUES (%(code)s, %(name)s, %(description)s, %(combine_strategy)s)
        ON CONFLICT (code) DO UPDATE
          SET name             = EXCLUDED.name,
              description      = EXCLUDED.description,
              combine_strategy = EXCLUDED.combine_strategy,
              updated_at       = now()
    """, [
        {
            "code":             r["code"],
            "name":             r["name"],
            "description":      r.get("description"),
            "combine_strategy": r.get("combine_strategy", "union"),
        } for r in rows
    ])

    # Fetch id<->code mapping for the grant loader.
    cur.execute("SELECT code, id FROM ops_perm.role")
    code_to_id = {code: rid for code, rid in cur.fetchall()}

    # Replace capabilities atomically per role.
    for r in rows:
        rid = code_to_id[r["code"]]
        caps = r.get("capabilities") or []
        cur.execute("DELETE FROM ops_perm.role_capability WHERE role_id = %s", (rid,))
        if caps:
            cur.executemany("""
                INSERT INTO ops_perm.role_capability (role_id, capability_code)
                VALUES (%s, %s)
            """, [(rid, c) for c in caps])
    return code_to_id


# normalized permission store
# The per-role permission tuples in bundle/roles/*.yaml are normalized into
# ops_perm.permission (one row per distinct tuple) and ops_perm.role_permission
# links: a tuple shared by N roles gives one permission row and N links.
# `compile.py --from-db` reads these back and must emit the same rego as the
# YAML-sourced build (equivalence check).

def _perm_norm(p: dict) -> dict:
    """Role-YAML permission entry -> ops_perm.permission columns. Lists keep their
    YAML order (not sorted) so compile.py can re-emit the in-tree policies/*.rego
    byte for byte; deduplication still works because the role YAMLs list values
    in a consistent order."""
    subj, gran = p["subject"], p["granularity"]
    verbs = list(p.get("verbs") or [])
    return {
        "subject":      subj,
        "members":      p.get("members", "scoped"),
        "granularity":  gran,
        "verbs":        verbs,
        "purposes":     list(p.get("purpose_of_use") or []),
        "data_classes": list(p.get("data_class") or []),
        "sensitivity":  list(p.get("sensitivity") or []),
        "description":  f"{subj}/{gran}; {', '.join(verbs)}",
    }


def _perm_key(d: dict) -> tuple:
    return (d["subject"], d["members"], d["granularity"], tuple(d["verbs"]),
            tuple(d["purposes"]), tuple(d["data_classes"]), tuple(d["sensitivity"]))


def upsert_permissions_from_roles(cur, roles_dir, code_to_id: dict) -> tuple:
    """Populate ops_perm.permission + role_permission from bundle/roles/*.yaml.
    Seed links are delete-synced (managed_by='seed'); runtime links survive.
    Returns (unique_permissions, seed_links)."""
    if not roles_dir.is_dir():
        log.warning("roles dir not found: %s; permission store not seeded", roles_dir)
        return (0, 0)
    role_perms = []                                    # [(role_code, order_idx, normalized perm)]
    for f in sorted(roles_dir.glob("r-*.yaml")):
        doc = yaml.safe_load(f.open())["role"]
        for i, p in enumerate(doc.get("permissions") or []):
            role_perms.append((doc["id"], i, _perm_norm(p)))
    if not role_perms:
        return (0, 0)

    key_to_id = {}                                     # dedup: one row per unique tuple
    for _code, _idx, d in role_perms:
        k = _perm_key(d)
        if k in key_to_id:
            continue
        cur.execute("""
            INSERT INTO ops_perm.permission
                (subject, members, granularity, verbs, purposes, data_classes, sensitivity, description)
            VALUES (%(subject)s, %(members)s, %(granularity)s, %(verbs)s, %(purposes)s,
                    %(data_classes)s, %(sensitivity)s, %(description)s)
            ON CONFLICT ON CONSTRAINT uq_permission_tuple DO UPDATE
              SET description = EXCLUDED.description, updated_at = now()
            RETURNING id
        """, d)
        key_to_id[k] = cur.fetchone()[0]

    seed_links = {}                                     # (role_id, permission_id) -> ordering (YAML index)
    for code, idx, d in role_perms:
        rid = code_to_id.get(code)
        if rid is None:
            log.warning("role %s (bundle/roles) has no ops_perm.role row; skipped", code)
            continue
        seed_links[(rid, key_to_id[_perm_key(d)])] = idx
    if seed_links:
        cur.executemany("""
            INSERT INTO ops_perm.role_permission (role_id, permission_id, ordering, managed_by)
            VALUES (%s, %s, %s, 'seed')
            ON CONFLICT (role_id, permission_id) DO UPDATE
              SET ordering = EXCLUDED.ordering, managed_by = 'seed'
        """, [(r, p, o) for (r, p), o in seed_links.items()])

    # Delete-sync seed links the YAMLs no longer declare (runtime links preserved).
    cur.execute("SELECT role_id, permission_id FROM ops_perm.role_permission WHERE managed_by='seed'")
    stale = {tuple(r) for r in cur.fetchall()} - set(seed_links)
    for rid, pid in stale:
        cur.execute("DELETE FROM ops_perm.role_permission "
                    "WHERE role_id=%s AND permission_id=%s AND managed_by='seed'", (rid, pid))
    return (len(key_to_id), len(seed_links))


def upsert_role_scope(cur, roles_rows: list[dict], code_to_id: dict[str, int]) -> int:
    """Seed ops_perm.role_scope from each role's optional `scope:` block
    ({ dimension: [values] }, dimension ∈ geography/programme/facility/
    artifact_tier/workshop_group).

    role_scope is the per-role data-scope cap: the resolver derives every
    filter's allowed values from role_scope ∩ user_scope (see
    permissions._cap_for_filter). Per-role DELETE-then-INSERT scoped to
    managed_by='seed' (preserves runtime rows written by the admin editor).
    role_scope carries its own managed_by column, so it does not go through
    PROVENANCE_TABLES."""
    n = 0
    for r in roles_rows:
        code = r["code"]
        if code not in code_to_id:
            raise ValueError(f"roles references unknown role: {code}")
        rid = code_to_id[code]
        cur.execute(
            "DELETE FROM ops_perm.role_scope WHERE role_id = %s AND managed_by = 'seed'",
            (rid,))
        for dimension, values in (r.get("scope") or {}).items():
            for value in (values or []):
                cur.execute("""
                    INSERT INTO ops_perm.role_scope (role_id, dimension, value, managed_by)
                    VALUES (%s, %s, %s, 'seed')
                    ON CONFLICT (role_id, dimension, value) DO UPDATE SET managed_by = 'seed'
                """, (rid, dimension, str(value)))
                n += 1
    return n


def upsert_page_widget(cur, rows: list[dict]) -> int:
    """Replace the page<->widget placement catalog (ops_perm.page_widget).
    Backend source of truth for which pages a widget renders on; role_widget
    grants are expanded across these placements by upsert_role_grants.

    Provenance-safe: no blanket DELETE, which would remove runtime plugin
    placements (managed_by='runtime') on every re-seed. Upsert the YAML rows and
    let sync_provenance() remove only orphaned seed placements, as for
    upsert_widgets."""
    n = 0
    for i, r in enumerate(rows):
        cur.execute("""
            INSERT INTO ops_perm.page_widget (page_id, widget_id, section_id, ordering, col_span, row_span)
            VALUES (%(page_id)s, %(widget_id)s, %(section_id)s, %(ordering)s, %(col_span)s, %(row_span)s)
            ON CONFLICT (page_id, widget_id) DO UPDATE
              SET section_id = EXCLUDED.section_id, ordering = EXCLUDED.ordering,
                  col_span = EXCLUDED.col_span, row_span = EXCLUDED.row_span
        """, {"page_id": r["page_id"], "widget_id": r["widget_id"],
              "section_id": r.get("section_id"), "ordering": r.get("ordering", i),
              "col_span": r.get("col_span", 6), "row_span": r.get("row_span", 1)})
        n += 1
    return n


def upsert_page_filter(cur, rows: list[dict]) -> int:
    """Structural page->filter offerings: which filters a page's global-filter
    bar shows, independent of role. Value caps come from role_scope ∩ user_scope in
    the resolver. Provenance-safe (ops_perm.page_filter is in PROVENANCE_TABLES): no
    blanket DELETE; sync_provenance removes only seed orphans, like page_widget."""
    for i, r in enumerate(rows):
        cur.execute("""
            INSERT INTO ops_perm.page_filter (page_id, filter_id, ordering)
            VALUES (%(page_id)s, %(filter_id)s, %(ordering)s)
            ON CONFLICT (page_id, filter_id) DO UPDATE SET ordering = EXCLUDED.ordering
        """, {"page_id": r["page_id"], "filter_id": r["filter_id"],
              "ordering": r.get("ordering", i)})
    return len(rows)


def upsert_widget_filter(cur, rows: list[dict]) -> int:
    """Structural widget->filter offerings: which filters a widget shows
    locally, independent of role. Same provenance contract as upsert_page_filter."""
    for i, r in enumerate(rows):
        cur.execute("""
            INSERT INTO ops_perm.widget_filter
                (dashboard_id, page_id, widget_id, filter_id, ordering)
            VALUES (%(dashboard_id)s, %(page_id)s, %(widget_id)s, %(filter_id)s, %(ordering)s)
            ON CONFLICT (dashboard_id, page_id, widget_id, filter_id)
              DO UPDATE SET ordering = EXCLUDED.ordering
        """, {"dashboard_id": r["dashboard_id"], "page_id": r["page_id"],
              "widget_id": r["widget_id"], "filter_id": r["filter_id"],
              "ordering": r.get("ordering", i)})
    return len(rows)


def upsert_role_grants(cur, rows: list[dict], code_to_id: dict[str, int]) -> dict[str, int]:
    """Replace the 4-axis grants for each listed role.
    For idempotency we DELETE-then-INSERT per role (scoped to that role only).

    Wildcard sentinel: a role's `dashboards` or `widgets` axis may be `"*"`
    to expand to every catalog entry at load time. The expansion reads
    the live ops_perm catalog, so wildcard roles widen automatically as
    new dashboards / widgets land."""
    counts = {"role_dashboard": 0, "role_widget": 0}

    cur.execute("SELECT id FROM ops_perm.dashboard")
    all_dashboards = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT id, dashboard_id FROM ops_perm.page")
    pages_by_dashboard = cur.fetchall()
    cur.execute("SELECT id FROM ops_perm.widget")
    all_widgets = [r[0] for r in cur.fetchall()]
    # page<->widget placement catalog + page->dashboard map (for page expansion)
    cur.execute("SELECT page_id, widget_id FROM ops_perm.page_widget")
    widget_pages: dict[str, list[str]] = {}
    for page_id, widget_id in cur.fetchall():
        widget_pages.setdefault(widget_id, []).append(page_id)
    page_dash = {pid: did for pid, did in pages_by_dashboard}

    for r in rows:
        code = r["role"]
        if code not in code_to_id:
            raise ValueError(f"role_grants references unknown role: {code}")
        rid = code_to_id[code]

        # Axis A: dashboards
        cur.execute("DELETE FROM ops_perm.role_dashboard WHERE role_id = %s", (rid,))
        dashboards = r.get("dashboards", [])
        if dashboards == "*":
            dashboards = all_dashboards
        for dash_id in dashboards:
            cur.execute("""
                INSERT INTO ops_perm.role_dashboard (role_id, dashboard_id)
                VALUES (%s, %s)
            """, (rid, dash_id))
            counts["role_dashboard"] += 1

        # Axis C: widgets, page-scoped.
        # YAML `widgets:` is keyed by dashboard_id (each widget expanded across its
        # ops_perm.page_widget placements in that dashboard) OR by an explicit
        # page_id ('dash/page' -> grant only on that page). "*" = all widgets.
        cur.execute("DELETE FROM ops_perm.role_widget WHERE role_id = %s", (rid,))
        widgets = r.get("widgets") or {}
        if widgets == "*":
            widgets = {did: list(all_widgets) for did in dashboards}
        for key, widget_ids in widgets.items():
            explicit_page = key if "/" in key else None
            for wid in widget_ids:
                if explicit_page:
                    targets = [(page_dash.get(explicit_page, explicit_page.split("/")[0]),
                                explicit_page)]
                else:  # dashboard key -> expand across this widget's cataloged pages
                    targets = [(key, pg) for pg in widget_pages.get(wid, [])
                               if page_dash.get(pg) == key]
                    if not targets:
                        log.warning("role %s: widget %s not cataloged on any page of "
                                    "dashboard %s; skipped", code, wid, key)
                for did, pg in targets:
                    cur.execute("""
                        INSERT INTO ops_perm.role_widget (role_id, dashboard_id, page_id, widget_id)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                    """, (rid, did, pg, wid))
                    counts["role_widget"] += 1

        # Axes B and D (filter value caps) live in ops_perm.role_scope
        # (upsert_role_scope, seeded from roles.yaml `scope:`). The resolver
        # derives the filter bar from the structural page_filter/widget_filter
        # tables ∩ role_scope ∩ user_scope. global_filters/widget_filters blocks
        # in role_grants.yaml are ignored.
    return counts


def upsert_users(cur, rows: list[dict], code_to_id: dict[str, int]) -> tuple[int, int]:
    """Upsert ops_identity.user and grant the listed roles in ops_perm.user_role
    with source='admin_grant'."""
    user_count = 0
    grant_count = 0
    for r in rows:
        realm    = r.get("realm", "moh-staff")
        username = r["username"]
        yaml_sub = r["kc_sub"]

        # Resolve the effective kc_sub before writing. ops_identity.user has two
        # keys: PRIMARY KEY (kc_sub) and UNIQUE (realm, username). The Keycloak
        # subject is authoritative, but a runtime JIT login (the gateway /me
        # upsert), or a Keycloak user whose id could not be pinned to the YAML
        # kc_sub (e.g. it already existed with a random id), can already own this
        # (realm, username) under a different, real kc_sub than the one in
        # users.yaml. A plain INSERT ... ON CONFLICT (kc_sub) does not cover that:
        # it violates UNIQUE (realm, username), which is not the conflict target,
        # and, because the whole loader is one transaction, aborts the entire
        # security seed.
        #
        # So reconcile on the natural key first: (realm, username) is one human
        # identity. If a row already owns it, that row is this identity: keep its
        # real sub (and its audit/FK children, which a DELETE would fail on with
        # RESTRICT) and refresh the mutable fields. Downstream grants and scopes
        # use the effective sub. On a clean boot no such row exists, so the pinned
        # YAML sub is inserted.
        cur.execute(
            "SELECT kc_sub FROM ops_identity.user WHERE realm = %s AND username = %s",
            (realm, username))
        existing = cur.fetchone()
        eff_sub = str(existing[0]) if existing else yaml_sub

        if existing:
            cur.execute("""
                UPDATE ops_identity.user
                   SET email        = %(email)s,
                       full_name    = %(full_name)s,
                       role_label   = %(role_label)s,
                       last_seen_at = now()
                 WHERE kc_sub = %(kc_sub)s::uuid
            """, {
                "email":      r.get("email"),
                "full_name":  r.get("full_name"),
                "role_label": r.get("role_label"),
                "kc_sub":     eff_sub,
            })
        else:
            # No row owns (realm, username) yet: insert the pinned YAML sub.
            # ON CONFLICT (kc_sub) still handles a same-sub row whose realm or
            # username changed in the YAML (a rename): the natural key is free
            # (checked above), so re-pointing it here cannot collide.
            cur.execute("""
                INSERT INTO ops_identity.user
                    (kc_sub, realm, username, email, full_name, role_label)
                VALUES (%(kc_sub)s::uuid, %(realm)s, %(username)s, %(email)s,
                        %(full_name)s, %(role_label)s)
                ON CONFLICT (kc_sub) DO UPDATE
                  SET realm      = EXCLUDED.realm,
                      username   = EXCLUDED.username,
                      email      = EXCLUDED.email,
                      full_name  = EXCLUDED.full_name,
                      role_label = EXCLUDED.role_label,
                      last_seen_at = now()
            """, {
                "kc_sub":     yaml_sub,
                "realm":      realm,
                "username":   username,
                "email":      r.get("email"),
                "full_name":  r.get("full_name"),
                "role_label": r.get("role_label"),
            })
        user_count += 1

        for code in (r.get("roles") or []):
            if code not in code_to_id:
                raise ValueError(f"user {r['username']} references unknown role {code}")
            cur.execute("""
                INSERT INTO ops_perm.user_role (kc_sub, role_id, source, is_active)
                VALUES (%s::uuid, %s, 'admin_grant', true)
                ON CONFLICT (kc_sub, role_id) DO UPDATE
                  SET is_active = true,
                      source    = 'admin_grant'
            """, (eff_sub, code_to_id[code]))
            grant_count += 1

        # Data scope (ops_identity.user_scope): the OPA row_filter source that
        # turns a role like R-PD-HIV / R-RHB-OROMIA into row-level enforcement
        # (see 7_security/opa/policies/common.rego). Optional `scope:` block maps
        # kind -> [values]; the table accepts geography, programme, facility,
        # artifact_tier and workshop_group. YAML is authoritative: delete this
        # user's seed rows, then re-insert.
        scope = r.get("scope") or {}
        cur.execute(
            "DELETE FROM ops_identity.user_scope WHERE kc_sub = %s::uuid AND note = 'seed'",
            (eff_sub,))
        for kind, values in scope.items():
            for value in (values or []):
                cur.execute("""
                    INSERT INTO ops_identity.user_scope (kc_sub, kind, value, note)
                    VALUES (%s::uuid, %s, %s, 'seed')
                    ON CONFLICT (kc_sub, kind, value) DO UPDATE SET note = 'seed'
                """, (eff_sub, kind, str(value)))
    return user_count, grant_count


# main
# provenance (pack/unpack reconciliation contract)
# Boot order: restore bundle/state dumps (if present), then run this loader.
# A dump carries both the seeded rows and rows created at runtime (UI/API). To
# keep the YAML authoritative for what it declares, including removals, every
# YAML-owned table carries a `managed_by` column: 'seed' rows belong to the
# YAML (add, edit and remove are applied here); 'runtime' rows are never
# touched. Scope is the primary entity tables (including ops_perm.section, so
# runtime layout bands survive re-seed). Grant link tables (role_*) are
# upsert-only: removing a grant line from role_grants.yaml does not delete the
# link row.
PROVENANCE_TABLES = [
    ("ops_perm.dashboard",            ["id"]),
    ("ops_perm.page",                 ["id"]),
    ("ops_perm.section",              ["id"]),
    # ops_perm.widget is not delete-synced: widget retirement is a
    # soft orphan (see upsert_widgets), so grants and placements survive until an
    # explicit admin purge. Only the stamp applies, via NO_DELETE below.
    ("ops_perm.widget",               ["id"]),
    ("ops_perm.page_widget",          ["page_id", "widget_id"]),
    ("ops_perm.filter",               ["id"]),
    ("ops_perm.page_filter",          ["page_id", "filter_id"]),
    ("ops_perm.widget_filter",        ["dashboard_id", "page_id", "widget_id", "filter_id"]),
    ("ops_perm.mart_attrs",           ["schema_name", "table_name"]),
    ("ops_identity.identity_source",  ["code"]),
    ("ops_perm.role",                 ["code"]),
    ("ops_identity.user",             ["kc_sub"]),
]


def ensure_provenance_columns(cur) -> None:
    for table, _keys in PROVENANCE_TABLES:
        cur.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
            f"managed_by text NOT NULL DEFAULT 'runtime'")


# Tables whose seed orphans are not hard-deleted by sync_provenance: widget
# retirement is a soft orphan; upsert_widgets stamps orphaned_at and the
# resolver hides the row; the delete here would cascade grants/placements and
# defeat the revive-with-grants-intact semantics.
PROVENANCE_NO_DELETE = {"ops_perm.widget"}


def sync_provenance(cur, table: str, key_cols: list[str], keys: list[tuple]) -> int:
    """Stamp YAML-declared rows as seed-managed, then delete seed-managed rows
    the YAML no longer declares (except PROVENANCE_NO_DELETE tables, which only
    get the stamp). Returns number of rows deleted."""
    if not keys:
        return 0
    cols = ", ".join(key_cols)
    placeholders = ", ".join(["(" + ", ".join(["%s"] * len(key_cols)) + ")"] * len(keys))
    flat = [v for k in keys for v in k]
    cur.execute(
        f"UPDATE {table} SET managed_by='seed' WHERE ({cols}) IN ({placeholders})", flat)
    if table in PROVENANCE_NO_DELETE:
        return 0
    try:
        cur.execute(
            f"DELETE FROM {table} WHERE managed_by='seed' "
            f"AND ({cols}) NOT IN ({placeholders})", flat)
        return cur.rowcount
    except Exception as exc:                                       # noqa: BLE001
        log.warning("provenance delete-sync skipped for %s (FK constraint?): %s",
                    table, exc)
        return 0


def mint_service_token(cur, service_pat: str) -> bool:
    """Idempotently register the platform service-account PAT (act-as-owner)
    from the SERVICE_PAT env, format nhdw_pat_<prefix>_<secret>. owner NULL and
    role_code R-SERVICE, so the token can only impersonate a named owner via the
    X-Act-As-Owner header (R-SERVICE holds just the act_as_owner capability).
    Stored as sha256(secret), like every other PAT. No expiry (long-lived svc)."""
    if not service_pat or not service_pat.startswith("nhdw_pat_"):
        return False
    parts = service_pat.split("_", 3)
    if len(parts) != 4:
        log.warning("SERVICE_PAT malformed; skipping service-token mint")
        return False
    import hashlib
    prefix, secret = parts[2], parts[3]
    secret_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    cur.execute("""
        INSERT INTO ops_perm.api_token
            (owner_kc_sub, role_code, name, prefix, secret_hash, rate_limit_per_min)
        VALUES (NULL, 'R-SERVICE', 'platform-service-account', %s, %s, 6000)
        ON CONFLICT (prefix) DO UPDATE
          SET secret_hash = EXCLUDED.secret_hash,
              role_code   = 'R-SERVICE',
              revoked_at  = NULL
    """, (prefix, secret_hash))
    return True


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-dir", default=os.environ.get(
        "NHDW_SEED_DIR",
        str(Path(__file__).resolve().parents[2] / "bundle/seed-pack/app/security")))
    parser.add_argument("--pg-host", default=os.environ.get("POSTGRES_HOST", "localhost"))
    parser.add_argument("--pg-port", default=os.environ.get("POSTGRES_PORT", "5432"))
    parser.add_argument("--pg-user", default=os.environ.get("POSTGRES_USER", "postgres"))
    parser.add_argument("--pg-db",   default=os.environ.get("OPS_DB_NAME", "nhdw_ops"))
    # bundle/roles/*.yaml (the OPA data-permission source for the normalized
    # permission store). Defaults next to the repo; in-cluster the seed Job mounts it and sets
    # NHDW_ROLES_DIR, since the roles live outside the seed-dir configMap.
    parser.add_argument("--roles-dir", default=os.environ.get("NHDW_ROLES_DIR"))
    args = parser.parse_args()

    seed_dir = Path(args.seed_dir).resolve()
    if not seed_dir.is_dir():
        log.error("seed dir does not exist: %s", seed_dir)
        return 2
    roles_dir = (Path(args.roles_dir).resolve() if args.roles_dir
                 else seed_dir.parents[1] / "roles")

    log.info("loading security seeds from %s", seed_dir)
    log.info("-> postgres %s:%s db=%s user=%s", args.pg_host, args.pg_port, args.pg_db, args.pg_user)

    conn_kwargs = dict(
        host=args.pg_host, port=args.pg_port,
        user=args.pg_user, dbname=args.pg_db,
        password=os.environ.get("PGPASSWORD"),
    )

    with psycopg.connect(**conn_kwargs) as conn:
        with conn.cursor() as cur:
            # Read all the YAMLs upfront.
            data: dict[str, Any] = {}
            for fname in SEED_FILES_IN_ORDER:
                fpath = seed_dir / fname
                if not fpath.exists():
                    log.warning("missing seed file (skipping): %s", fname)
                    continue
                data[fname] = load_yaml(fpath)

            # The authored catalog is distributed as per-dashboard data files
            # (dashboard.yaml / pages/*.yaml / sections/*.yaml / widget.yaml next
            # to each widget's code), collected into catalog.json by
            # bundle/scripts/collect-catalog.py (run by seed-app-security.sh).
            # When present it is authoritative for the 7 catalog tables; the flat
            # YAMLs are only a fallback.
            catp = seed_dir / "catalog.json"
            if catp.exists():
                cat = json.loads(catp.read_text(encoding="utf-8"))
                for key, fname in [
                        ("dashboards", "dashboards.yaml"), ("pages", "pages.yaml"),
                        ("sections", "sections.yaml"), ("widgets", "widgets.yaml"),
                        ("page_widget", "page_widget.yaml"),
                        ("page_filter", "page_filter.yaml"),
                        ("widget_filter", "widget_filter.yaml")]:
                    data[fname] = cat.get(key, [])
                log.info("catalog.json present; distributed per-dashboard catalog "
                         "is authoritative for the 7 catalog tables")

            # Provenance columns must exist before any sync (idempotent).
            ensure_provenance_columns(cur)

            # Load in dependency order. Each helper handles its own conflict
            # semantics. Everything runs in one transaction; partial loads are
            # not possible.
            n = upsert_dashboards(cur, data.get("dashboards.yaml", []))
            log.info("dashboards:           %d", n)

            n = upsert_pages(cur, data.get("pages.yaml", []))
            log.info("pages:                %d", n)

            n = upsert_sections(cur, data.get("sections.yaml", []))
            log.info("sections:             %d", n)

            n = upsert_widgets(cur, data.get("widgets.yaml", []))
            log.info("widgets:              %d", n)

            n = upsert_page_widget(cur, data.get("page_widget.yaml", []))
            log.info("page_widget:          %d", n)

            n = upsert_filters(cur, data.get("filters.yaml", []))
            log.info("filters:              %d", n)

            n = upsert_page_filter(cur, data.get("page_filter.yaml", []))
            log.info("page_filter:          %d", n)

            n = upsert_widget_filter(cur, data.get("widget_filter.yaml", []))
            log.info("widget_filter:        %d", n)

            n = upsert_mart_attrs(cur, data.get("mart_attrs.yaml", []))
            log.info("mart_attrs:           %d", n)
            n = upsert_column_classification(cur, data.get("column_classification.yaml", []))
            log.info("column_class:         %d", n)

            n = upsert_identity_sources(cur, data.get("identity_sources.yaml", []))
            log.info("identity_sources:     %d", n)

            code_to_id = upsert_roles(cur, data.get("roles.yaml", []))
            log.info("roles:                %d", len(code_to_id))

            # Role-level default data scope (ops_perm.role_scope).
            n = upsert_role_scope(cur, data.get("roles.yaml", []), code_to_id)
            log.info("role_scope:           %d", n)

            # Normalized permission store from bundle/roles/*.yaml.
            pn, ln = upsert_permissions_from_roles(cur, roles_dir, code_to_id)
            log.info("permissions:          %d unique, %d role links", pn, ln)

            counts = upsert_role_grants(cur, data.get("role_grants.yaml", []), code_to_id)
            log.info("role_dashboard:       %d", counts["role_dashboard"])
            log.info("role_widget:          %d", counts["role_widget"])

            u, g = upsert_users(cur, data.get(USERS_FILE, []), code_to_id)
            log.info("users:                %d (+%d role grants)", u, g)

            # Register the platform service-account PAT (act-as-owner)
            # from the SERVICE_PAT env, after R-SERVICE exists (FK role_code).
            if mint_service_token(cur, os.environ.get("SERVICE_PAT", "")):
                log.info("service-token:        ok platform-service-account (R-SERVICE)")

            # provenance sync: YAML is authoritative for what it declares
            yaml_keys = {
                "ops_perm.dashboard": [(r["id"],) for r in data.get("dashboards.yaml", [])],
                "ops_perm.page":      [(r["id"],) for r in data.get("pages.yaml", [])],
                "ops_perm.section":   [(r["id"],) for r in data.get("sections.yaml", [])],
                "ops_perm.widget":    [(r["id"],) for r in data.get("widgets.yaml", [])],
                "ops_perm.page_widget": [(r["page_id"], w) for r in data.get("page_widget.yaml", [])
                                          for w in (r.get("widgets") or [r.get("widget_id")]) if w],
                "ops_perm.filter":    [(r["id"],) for r in data.get("filters.yaml", [])],
                "ops_perm.page_filter": [(r["page_id"], r["filter_id"])
                                          for r in data.get("page_filter.yaml", [])],
                "ops_perm.widget_filter": [(r["dashboard_id"], r["page_id"], r["widget_id"], r["filter_id"])
                                            for r in data.get("widget_filter.yaml", [])],
                "ops_perm.mart_attrs": [(r["schema_name"], r["table_name"])
                                         for r in data.get("mart_attrs.yaml", [])],
                "ops_identity.identity_source": [(r["code"],) for r in data.get("identity_sources.yaml", [])],
                "ops_perm.role":      [(r["code"],) for r in data.get("roles.yaml", [])],
                "ops_identity.user":  [(r["kc_sub"],) for r in data.get(USERS_FILE, [])],
            }
            for table, key_cols in PROVENANCE_TABLES:
                deleted = sync_provenance(cur, table, key_cols, yaml_keys.get(table, []))
                if deleted:
                    log.info("provenance:           %s; %d seed-removed row(s) deleted", table, deleted)
        conn.commit()
    log.info("ok security seed load complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
