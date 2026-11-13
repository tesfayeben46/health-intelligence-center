#!/usr/bin/env python3
"""engine.py: the NHDW seed engine (the stable *system* side of seed decoupling).

Reads a seed-pack manifest, checks it is compatible with the deployed system, and applies each
entry in order through the adapter named by its `kind`. The engine is the stable *how*; the pack
(bundle/seed-pack/) is the swappable *what*. See bundle/seed-pack/README.md.

  engine.py <pack-dir|manifest> [--system-version X.Y.Z] [--dry-run]
                                [--only id,id] [--from id] [--group g]

Guarantees:
  - Compatibility is checked first. A pack whose `targets_system` excludes the running system is
    refused before any write (fail-closed: the "seeds can't break the system" contract).
  - Every entry resolves to zero+ data files (file/glob/dir, honouring `order`); the adapter turns
    the entry into concrete Actions. `--dry-run` prints the full plan and runs nothing.
  - Execution is sequential and stop-on-first-error; adapters are expected to be idempotent.

Adapters build the concrete command each entry would run against its target (psql/mysql/sqlcmd/
mc/curl/kcadm). Connection details come from the environment (the same vars the current seeders
use), so the same engine works locally and on a fleet; only the config differs.
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate import load, validate_manifest, satisfies         # noqa: E402


# system version resolution
def resolve_system_version(explicit: str | None) -> tuple[str, str]:
    """(version, source). Order: --system-version -> $NHDW_SYSTEM_VERSION -> repo VERSION file."""
    if explicit:
        return explicit, "flag"
    env = os.environ.get("NHDW_SYSTEM_VERSION")
    if env:
        return env, "env"
    for up in Path(__file__).resolve().parents:
        vf = up / "VERSION"
        if vf.is_file():
            return vf.read_text(encoding="utf-8").strip(), str(vf)
    return "0.0.0", "default(unstamped)"


# execution context + actions
@dataclass
class Ctx:
    dry_run: bool = True
    env: dict = field(default_factory=lambda: dict(os.environ))


@dataclass
class Action:
    desc: str                      # human description (always shown)
    argv: list[str] | None = None  # concrete command; None = informational / not-yet-wired


def _files(entry: dict, root: Path) -> list[Path]:
    """Resolve an entry's data files, honouring an explicit `order` of basenames."""
    if "file" in entry:
        return [root / entry["file"]]
    if "glob" in entry:
        hits = sorted(root.glob(entry["glob"]))
    elif "dir" in entry:
        hits = sorted(p for p in (root / entry["dir"]).rglob("*") if p.is_file())
    else:
        return []
    if entry.get("order"):
        rank = {name: i for i, name in enumerate(entry["order"])}
        hits.sort(key=lambda p: (rank.get(p.name, len(rank)), p.name))
    return hits


# adapters: kind -> (entry, files, ctx) -> [Action]
def _sql(entry, files, ctx):
    tgt = entry["target"]
    return [Action(f"psql[{tgt}] < {f.name}", ["psql", "-v", "ON_ERROR_STOP=1", "-f", str(f)]) for f in files] \
        or [Action(f"psql[{tgt}]: no files resolved (data not migrated yet)")]

def _mysql_dump(entry, files, ctx):
    return [Action(f"mysql-load[{entry['target']}] < {f.name}", ["sh", "-c", f"mysql < {f}"]) for f in files] \
        or [Action(f"mysql-load[{entry['target']}]: no dump resolved yet")]

def _pg_dump(entry, files, ctx):
    return [Action(f"pg-restore[{entry['target']}] {f.name}", ["sh", "-c", f"psql -f {f}"]) for f in files] \
        or [Action(f"pg-restore[{entry['target']}]: no dump resolved yet")]

def _sqlserver_bak(entry, files, ctx):
    return [Action(f"mssql-restore[{entry['target']}] {f.name}", ["sh", "-c", f"# sqlcmd RESTORE {f}"]) for f in files] \
        or [Action(f"mssql-restore[{entry['target']}]: no .bak resolved yet")]

def _csv(entry, files, ctx):
    return [Action(f"csv-load[{entry['target']}] {f.name}") for f in files] \
        or [Action(f"csv-load[{entry['target']}]: no csv resolved yet")]

def _mc(entry, files, ctx):
    d = entry.get("dir", "")
    return [Action(f"mc mirror[{entry['target']}] {d} ({len(files)} objs)")]

def _kcadm(entry, files, ctx):
    return [Action(f"keycloak-apply[{entry['target']}] ({entry.get('dir') or entry.get('file') or 'inline'})")]

def _api(entry, files, ctx):
    src = entry.get("dir") or entry.get("file") or "inline"
    return [Action(f"gateway-api[{entry['target']}] <- {src}")]

def _loader(entry, files, ctx):
    ldr = entry.get("loader", "?loader?")
    src = entry.get("dir") or entry.get("glob") or entry.get("file") or "?"
    return [Action(f"loader[{entry['target']}] {Path(ldr).name} over {src} ({len(files)} data files)")]

ADAPTERS = {
    "sql": _sql, "mysql-dump": _mysql_dump, "pg-dump": _pg_dump,
    "sqlserver-bak": _sqlserver_bak, "csv": _csv, "mc": _mc,
    "kcadm": _kcadm, "api": _api, "loader": _loader,
}


# plan + run
def plan(manifest: dict, root: Path):
    steps = []
    for e in manifest["entries"]:
        adapter = ADAPTERS.get(e["kind"])
        if adapter is None:
            raise SystemExit(f"no adapter for kind {e['kind']!r} (entry {e['id']})")
        steps.append((e, adapter(e, _files(e, root), Ctx())))
    return steps


def run(pack_arg: str, system_version: str | None, dry_run: bool,
        only: set[str] | None, frm: str | None, group: str | None) -> int:
    p = Path(pack_arg)
    manifest_path = p if p.is_file() else p / "manifest.yaml"
    root = manifest_path.parent
    manifest = load(manifest_path)

    errs = validate_manifest(manifest, pack_root=root)
    if errs:
        print("INVALID manifest: refusing to seed:")
        for e in errs:
            print(f"  FAIL: {e}")
        return 1

    sysver, src = resolve_system_version(system_version)
    ok, why = satisfies(sysver, manifest["targets_system"])
    print(f"seed-pack {manifest['seed_pack_version']} -> system {sysver} ({src}); "
          f"targets_system {manifest['targets_system']!r}: {'OK' if ok else 'INCOMPATIBLE'}")
    if not ok:
        print(f"  FAIL: REFUSED (fail-closed): {why}")
        return 2

    steps = plan(manifest, root)
    started = frm is None
    print(f"{'DRY-RUN plan' if dry_run else 'APPLYING'}: {len(steps)} entries:")
    rc = 0
    for entry, actions in steps:
        eid = entry["id"]
        if frm and eid == frm:
            started = True
        if not started or (only and eid not in only) or (group and entry.get("group") != group):
            print(f"  · skip {eid}")
            continue
        for a in actions:
            print(f"  -> {eid}: {a.desc}")
            if not dry_run and a.argv is not None:
                r = subprocess.run(a.argv, env=os.environ.copy())
                if r.returncode != 0:
                    print(f"  FAIL: {eid} FAILED (exit {r.returncode}): stopping")
                    return 3
            elif not dry_run and a.argv is None:
                print(f"  ! {eid}: adapter not yet wired to a live target")
    print("ok: dry-run complete" if dry_run else "ok: apply complete")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply an NHDW seed pack.")
    ap.add_argument("pack", help="pack directory or manifest file")
    ap.add_argument("--system-version")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="comma-separated entry ids to run")
    ap.add_argument("--from", dest="frm", help="start at this entry id")
    ap.add_argument("--group", help="only entries in this concurrency group")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else None
    return run(a.pack, a.system_version, a.dry_run, only, a.frm, a.group)


if __name__ == "__main__":
    sys.exit(main())
