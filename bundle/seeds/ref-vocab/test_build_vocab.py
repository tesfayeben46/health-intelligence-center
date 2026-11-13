#!/usr/bin/env python3
"""Regression tests for the nhdw_ref vocabulary seed (run: python3 test_build_vocab.py).

Guards the invariants the rest of the platform depends on:
  * referential integrity across the generated CSVs (a broken crosswalk target would
    fail the Postgres FK load at seed time; this catches it at build time);
  * the parity rule: programme concept codes are the exact conformed_bk strings the
    resolution view emits, and commodity codes are the view's norm_name form;
  * stable ids: the committed registry only ever appends, never renumbers.

Also runnable under pytest unchanged. Keep these green before every commit that
touches build_vocab.py, the entity model, or a supplement.
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
BUILD = HERE / "_build"


def _rows(name: str) -> list[dict]:
    with open(BUILD / name, newline="") as fh:
        return list(csv.DictReader(fh))


def _fresh_build() -> None:
    subprocess.run([sys.executable, str(HERE / "build_vocab.py")], check=True,
                   capture_output=True)


def test_referential_integrity():
    concepts = _rows("concept.csv")
    ids = {r["concept_id"] for r in concepts}
    vocabs = {r["vocabulary_id"] for r in _rows("vocabulary.csv")}
    domains = {r["domain_id"] for r in _rows("domain.csv")}
    classes = {r["concept_class_id"] for r in _rows("concept_class.csv")}
    for r in concepts:
        assert r["vocabulary_id"] in vocabs, f"concept {r['concept_code']}: unknown vocabulary"
        assert r["domain_id"] in domains, f"concept {r['concept_code']}: unknown domain"
        assert r["concept_class_id"] in classes, f"concept {r['concept_code']}: unknown class"
    for r in _rows("source_to_concept_map.csv"):
        assert r["target_concept_id"] in ids, f"s2c {r['source_code']}: dangling target"
        assert r["source_vocabulary_id"] in vocabs, f"s2c {r['source_code']}: unknown source vocab"
    for r in _rows("concept_relationship.csv"):
        assert r["concept_id_1"] in ids and r["concept_id_2"] in ids, "dangling relationship"


def test_unique_codes_per_vocabulary():
    seen = set()
    for r in _rows("concept.csv"):
        key = (r["vocabulary_id"], r["concept_code"], r["valid_from"])
        assert key not in seen, f"duplicate concept {key}"
        seen.add(key)
    seen = set()
    for r in _rows("source_to_concept_map.csv"):
        key = (r["source_code"], r["source_vocabulary_id"], r["valid_from"])
        assert key not in seen, f"duplicate crosswalk row {key}"
        seen.add(key)


def test_parity_rule_programme_codes():
    """Programme concept codes must equal the view's conformed_bk strings exactly."""
    em = yaml.safe_load((HERE.parent / "entity-model" / "entity-model.yaml").read_text())
    expected = ({p["id"] for p in em["programmes"]}
                | {i["programme"] for i in em["indicators"]}
                | {c["programme"] for c in em["commodities"]}
                | {"OTHER"})
    got = {r["concept_code"] for r in _rows("concept.csv")
           if r["vocabulary_id"] == "NHDW-PROGRAMME"}
    assert got == expected, f"programme codes drifted: only-got={got-expected} missing={expected-got}"


def test_parity_rule_commodity_norm_names():
    """Commodity concept codes must be the SQL norm_name form of the display name."""
    for r in _rows("concept.csv"):
        if r["vocabulary_id"] != "NHDW-COMMODITY":
            continue
        expect = re.sub(r"[^a-zA-Z0-9 ]", "", r["concept_name"]).lower()
        assert r["concept_code"] == expect, \
            f"commodity code {r['concept_code']!r} != norm_name({r['concept_name']!r})"


def test_every_dataelement_maps_to_a_programme():
    prog_ids = {r["concept_id"] for r in _rows("concept.csv")
                if r["vocabulary_id"] == "NHDW-PROGRAMME"}
    de = [r for r in _rows("source_to_concept_map.csv")
          if r["source_vocabulary_id"] == "DHIS2.dataElement"]
    assert de, "no DHIS2 dataElement crosswalk rows at all"
    for r in de:
        assert r["target_concept_id"] in prog_ids, \
            f"dataElement {r['source_code']} maps outside NHDW-PROGRAMME"


def test_registry_stable_and_in_local_range():
    reg = json.loads((HERE / "concept_registry.json").read_text())["assignments"]
    assert min(reg.values()) >= 2_000_000_001, "concept id below the OMOP local range"
    assert len(set(reg.values())) == len(reg), "duplicate concept ids in the registry"
    before = dict(reg)
    _fresh_build()   # regenerating must not renumber anything
    after = json.loads((HERE / "concept_registry.json").read_text())["assignments"]
    for k, v in before.items():
        assert after.get(k) == v, f"registry renumbered {k}: {v} -> {after.get(k)}"


def test_who_codes_are_published_and_off_the_parity_path():
    """The curated WHO subset must (a) exist, (b) map only NHDW concepts we
    define, and (c) never enter source_to_concept_map: the s2c map is what the
    resolution views JOIN, so an ICD-10/ATC row there would change a conformed
    key."""
    rel = _rows("concept_relationship.csv")
    con = _rows("concept.csv")
    s2c = _rows("source_to_concept_map.csv")

    by_id = {c["concept_id"]: c for c in con}
    maps_to = [r for r in rel if r["relationship_id"] == "Maps to"]
    assert maps_to, "no WHO mappings emitted"

    external = {"ICD10", "ATC"}
    for r in maps_to:
        left, right = by_id[r["concept_id_1"]], by_id[r["concept_id_2"]]
        assert left["vocabulary_id"].startswith("NHDW-"), \
            f"Maps to must originate at an NHDW concept, got {left['vocabulary_id']}"
        assert right["vocabulary_id"] in external, \
            f"Maps to must target ICD10/ATC, got {right['vocabulary_id']}"

    # every disease carries an ICD-10 code (they all have one)
    diseases = {c["concept_code"] for c in con if c["vocabulary_id"] == "NHDW-DISEASE"}
    mapped_dis = {by_id[r["concept_id_1"]]["concept_code"] for r in maps_to
                  if by_id[r["concept_id_2"]]["vocabulary_id"] == "ICD10"}
    assert diseases <= mapped_dis, f"diseases without an ICD-10 code: {diseases - mapped_dis}"

    # the parity path is untouched: no external vocabulary in the crosswalk
    ext_ids = {c["concept_id"] for c in con if c["vocabulary_id"] in external}
    assert not [m for m in s2c if m["target_concept_id"] in ext_ids], \
        "an ICD-10/ATC concept is a source_to_concept_map target, which would " \
        "re-key conformance; WHO codes belong in concept_relationship only"


def test_who_codes_target_only_declared_domains():
    """External concepts reuse the declared domains/classes (an ICD-10 code is
    a disease); an undeclared domain would break referential integrity."""
    con = _rows("concept.csv")
    domains = {d["domain_id"] for d in _rows("domain.csv")}
    classes = {c["concept_class_id"] for c in _rows("concept_class.csv")}
    for c in con:
        if c["vocabulary_id"] in ("ICD10", "ATC"):
            assert c["domain_id"] in domains, f"{c['concept_code']}: undeclared domain"
            assert c["concept_class_id"] in classes, f"{c['concept_code']}: undeclared class"


def test_built_csvs_use_lf_line_endings():
    """seed-ref-vocab.sh streams these straight into COPY, which locks onto the
    newline convention of the first data row and then rejects a terminator line
    using the other one; a CRLF csv fails the whole load with "unquoted newline
    found in data". csv.writer's default 'excel' dialect emits \\r\\n, so this
    has to be asserted rather than assumed."""
    out = Path(__file__).resolve().parent / "_build"
    built = sorted(out.glob("*.csv"))
    assert built, f"no built CSVs in {out}: run build_vocab.py first"
    for f in built:
        raw = f.read_bytes()
        assert b"\r\n" not in raw, (
            f"{f.name} has CRLF line endings; COPY will reject the load. "
            "csv.writer needs lineterminator='\\n'.")


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {name}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
