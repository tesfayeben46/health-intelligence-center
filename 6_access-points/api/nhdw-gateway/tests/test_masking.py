#!/usr/bin/env python3
"""Regression tests for the masking primitives (app/masking.py).

Pure Python: no database, no OPA. The contract under test:
  drop        removed from rows AND reported in dropped
  hash        salted + per-response nonce -> UNLINKABLE across calls
  partial     •••<last4>; short values fully veiled
  tokenize    deterministic -> intra- and inter-response joins survive
  generalize  numbers -> 1 decimal, dates -> month; a value that cannot be
              generalized poisons the WHOLE column (mixed = contrast leak)
  fail-closed unknown mask_class drops; missing HMAC key drops hash/tokenize

Run:  python3 tests/test_masking.py   (from the gateway root)
"""
from __future__ import annotations

import datetime
import decimal
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MASKING_HMAC_KEY", "testkey")

from app.mart_attrs import ColumnClass          # noqa: E402
from app import masking as m                    # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def main() -> int:
    m._HMAC_KEY = "testkey"
    cc = {
        "lat": ColumnClass("lat", "restricted", "generalize", False),
        "lng": ColumnClass("lng", "restricted", "generalize", False),
        "telecom": ColumnClass("telecom", "restricted", "partial", True),
        "patient_id": ColumnClass("patient_id", "privileged", "tokenize", True),
        "observation": ColumnClass("observation", "privileged", "drop", True),
        "weird": ColumnClass("weird", "privileged", "wat", True),
    }
    rows = [
        {"lat": 8.9871234, "lng": 38.7612345, "telecom": "+251911223344",
         "patient_id": "P-001", "observation": "secret", "weird": "x", "name": "Adama HC"},
        {"lat": 9.1, "lng": None, "telecom": "ab", "patient_id": "P-001",
         "observation": "s2", "weird": "y", "name": "B"},
    ]
    out, dropped = m.apply_masking(
        rows, cc, ["lat", "lng", "telecom", "patient_id", "observation", "weird"])
    r0, r1 = out
    check("generalize rounds GPS to 1 decimal", r0["lat"] == 9.0 and r0["lng"] == 38.8, str(r0))
    check("partial reveals only the tail", r0["telecom"] == "•••3344" and r1["telecom"] == "•••")
    check("tokenize is deterministic within a response",
          r0["patient_id"].startswith("tok_") and r0["patient_id"] == r1["patient_id"])
    check("drop class removes the column", "observation" not in r0)
    check("unknown mask_class drops (fail-closed)", "weird" not in r0)
    check("unmasked columns survive untouched", r0["name"] == "Adama HC")
    check("dropped reports both removals", sorted(dropped) == ["observation", "weird"], str(dropped))

    cc2 = {"x": ColumnClass("x", "restricted", "hash", False)}
    a, _ = m.apply_masking([{"x": "v"}], cc2, ["x"])
    b, _ = m.apply_masking([{"x": "v"}], cc2, ["x"])
    check("hash is unlinkable across responses", a[0]["x"] != b[0]["x"])

    tok1, _ = m.apply_masking([{"x": "v"}],
                              {"x": ColumnClass("x", "privileged", "tokenize", True)}, ["x"])
    tok2, _ = m.apply_masking([{"x": "v"}],
                              {"x": ColumnClass("x", "privileged", "tokenize", True)}, ["x"])
    check("tokenize is stable across responses", tok1[0]["x"] == tok2[0]["x"])

    m._HMAC_KEY = ""
    c, drp = m.apply_masking([{"x": "v"}], cc2, ["x"])
    check("no HMAC key -> hash degrades to drop", "x" not in c[0] and drp == ["x"])
    m._HMAC_KEY = "testkey"

    cc3 = {"g": ColumnClass("g", "restricted", "generalize", False)}
    d, drp = m.apply_masking([{"g": 8.5}, {"g": "not-a-number"}], cc3, ["g"])
    check("ungeneralizable value poisons the whole column",
          "g" not in d[0] and "g" not in d[1] and drp == ["g"])

    e, _ = m.apply_masking([{"dt": datetime.date(2026, 9, 20)}],
                           {"dt": ColumnClass("dt", "restricted", "generalize", False)}, ["dt"])
    check("generalize truncates dates to month", e[0]["dt"] == "2026-09-01")

    # Trino returns every NUMERIC column as Decimal. generalize must coarsen
    # Decimal values; dropping them would be safe but would disable the class
    # on every numeric mart column.
    ccd = {"lat": ColumnClass("lat", "restricted", "generalize", False)}
    f, drp = m.apply_masking([{"lat": decimal.Decimal("11.7900")},
                              {"lat": decimal.Decimal("9.6000")}], ccd, ["lat"])
    check("generalize coarsens Decimal rather than dropping it",
          drp == [] and f[0]["lat"] == 11.8 and f[1]["lat"] == 9.6)

    g, drp = m.apply_masking([{"lat": decimal.Decimal("11.79")}, {"lat": None}],
                             ccd, ["lat"])
    check("a NULL neighbour does not poison a generalizable column",
          drp == [] and g[0]["lat"] == 11.8 and g[1]["lat"] is None)

    # the lookup key, not the transform
    # Masking is a denylist: columns_for_mart returning {} means "nothing
    # classified here" and every column is served in the clear. A
    # catalog-qualified schema ('marts.bi_supply') must resolve to the same
    # classification as the bare one, or masking fails open for that feed.
    # Policy self-tests cannot catch this because the policy is never reached.
    import asyncio
    from app import mart_attrs as ma

    ma._col_cache = {("bi_supply", "mart_ct_nodes"): {
        "lat": ColumnClass("lat", "restricted", "generalize", False)}}
    try:
        bare = asyncio.run(ma.columns_for_mart("bi_supply", "mart_ct_nodes"))
        qual = asyncio.run(ma.columns_for_mart("marts.bi_supply", "mart_ct_nodes"))
        check("bare schema finds the classified column", "lat" in bare)
        check("catalog-qualified schema finds it too (no silent fail-open)",
              "lat" in qual)
        check("an unclassified relation still returns {}",
              asyncio.run(ma.columns_for_mart("bi_supply", "mart_nothing")) == {})
    finally:
        ma._col_cache = None

    print("PASS" if not FAILS else f"FAIL: {FAILS}")
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
