"""Generate dbt schema.yml files for every vault hub/link/sat by parsing
the AutomateDV macro args directly from the SQL files. Hand-rolled models
(the Business Vault) are parsed via a fallback: the name-derived pk
(hub_x -> hk_x, matched against the `automate_dv.hash(...)` aliases; the
vault is insert-only so configs carry no unique_key to lean on). The
resulting tests catch:
  - Hubs:  uniqueness + not_null on hash_key (catches duplicate inserts);
           not_null on the natural/business key column(s).
  - Links: uniqueness + not_null on hk_link; relationships to parent
           hubs on every src_fk (catches orphan FKs + missed loads).
  - Sats:  not_null on hash_key + hashdiff; relationships from the sat pk
           to its parent hub/link (catches orphan sat rows); uniqueness on
           (hk, load_ts) (catches duplicate snapshot rows at the same
           effective time).

Output: one schema.yml per source subdir under models/raw_vault/<src>/,
plus a single models/business_vault/schema.yml.

Emission contract: each file is exactly one YAML document with one
top-level `models:` list. With several documents or `models:` keys in a
file, YAML last-key-wins would silently drop every block but the last.

Rationale:
  These tests are the validation contract for a Data Vault. They catch
  hash collisions (rare but catastrophic), missing-hub joins (silently
  empty fact tables downstream), and duplicate sat snapshots (inflated
  row counts in downstream marts).
"""
from __future__ import annotations
import pathlib, re, sys

# Portable: resolve relative to this script, never a hardcoded machine path.
MODELS = pathlib.Path(__file__).resolve().parents[1] / "models"
RAW_VAULT = MODELS / "raw_vault"
BUSINESS_VAULT = MODELS / "business_vault"

# Role-aliased FKs whose column name does not munge to an existing hub_*.
# Trailing '_fk' role suffixes are stripped generically in resolve_fk();
# only renamed roles need an entry here.
ROLE_ALIASES: dict[str, str] = {
    # lnk_dhis2_data_value: attribute_option_combo is a second role on the
    # category_option_combo hub (documented in the link's header comment).
    "hub_dhis2_attribute_option_combo": "hub_dhis2_category_option_combo",
    # lnk_mfr_org_partof: the parent side is a second role on the
    # organization hub.
    "hub_mfr_organization_parent": "hub_mfr_organization",
}

IDENT_RE = re.compile(r"[a-z_][a-z0-9_]*\Z")


def warn(msg: str) -> None:
    print(f"  WARN: {msg}", file=sys.stderr)


def parse_hash_calls(sql: str) -> list[tuple[list[str], str]]:
    """All automate_dv.hash(columns=[...], alias='...') calls as
    ([col, ...], alias) pairs. Column entries that are expressions rather
    than plain identifiers survive as raw text; callers must filter."""
    calls = []
    for m in re.finditer(
        r"automate_dv\.hash\(\s*columns\s*=\s*\[(.*?)\]\s*,\s*alias\s*=\s*'([^']+)'",
        sql, re.DOTALL,
    ):
        cols = [c.strip().strip("'\"") for c in m.group(1).split(",") if c.strip()]
        calls.append((cols, m.group(2)))
    return calls


def parse_macro_args(sql: str) -> dict:
    """Pull out src_pk, src_nk (string or list), src_fk[], src_hashdiff
    alias from an AutomateDV hub/link/sat macro call, plus hash-call
    fallbacks for hand-rolled models."""
    out: dict = {}
    m = re.search(r"src_pk\s*=\s*'([^']+)'", sql)
    if m: out["pk"] = m.group(1)
    m = re.search(r"src_nk\s*=\s*'([^']+)'", sql)
    if m: out["nks"] = [m.group(1)]
    m = re.search(r"src_nk\s*=\s*\[([^\]]+)\]", sql, re.DOTALL)
    if m:
        out["nks"] = [c.strip(" \n'\"") for c in m.group(1).split(",") if c.strip(" \n'\"")]
    m = re.search(r"src_fk\s*=\s*\[([^\]]+)\]", sql, re.DOTALL)
    if m:
        out["fks"] = [f.strip(" \n'\"") for f in m.group(1).split(",") if f.strip(" \n'\"")]
    m = re.search(r"src_hashdiff\s*=\s*\{[^}]*'alias'\s*:\s*'([^']+)'", sql)
    if m: out["hashdiff"] = m.group(1)
    out["hash_calls"] = parse_hash_calls(sql)
    return out


def name_derived_pk(stem: str, hash_calls) -> str | None:
    """Hand-rolled fallback: hub_x -> hk_x / lnk_x -> hk_x / sat_x -> hk_x,
    accepted only if a hash call actually produces that alias (the vault is
    insert-only, so configs carry no unique_key to lean on)."""
    for prefix in ("hub_", "lnk_", "sat_"):
        if stem.startswith(prefix):
            candidate = "hk_" + stem.removeprefix(prefix)
            if any(alias == candidate for _, alias in hash_calls):
                return candidate
    return None


def parse_hub(path: pathlib.Path) -> tuple[str, str, list[str]] | None:
    """(name, pk, nk_columns). Falls back to the name-derived pk + the pk
    hash-call inputs for hand-rolled hubs (Business Vault conformed hubs)."""
    meta = parse_macro_args(path.read_text())
    pk = meta.get("pk") or name_derived_pk(path.stem, meta["hash_calls"])
    if not pk:
        warn(f"{path.name}: no src_pk/unique_key found; skipped entirely")
        return None
    nks = meta.get("nks")
    if nks is None:
        # Hand-rolled hub: the hash inputs of the pk alias are the BK,
        # provided they are plain columns (expressions get no nk test).
        for cols, alias in meta["hash_calls"]:
            if alias == pk and all(IDENT_RE.fullmatch(c) for c in cols):
                nks = cols
                break
        nks = nks or []
        if not nks:
            warn(f"{path.name}: natural key not derivable; nk not_null test omitted")
    return path.stem, pk, nks


def parse_link(path: pathlib.Path) -> tuple[str, str, list[str]] | None:
    """(name, pk, fk_columns). Falls back to the name-derived pk + hash
    aliases for hand-rolled links (BV same-as links)."""
    meta = parse_macro_args(path.read_text())
    pk = meta.get("pk") or name_derived_pk(path.stem, meta["hash_calls"])
    if not pk:
        warn(f"{path.name}: no src_pk/unique_key found; skipped entirely")
        return None
    fks = meta.get("fks")
    if fks is None:
        fks = [alias for _, alias in meta["hash_calls"]
               if alias != pk and alias.startswith("hk_")]
        if not fks:
            warn(f"{path.name}: no FK columns derivable; FK tests omitted")
    return path.stem, pk, fks


def parse_sat(path: pathlib.Path) -> tuple[str, str, str | None] | None:
    """(name, pk, hashdiff_column). Falls back to the name-derived pk (or
    the first bare hk_* output column, for hand-rolled/derived sats keyed
    on a hub they don't hash themselves) and a literal `AS hashdiff`
    projection."""
    sql = path.read_text()
    meta = parse_macro_args(sql)
    pk = meta.get("pk") or name_derived_pk(path.stem, meta["hash_calls"])
    if not pk:
        m = re.search(r"^\s*(hk_[a-z0-9_]+)\s*,\s*$", sql, re.MULTILINE)
        pk = m.group(1) if m else None
    if not pk:
        warn(f"{path.name}: no pk derivable; skipped entirely")
        return None
    hd = meta.get("hashdiff")
    if hd is None and re.search(r"(?i)\bas\s+hashdiff\b", sql):
        hd = "hashdiff"
    if hd is None:
        warn(f"{path.name}: no hashdiff column derivable; hashdiff test omitted")
    return path.stem, pk, hd


def collect(dirs: list[pathlib.Path]):
    """All hub/link/sat models under `dirs` (recursive), parsed and sorted.
    `.sql.disabled` files never match the *.sql globs, by design."""
    hubs, links, sats = [], [], []
    seen: set[pathlib.Path] = set()
    for d in dirs:
        for f in sorted(d.rglob("*.sql")):
            if f in seen:
                continue
            seen.add(f)
            if f.stem.startswith("hub_"):
                if (h := parse_hub(f)): hubs.append(h)
            elif f.stem.startswith("lnk_"):
                if (l := parse_link(f)): links.append(l)
            elif f.stem.startswith("sat_"):
                if (s := parse_sat(f)): sats.append(s)
    return sorted(hubs), sorted(links), sorted(sats)


# registries (built once over both vault layers)
ALL_HUBS, ALL_LINKS, ALL_SATS = collect([RAW_VAULT, BUSINESS_VAULT])
HUB_PK: dict[str, str] = {name: pk for name, pk, _ in ALL_HUBS}
# A sat's parent is whichever hub or link owns its pk column.
PK_OWNER: dict[str, str] = {pk: name for name, pk, _ in ALL_HUBS}
PK_OWNER.update({pk: name for name, pk, _ in ALL_LINKS})


def resolve_fk(fk: str) -> tuple[str, str] | None:
    """Resolve a link FK column to (parent_hub, parent_pk_column).
    `hk_dhis2_org_unit` -> hub_dhis2_org_unit. Role suffixes are stripped
    (`hk_mbrana_zone_fk` -> hub_mbrana_zone) and renamed roles resolve via
    ROLE_ALIASES. Returns None if no hub matches (caller warns)."""
    base = fk.removesuffix("_fk")
    candidate = "hub_" + base.removeprefix("hk_")
    candidate = ROLE_ALIASES.get(candidate, candidate)
    if candidate in HUB_PK:
        return candidate, HUB_PK[candidate]
    return None


def gen_schema(hubs, links, sats) -> str:
    """Emit one YAML doc (one `version: 2`, one `models:` list) covering
    the given hubs, links and sats (in that order)."""
    lines = ["version: 2", "",
             "# Auto-generated by 2_pipelines/vault/scripts/gen-vault-tests.py.",
             "# Edit the generator, not this file. Re-run after adding models.",
             "", "models:"]
    for name, pk, nks in hubs:
        entity = name.removeprefix("hub_").replace("_", " ")
        lines += [
            f"  - name: {name}",
            f"    description: 'Hub for the {entity} business entity.'",
            "    columns:",
            f"      - name: {pk}",
            "        description: 'Hash key (PK). Must be unique; duplicate inserts break the Data Vault model.'",
            "        tests:",
            "          - not_null",
            "          - unique",
        ]
        for nk in nks:
            lines += [
                f"      - name: {nk}",
                "        description: 'Business (natural) key the hash key is derived from.'",
                "        tests:",
                "          - not_null",
            ]
    for name, pk, fks in links:
        lines += [
            f"  - name: {name}",
            f"    description: 'Link relating {len(fks)} hub roles at the grain documented in the model SQL.'",
            "    columns:",
            f"      - name: {pk}",
            "        description: 'Link hash key (PK). Composite hash of the FKs.'",
            "        tests:",
            "          - not_null",
            "          - unique",
        ]
        for fk in fks:
            tgt = resolve_fk(fk)
            if not tgt:
                # Skip relationships test if we can't resolve; emit not_null only.
                warn(f"{name}: no hub resolved for FK {fk}; relationships test omitted")
                lines += [
                    f"      - name: {fk}",
                    f"        description: 'FK component. No matching hub_* resolved for {fk}; relationships test omitted.'",
                    "        tests:",
                    "          - not_null",
                ]
                continue
            hub, hub_pk = tgt
            lines += [
                f"      - name: {fk}",
                f"        description: 'FK to {hub}.{hub_pk}.'",
                "        tests:",
                "          - not_null",
                "          - relationships:",
                f"              to: ref('{hub}')",
                f"              field: {hub_pk}",
            ]
    for name, pk, hd in sats:
        parent = PK_OWNER.get(pk)
        lines += [
            f"  - name: {name}",
            f"    description: 'Satellite carrying the descriptive payload for {pk}.'",
            "    columns:",
            f"      - name: {pk}",
        ]
        if parent:
            lines += [
                f"        description: 'FK to parent {parent}.'",
                "        tests:",
                "          - not_null",
                "          - relationships:",
                f"              to: ref('{parent}')",
                f"              field: {pk}",
            ]
        else:
            warn(f"{name}: no parent hub/link owns pk {pk}; relationships test omitted")
            lines += [
                f"        description: 'FK to parent. No hub or link owns {pk}; relationships test omitted.'",
                "        tests:",
                "          - not_null",
            ]
        if hd:
            lines += [
                f"      - name: {hd}",
                "        description: 'Hash diff over payload. Distinguishes change events.'",
                "        tests:",
                "          - not_null",
            ]
        lines += [
            "    tests:",
            "      - dbt_utils.unique_combination_of_columns:",
            f"          combination_of_columns: [{pk}, load_ts]",
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    written = 0
    # raw_vault: one schema.yml per source dir.
    for src_dir in sorted(p for p in RAW_VAULT.iterdir() if p.is_dir()):
        hubs, links, sats = collect([src_dir])
        if not (hubs or links or sats):
            continue
        out = src_dir / "schema.yml"
        out.write_text(gen_schema(hubs, links, sats))
        print(f"  wrote {out.relative_to(MODELS.parent)}")
        written += 1
    # business_vault: one schema.yml covering all subdirs.
    hubs, links, sats = collect([BUSINESS_VAULT])
    if hubs or links or sats:
        out = BUSINESS_VAULT / "schema.yml"
        out.write_text(gen_schema(hubs, links, sats))
        print(f"  wrote {out.relative_to(MODELS.parent)}")
        written += 1
    print(f"\n{written} schema.yml files written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
