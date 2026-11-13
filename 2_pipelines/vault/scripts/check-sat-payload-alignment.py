"""Assert the payload/hashdiff single-source-of-truth contract holds:

  1. macros/sat_payloads.sql defines one spec per satellite; every spec's
     `volatile` list is a subset of its `payload` list, and volatile
     columns are limited to the documented audit-timestamp class.
  2. Every raw-vault sat model consumes src_payload=sat_payload('<key>')
     with <key> == its own name minus the 'sat_' prefix; no inline
     payload lists that could drift from the stage hashdiff.
  3. Every stage view computes its hashdiff via sat_hashdiff('<key>') with
     the alias 'hd_<key>', so hashdiff == payload minus volatile, always.
  4. Keys map 1:1: every spec key has exactly one sat and one stage
     hashdiff; no sat/stage uses a key missing from the spec.

Run from anywhere: python3 2_pipelines/vault/scripts/check-sat-payload-alignment.py
Exit code 0 = aligned; 1 = violations (printed).
"""
from __future__ import annotations
import ast, pathlib, re, sys

VAULT = pathlib.Path(__file__).resolve().parents[1]
RAW_VAULT = VAULT / "models" / "raw_vault"
SPEC_FILE = VAULT / "macros" / "sat_payloads.sql"

# The single volatile-timestamp policy: only pure audit timestamps may be
# excluded from a hashdiff.
ALLOWED_VOLATILE = {"created", "last_updated", "modified_date", "meta_last_updated"}

errors: list[str] = []


def load_spec() -> dict:
    m = re.search(r"do return\(\((?P<d>\{.*?\})\)\s*\)|do return\((?P<d2>\{.*\})\)\s*-%\}",
                  SPEC_FILE.read_text(), re.DOTALL)
    if not m:
        sys.exit(f"FATAL: could not locate the spec dict in {SPEC_FILE}")
    literal = m.group("d") or m.group("d2")
    try:
        return ast.literal_eval(literal)
    except (ValueError, SyntaxError) as e:
        sys.exit(f"FATAL: spec dict in {SPEC_FILE} is not a Python/Jinja literal: {e}")


spec = load_spec()

# 1. spec internal consistency
for key, entry in spec.items():
    payload, volatile = entry["payload"], entry["volatile"]
    if len(set(payload)) != len(payload):
        errors.append(f"spec[{key}]: duplicate payload columns")
    for col in volatile:
        if col not in payload:
            errors.append(f"spec[{key}]: volatile column '{col}' not in payload")
        if col not in ALLOWED_VOLATILE:
            errors.append(f"spec[{key}]: '{col}' is not an allowed audit timestamp "
                          f"({sorted(ALLOWED_VOLATILE)}); hashdiff exclusions are for "
                          "pure audit timestamps ONLY")

# 2. sats consume sat_payload('<own key>')
sat_keys: set[str] = set()
for f in sorted(RAW_VAULT.rglob("sats/*.sql")):
    key = f.stem.removeprefix("sat_")
    sql = f.read_text()
    m = re.search(r"src_payload\s*=\s*sat_payload\('([^']+)'\)", sql)
    if not m:
        errors.append(f"{f.name}: src_payload is not sat_payload('<key>'); inline "
                      "lists can drift from the stage hashdiff")
        continue
    if m.group(1) != key:
        errors.append(f"{f.name}: consumes sat_payload('{m.group(1)}') but its own key is '{key}'")
    if key not in spec:
        errors.append(f"{f.name}: key '{key}' missing from macros/sat_payloads.sql")
    sat_keys.add(key)

# 3. stages compute hd via sat_hashdiff('<key>') aliased hd_<key>
stage_keys: set[str] = set()
for f in sorted(RAW_VAULT.rglob("stage/*.sql")):
    sql = f.read_text()
    for m in re.finditer(r"columns\s*=\s*sat_hashdiff\('([^']+)'\)\s*,\s*alias\s*=\s*'([^']+)'", sql):
        key, alias = m.group(1), m.group(2)
        if alias != f"hd_{key}":
            errors.append(f"{f.name}: sat_hashdiff('{key}') aliased '{alias}' (expected 'hd_{key}')")
        if key not in spec:
            errors.append(f"{f.name}: key '{key}' missing from macros/sat_payloads.sql")
        if key in stage_keys:
            errors.append(f"{f.name}: key '{key}' hashed by more than one stage view")
        stage_keys.add(key)
    # inline hashdiffs bypass the contract
    if re.search(r"columns\s*=\s*\[[^\]]*\]\s*,\s*alias\s*=\s*'hd_", sql, re.DOTALL):
        errors.append(f"{f.name}: inline hashdiff column list bypasses sat_hashdiff()")

# 4. 1:1 coverage
for key in sorted(spec):
    if key not in sat_keys:
        errors.append(f"spec[{key}]: no sat model consumes it")
    if key not in stage_keys:
        errors.append(f"spec[{key}]: no stage view hashes it")
for key in sorted(sat_keys - set(spec)) + sorted(stage_keys - set(spec)):
    pass  # already reported above per-file

if errors:
    print(f"{len(errors)} payload/hashdiff alignment violation(s):")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print(f"OK: {len(sat_keys)} satellites aligned (payload == hashdiff + documented "
      "audit-timestamp exclusions, single source of truth).")
