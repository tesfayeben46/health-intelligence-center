#!/usr/bin/env python3
"""validate.py: structural and compatibility validation for an NHDW seed-pack manifest.

Part of the seed engine (system code). It is dependency-light (stdlib +
PyYAML, both already used across this repo) so it runs anywhere the bundle does, offline.

  validate.py <manifest.(yaml|json)> [--system-version X.Y.Z] [--check-files]

Exit 0 = valid (and, if --system-version given, compatible). Non-zero = problems printed.

The compatibility check (`targets_system` vs the deployed system version) is the core
"seeds can't break the system" guarantee: a pack whose range excludes the running system
is refused before any data is applied (fail-closed).
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except Exception:                                       # pragma: no cover
    yaml = None

KINDS = {"mysql-dump", "sqlserver-bak", "pg-dump", "sql", "kcadm", "mc", "api", "csv", "loader"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


# version range ("targets_system")
def parse_version(s: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", str(s).split("-")[0].split("+")[0])
    if not nums:
        raise ValueError(f"unparseable version {s!r}")
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def _cmp(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    return (a > b) - (a < b)


def satisfies(system_version: str, targets_system: str) -> tuple[bool, str]:
    """True iff system_version meets every space-separated constraint (ANDed)."""
    sv = parse_version(system_version)
    for raw in str(targets_system).split():
        m = re.match(r"^(>=|<=|==|~>|>|<)?\s*(\d[\w.\-+]*)$", raw)
        if not m:
            return False, f"unparseable constraint {raw!r}"
        op, ver = m.group(1) or ">=", m.group(2)
        tv = parse_version(ver)
        c = _cmp(sv, tv)
        ok = {
            ">=": c >= 0, ">": c > 0, "<=": c <= 0, "<": c < 0, "==": c == 0,
        }.get(op)
        if op == "~>":                                  # pessimistic: >= tv, < next-minor
            floor = tv
            upper = (tv[0], tv[1] + 1, 0)
            ok = _cmp(sv, floor) >= 0 and _cmp(sv, upper) < 0
        if not ok:
            return False, f"system {system_version} fails constraint {raw!r}"
    return True, "compatible"


# structural validation
def validate_manifest(obj: dict, pack_root: Path | None = None,
                      check_files: bool = False) -> list[str]:
    errs: list[str] = []
    if not isinstance(obj, dict):
        return ["manifest is not a mapping"]
    if obj.get("apiVersion") != "nhdw.seed/v1":
        errs.append("apiVersion must be 'nhdw.seed/v1'")
    if not isinstance(obj.get("seed_pack_version"), str):
        errs.append("seed_pack_version (string) is required")
    ts = obj.get("targets_system")
    if not isinstance(ts, str):
        errs.append("targets_system (string) is required")
    else:
        try:
            satisfies("1.0.0", ts)                      # parseability probe
        except Exception as e:                          # noqa: BLE001
            errs.append(f"targets_system unparseable: {e}")
    entries = obj.get("entries")
    if not isinstance(entries, list) or not entries:
        errs.append("entries must be a non-empty list")
        return errs
    seen: set[str] = set()
    for i, e in enumerate(entries):
        tag = f"entries[{i}]"
        if not isinstance(e, dict):
            errs.append(f"{tag}: not a mapping"); continue
        eid = e.get("id")
        if not isinstance(eid, str) or not ID_RE.match(eid):
            errs.append(f"{tag}: id must be kebab-case")
        elif eid in seen:
            errs.append(f"{tag}: duplicate id {eid!r}")
        else:
            seen.add(eid)
        if not isinstance(e.get("target"), str):
            errs.append(f"{tag} ({eid}): target (string) required")
        if e.get("kind") not in KINDS:
            errs.append(f"{tag} ({eid}): kind must be one of {sorted(KINDS)}")
        locs = [k for k in ("file", "glob", "dir") if k in e]
        if len(locs) > 1:
            errs.append(f"{tag} ({eid}): file/glob/dir are mutually exclusive")
        if not locs and e.get("kind") not in {"api", "kcadm"}:
            errs.append(f"{tag} ({eid}): kind {e.get('kind')} needs file, glob, or dir")
        if check_files and pack_root and locs:
            loc = e[locs[0]]
            hits = list(pack_root.glob(loc)) if locs[0] in ("glob",) else [pack_root / loc]
            if not any(p.exists() for p in hits):
                errs.append(f"{tag} ({eid}): {locs[0]} {loc!r} resolves to nothing under {pack_root}")
    return errs


def load(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        if yaml is None:
            raise SystemExit("PyYAML not available to read a .yaml manifest")
        return yaml.safe_load(text)
    return json.loads(text)


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate an NHDW seed-pack manifest.")
    ap.add_argument("manifest")
    ap.add_argument("--system-version", help="deployed system version to check targets_system against")
    ap.add_argument("--check-files", action="store_true", help="also verify referenced files exist")
    a = ap.parse_args()
    path = Path(a.manifest)
    obj = load(path)
    errs = validate_manifest(obj, pack_root=path.parent, check_files=a.check_files)
    if errs:
        print("INVALID manifest:")
        for e in errs:
            print(f"  FAIL: {e}")
        return 1
    print(f"ok: manifest structurally valid: {obj['seed_pack_version']} "
          f"({len(obj['entries'])} entries), targets_system {obj['targets_system']!r}")
    if a.system_version:
        ok, why = satisfies(a.system_version, obj["targets_system"])
        print(f"{'ok' if ok else 'FAIL'}: compatibility: {why}")
        return 0 if ok else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
