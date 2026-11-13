#!/usr/bin/env python3
"""EXPLAIN dbt models against the live warehouse before a deploy runs them.

dbt builds its manifest without asking Postgres whether the SQL is legal, so a
semantic error otherwise surfaces mid-build, after the job is scheduled, the
images are built and (for the vault) up to forty minutes are spent. Typical
cases caught here:

  * an ON-joined dim ahead of a `USING (facility_id)` chain: "common column
    name appears more than once in left table"
  * COALESCE over a BOOLEAN and a TEXT status column: "types text and boolean
    cannot be matched"

The script resolves the dbt templating the models use (`source()`, `ref()`,
the automate_dv hash macro, the is_incremental block), then EXPLAINs each
model (planned, never executed) through `kubectl exec postgres-vault-0 -- psql`.

Models using other templating are reported as skipped rather than guessed at.

Reading the output: a syntax or type error is a defect in the model. A
"relation ... does not exist" for a bi_* mart usually is not: it means that
upstream mart has not been built on this warehouse yet (a failed or pending
dbt-marts run), so the checker is reporting live state, not bad SQL.

  bundle/scripts/check-dbt-sql.py                       # every vault + marts model
  bundle/scripts/check-dbt-sql.py path/to/model.sql …   # just these
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCES: dict[str, str] = {}
NS = os.environ.get("NS", "nhdw")

# Which schema a ref() resolves to. dbt puts raw-vault models in
# nhdw_vault_raw and business-vault models in nhdw_vault_business
# (dbt_project.yml `+schema:`), so the model's own directory decides.
RAW_DIRS = ("raw_vault",)


def source_schema_map() -> dict[str, str]:
    """dbt source NAME -> its schema, read from every project's _sources*.yml.
    The two differ where it matters (`ref_vocab` lives in nhdw_ref), so using
    the name as the schema would invent relations that don't exist."""
    import yaml
    out: dict[str, str] = {}
    for project in ("vault", "marts", "staging"):
        root = REPO / "2_pipelines" / project / "models"
        if not root.is_dir():
            continue
        for f in root.rglob("_sources*.yml"):
            try:
                doc = yaml.safe_load(f.read_text()) or {}
            except Exception:                                     # noqa: BLE001
                continue
            for src in doc.get("sources") or []:
                if src.get("name"):
                    out[src["name"]] = src.get("schema", src["name"])
    return out


def marts_schema_map() -> dict[str, str]:
    """model directory -> schema, read from marts/dbt_project.yml (`+schema:`)
    so the mapping is never a second copy that can drift. A nested `public:`
    group under a domain overrides to bi_public."""
    import yaml
    cfg = yaml.safe_load((REPO / "2_pipelines/marts/dbt_project.yml").read_text())
    tree = (cfg.get("models") or {}).get("nhdw_marts") or {}
    out: dict[str, str] = {}

    def walk(node: dict, path: tuple[str, ...]) -> None:
        for key, value in node.items():
            if key.startswith("+") or not isinstance(value, dict):
                continue
            schema = value.get("+schema")
            if schema:
                out["/".join(path + (key,))] = schema
            walk(value, path + (key,))

    walk(tree, ())
    return out


def seeds_schema_map() -> tuple[str, dict[str, str]]:
    """(default seed schema, per-seed `+schema:` overrides) from
    marts/dbt_project.yml `seeds:`, on the same no-drift principle as the models."""
    import yaml
    cfg = yaml.safe_load((REPO / "2_pipelines/marts/dbt_project.yml").read_text())
    tree = (cfg.get("seeds") or {}).get("nhdw_marts") or {}
    default = tree.get("+schema", "bi_public")
    overrides = {k: v.get("+schema", default) for k, v in tree.items()
                 if not k.startswith("+") and isinstance(v, dict)}
    return default, overrides


def build_ref_map() -> dict[str, str]:
    """model name -> live relation, derived from the projects' own config."""
    out: dict[str, str] = {}

    vault_root = REPO / "2_pipelines/vault/models"
    for f in vault_root.rglob("*.sql"):
        schema = ("nhdw_vault_raw" if any(p in RAW_DIRS for p in f.parts)
                  else "nhdw_vault_business")
        out[f.stem] = f"{schema}.{f.stem}"

    staging_root = REPO / "2_pipelines/staging/models"
    if staging_root.is_dir():
        for f in staging_root.rglob("*.sql"):
            out[f.stem] = f"nhdw_staging.{f.stem}"

    marts_root = REPO / "2_pipelines/marts/models"
    dir_schema = marts_schema_map()
    for f in marts_root.rglob("*.sql"):
        rel = f.relative_to(marts_root).parent.as_posix()
        schema = None
        # longest matching directory path wins (a nested public/ group beats
        # its parent domain)
        for d in sorted(dir_schema, key=len, reverse=True):
            if rel == d or rel.startswith(d + "/"):
                schema = dir_schema[d]
                break
        out[f.stem] = f"{schema}.{f.stem}" if schema else f.stem

    # dbt seeds materialise as tables too; the schema comes from the project
    # config (default bi_public, per-seed `+schema:` overrides honoured;
    # e.g. reporting_deadlines lands in bi_quality with its consumer)
    seeds_root = REPO / "2_pipelines/marts/seeds"
    if seeds_root.is_dir():
        seed_default, seed_overrides = seeds_schema_map()
        for f in seeds_root.rglob("*.csv"):
            out[f.stem] = f"{seed_overrides.get(f.stem, seed_default)}.{f.stem}"
    return out


def expand_hash(sql: str) -> str:
    """automate_dv.hash(columns=[...], alias='x') -> the SHA256 expression dbt
    emits (verified against the project's own compiled output)."""
    def repl(m: re.Match) -> str:
        cols = [a or b for a, b in re.findall(r'"([^"]+)"|\'([^\']+)\'', m.group(1))]
        alias = m.group(2)
        parts = ",\n    ".join(
            f"COALESCE(NULLIF(UPPER(TRIM(CAST({c} AS VARCHAR))), ''), '^^')" for c in cols)
        default = "||".join(["^^"] * len(cols))
        return (f"SHA256(CAST(NULLIF(CONCAT_WS('||',\n    {parts}\n), "
                f"'{default}') AS BYTEA)) AS {alias}")
    return re.sub(r"\{\{\s*automate_dv\.hash\(columns=\[(.*?)\],\s*alias='([^']+)'\)\s*\}\}",
                  repl, sql, flags=re.S)


def render(path: Path, refs: dict[str, str]) -> str | None:
    src = path.read_text()
    # Blank out /* */ comment blocks first (keeping offsets): several models
    # document themselves with an example query, and a SELECT inside a comment
    # would otherwise be mistaken for the statement start.
    masked = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), src, flags=re.S)
    # The statement starts at the first line that begins with WITH/SELECT;
    # matching "SELECT " with a trailing space would instead slice from an
    # inner subquery whenever the outer SELECT sits alone on its line.
    head = re.search(r"^[ \t]*(WITH|SELECT)\b", masked, re.M)
    if not head:
        return None
    body = src[head.start():]
    body = re.sub(r"\{\{\s*source\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)\s*\}\}",
                  lambda m: f"{SOURCES.get(m.group(1), m.group(1))}.{m.group(2)}", body)
    body = re.sub(r"\{\{\s*ref\('([^']+)'\)\s*\}\}",
                  lambda m: refs.get(m.group(1), m.group(1)), body)
    body = expand_hash(body)
    # first-run shape: the incremental predicate references {{ this }}
    body = re.sub(r"\{%\s*if is_incremental\(\).*?\{%\s*endif\s*%\}", "", body, flags=re.S)
    # var() appears with either quote style in this repo; matching only one
    # leaves the model unrendered and silently "skipped"
    body = re.sub(r"""\{\{\s*var\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]*)['"]\s*\)\s*\}\}""",
                  lambda m: m.group(2), body)
    if "{{" in body or "{%" in body:
        return None
    return "EXPLAIN\n" + body + ";"


def explain(sql: str) -> tuple[bool, str]:
    p = subprocess.run(
        ["kubectl", "-n", NS, "exec", "-i", "postgres-vault-0", "--",
         "psql", "-U", "vault_admin", "-d", "nhdw_vault", "-tA", "-v", "ON_ERROR_STOP=1"],
        input=sql, text=True, capture_output=True)
    if p.returncode == 0:
        return True, ""
    err = next((l for l in p.stderr.splitlines() if "ERROR" in l), p.stderr.strip()[:200])
    return False, err


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("models", nargs="*", help="model .sql files (default: all)")
    args = ap.parse_args()

    global SOURCES
    SOURCES = source_schema_map()
    refs = build_ref_map()
    paths = [Path(m) for m in args.models]
    if not paths:
        for project in ("vault", "marts"):
            paths += sorted((REPO / "2_pipelines" / project / "models").rglob("*.sql"))

    failed = skipped = ok = 0
    for path in paths:
        sql = render(path, refs)
        if sql is None:
            print(f"  ~ skip {path.name} (templating this checker does not resolve)")
            skipped += 1
            continue
        good, err = explain(sql)
        if good:
            print(f"  ok: {path.name}")
            ok += 1
        else:
            print(f"  FAIL: {path.name}: {err}")
            failed += 1

    print(f"\n{ok} planned · {skipped} skipped · {failed} FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
