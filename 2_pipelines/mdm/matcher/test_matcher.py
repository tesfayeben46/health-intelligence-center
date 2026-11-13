#!/usr/bin/env python3
"""Regression tests for the MDM matcher (run against a throwaway Postgres).

Builds fixture twins of the vault surfaces the matcher reads
(v_facility_resolution and the two coordinate sats, as tables with the same columns)
plus the real ops_governance DDL, then asserts the matcher contract:

  1. unambiguous exact name        -> one name_exact candidate, score 1.0
  2. name shared by 2 masters      -> one name_ambiguous candidate per master,
                                     with distance_km tie-break features
  3. near-name (no exact twin)     -> name_probabilistic with the similarity,
                                     below-threshold pairs absent
  4. master with no twin           -> no candidate at all
  5. steward-decided candidates    -> never touched by a rerun (status guard)
  6. rerun                         -> idempotent (same rows, refreshed run uid)
  7. commodity lane                -> near-name only, vocabulary identity
  8. rules registry + run log      -> registry thresholds / disabled rules,
                                     run row + sightings, first-seen kept

Usage:  TEST_DSN=postgresql://... python3 test_matcher.py
"""
from __future__ import annotations

import os
import sys
import uuid

import psycopg

import matcher

FIXTURE = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;
DROP SCHEMA IF EXISTS nhdw_vault_business CASCADE;
DROP SCHEMA IF EXISTS ops_governance CASCADE;
CREATE SCHEMA nhdw_vault_business;
CREATE SCHEMA ops_governance;

CREATE TABLE nhdw_vault_business.v_facility_resolution (
    hk_source_facility bytea,
    source_system      text,
    source_name        text,
    norm_name          text,
    conformed_bk       text,
    match_strategy     text
);
-- coordinate types mirror the live sats (MFR double precision, mBrana numeric)
CREATE TABLE nhdw_vault_business.v_sat_mfr_facility_current (
    hk_mfr_facility bytea, latitude double precision, longitude double precision
);
CREATE TABLE nhdw_vault_business.v_sat_mbrana_facility_current (
    hk_mbrana_facility bytea, latitude numeric, longitude numeric,
    woreda text, zone text, town text
);

-- conformed masters (FAC side, via MFR)
INSERT INTO nhdw_vault_business.v_facility_resolution VALUES
  (decode(md5('mfr-1'),'hex'),'MFR.location','Adama Health Center','adama health center','FAC-OR-HC-a1','facility_id'),
  (decode(md5('mfr-2'),'hex'),'MFR.location','Bishoftu Clinic','bishoftu clinic','FAC-OR-CL-b1','facility_id'),
  (decode(md5('mfr-3'),'hex'),'MFR.location','Bishoftu Clinic','bishoftu clinic','FAC-OR-CL-b2','facility_id'),
  (decode(md5('mfr-4'),'hex'),'MFR.location','Nejashi II','nejashi ii','FAC-TI-HP-n2','facility_id'),
  (decode(md5('mfr-5'),'hex'),'MFR.location','Lonely Hospital','lonely hospital','FAC-AA-HO-l1','facility_id');

-- un-conformed records (mBrana, norm_name strategy)
INSERT INTO nhdw_vault_business.v_facility_resolution VALUES
  (decode(md5('mb-1'),'hex'),'mBrana.dbo','Adama Health Center','adama health center','adama health center','norm_name'),
  (decode(md5('mb-2'),'hex'),'mBrana.dbo','Bishoftu Clinic','bishoftu clinic','bishoftu clinic','norm_name'),
  (decode(md5('mb-3'),'hex'),'mBrana.dbo','Nejashi','nejashi','nejashi','norm_name'),
  (decode(md5('mb-4'),'hex'),'mBrana.dbo','Totally Different','totally different','totally different','norm_name');

-- coordinates: masters b1 (8.75,38.98) and b2 (9.60,41.85); mb-2 sits near b1
INSERT INTO nhdw_vault_business.v_sat_mfr_facility_current VALUES
  (decode(md5('mfr-2'),'hex'),8.75,38.98),
  (decode(md5('mfr-3'),'hex'),9.60,41.85);
INSERT INTO nhdw_vault_business.v_sat_mbrana_facility_current VALUES
  (decode(md5('mb-2'),'hex'),8.76,38.99,'Ada''a','East Shewa','Bishoftu'),
  (decode(md5('mb-9'),'hex'),NULL,NULL,NULL,NULL,NULL);  -- attribute-less record must not crash

-- commodity lane: the master universe is the VOCABULARY
DROP SCHEMA IF EXISTS nhdw_ref CASCADE;
CREATE SCHEMA nhdw_ref;
CREATE TABLE nhdw_ref.concept (
    concept_code text, vocabulary_id text, invalid_reason text,
    valid_from date DEFAULT '2000-01-01', valid_to date DEFAULT '2099-12-31'
);
INSERT INTO nhdw_ref.concept (concept_code, vocabulary_id, invalid_reason) VALUES
  ('amoxicillin 500mg', 'NHDW-COMMODITY', NULL),
  ('paracetamol 500mg', 'NHDW-COMMODITY', NULL),
  ('retired thing',     'NHDW-COMMODITY', 'D'),      -- invalid: never a master
  ('amoxicillin 500mg', 'SOME-OTHER-VOCAB', NULL);   -- other vocab: not commodity

CREATE TABLE nhdw_vault_business.v_commodity_resolution (
    hk_source_commodity bytea,
    source_system  text,
    source_name    text,
    norm_name      text,
    conformed_bk   text,
    match_strategy text
);
INSERT INTO nhdw_vault_business.v_commodity_resolution VALUES
  -- crosswalked: never a candidate source
  (decode(md5('cm-1'),'hex'),'HCMIS','Amoxicillin 500mg','amoxicillin 500mg','amoxicillin 500mg','vocabulary'),
  -- NEAR-name miss: the one real candidate (misspelt single l)
  (decode(md5('cm-2'),'hex'),'mBrana','Amoxicilin 500mg','amoxicilin 500mg','amoxicilin 500mg','norm_name'),
  -- Exact-name miss: conforms implicitly by hash equality, no candidate
  (decode(md5('cm-3'),'hex'),'mBrana','Paracetamol 500mg','paracetamol 500mg','paracetamol 500mg','norm_name'),
  -- nothing close in the vocabulary: no candidate
  (decode(md5('cm-4'),'hex'),'mBrana','Weird Elixir','weird elixir','weird elixir','norm_name');
"""


def apply_ops_ddl(conn, repo_root: str) -> None:
    """The real candidate-table DDL (35_mdm.sql) minus psql meta/grants."""
    src = open(os.path.join(repo_root, "3_storage/postgres/init/35_mdm.sql")).read()
    stmts, keep, buf = [], False, []
    for line in src.splitlines():
        if line.startswith("\\connect"):
            continue
        buf.append(line)
    body = "\n".join(buf)
    body = body[: body.index("DO $$")]        # grants need the real roles; skip
    conn.execute(body)
    conn.commit()


def q(conn, sql, *args):
    with conn.cursor() as cur:
        cur.execute(sql, args or None)
        return cur.fetchall()


def main() -> int:
    dsn = os.environ["TEST_DSN"]
    fails = []

    def check(name, ok, detail=""):
        print(("  ok   " if ok else "  FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
        if not ok:
            fails.append(name)

    conn = psycopg.connect(dsn)
    conn.execute(FIXTURE)
    conn.commit()
    apply_ops_ddl(conn, os.environ.get("REPO_ROOT", "/repo"))

    pairs = matcher.generate_candidates(conn, threshold=0.5)
    by_rule = {}
    for p in pairs:
        by_rule.setdefault(p["match_rule"], []).append(p)

    # 1. unambiguous exact
    ex = by_rule.get("name_exact", [])
    check("one name_exact candidate", len(ex) == 1, str(len(ex)))
    check("name_exact targets the master", ex and ex[0]["master_bk"] == "FAC-OR-HC-a1")
    check("name_exact score 1.0", ex and float(ex[0]["score"]) == 1.0)

    # 2. ambiguous: one candidate per possible master + geo tie-break
    am = by_rule.get("name_ambiguous", [])
    check("two name_ambiguous candidates", len(am) == 2, str(len(am)))
    dists = {p["master_bk"]: p["match_features"].get("distance_km") for p in am}
    check("ambiguous rows carry n_targets=2", all(p["match_features"]["n_targets"] == 2 for p in am))
    both = dists.get("FAC-OR-CL-b1") is not None and dists.get("FAC-OR-CL-b2") is not None
    check("distance_km computed for both masters", both, str(dists))
    check("near master closer than far master",
          both and float(dists["FAC-OR-CL-b1"]) < 5 < float(dists["FAC-OR-CL-b2"]), str(dists))
    check("ambiguous rows carry the admin-area tie-break",
          all(p["match_features"].get("left_woreda") == "Ada'a" for p in am))

    # 3. probabilistic only where no exact twin
    pr = by_rule.get("name_probabilistic", [])
    check("nejashi to nejashi ii probabilistic candidate",
          any(p["master_bk"] == "FAC-TI-HP-n2" for p in pr), str(pr))
    check("probabilistic similarity < 1.0", all(0 < float(p["score"]) < 1 for p in pr))
    check("no probabilistic row for exact-twin names",
          not any(p["match_features"].get("norm_name") in ("adama health center", "bishoftu clinic") for p in pr))

    # 4. unmatched stays unmatched
    check("'totally different' produced no exact/ambiguous candidate",
          not any(p["match_features"].get("norm_name") == "totally different"
                  for p in ex + am))
    check("'lonely hospital' master attracted no candidate",
          not any(p["master_bk"] == "FAC-AA-HO-l1" for p in pairs))

    # store + idempotency
    run1 = matcher.store_candidates(conn, pairs, context="ET", run_uid=str(uuid.uuid4()))
    n1 = q(conn, "SELECT count(*) FROM ops_governance.mdm_match_candidate")[0][0]
    check("stored rows == generated pairs", n1 == len(pairs), f"{n1} vs {len(pairs)}")

    # 5. steward protection: decide one, rerun, assert untouched
    conn.execute("""UPDATE ops_governance.mdm_match_candidate
                       SET status='decided', score=0.123
                     WHERE match_rule='name_exact'""")
    conn.commit()
    matcher.store_candidates(conn, pairs, context="ET", run_uid=str(uuid.uuid4()))
    kept = q(conn, "SELECT score FROM ops_governance.mdm_match_candidate WHERE status='decided'")
    check("decided candidate untouched by rerun", kept and float(kept[0][0]) == 0.123, str(kept))

    # 6. rerun adds nothing
    n2 = q(conn, "SELECT count(*) FROM ops_governance.mdm_match_candidate")[0][0]
    check("rerun is idempotent", n2 == n1, f"{n2} vs {n1}")

    # 7. commodity lane
    cpairs = matcher.generate_candidates(conn, threshold=0.5,
                                         entity_class="commodity")
    check("exactly one commodity candidate (the near-name miss)",
          len(cpairs) == 1, str(cpairs))
    cp = cpairs[0] if cpairs else {}
    check("near-name maps onto the valid concept",
          cp.get("master_bk") == "amoxicillin 500mg", str(cp))
    check("commodity candidates are probabilistic only",
          cp.get("match_rule") == "name_probabilistic")
    check("exact-name miss conforms implicitly (no candidate)",
          not any(p["match_features"].get("norm_name") == "paracetamol 500mg"
                  for p in cpairs))
    check("unrelated name attracts nothing",
          not any(p["match_features"].get("norm_name") == "weird elixir"
                  for p in cpairs))

    matcher.store_candidates(conn, cpairs, context="ET",
                             run_uid=str(uuid.uuid4()), entity_class="commodity")
    row = q(conn, """SELECT entity_class_code, right_source_system_code,
                            suggested_master_bk
                     FROM ops_governance.mdm_match_candidate
                     WHERE entity_class_code = 'commodity'""")
    check("commodity candidate stored with vocabulary identity",
          row == [("commodity", "NHDW.vocabulary", "amoxicillin 500mg")], str(row))
    matcher.store_candidates(conn, cpairs, context="ET",
                             run_uid=str(uuid.uuid4()), entity_class="commodity")
    n3 = q(conn, "SELECT count(*) FROM ops_governance.mdm_match_candidate "
                 "WHERE entity_class_code = 'commodity'")[0][0]
    check("commodity rerun is idempotent", n3 == 1, str(n3))

    # 8. rules registry + run log (init 45)
    check("no registry yet: load_rules is None (0.1.0 behaviour)",
          matcher.load_rules(conn, "ET") is None)
    check("no run log yet: start_run opens nothing",
          matcher.start_run(conn, str(uuid.uuid4()), "ET", {}) is False)

    src45 = open(os.path.join(os.environ.get("REPO_ROOT", "/repo"),
                              "3_storage/postgres/init/45_mdm_rules_runs.sql")).read()
    body45 = "\n".join(l for l in src45.splitlines() if not l.startswith("\\connect"))
    conn.execute(body45[: body45.index("DO $$")])   # grants need the real roles
    conn.commit()

    rules = matcher.load_rules(conn, "ET")
    check("registry seeds the four built-in rules", rules is not None and len(rules) == 4, str(rules))
    check("seeded similarity threshold is the old default 0.85",
          matcher.sql_threshold(rules, "facility", 0.5) == 0.85)
    check("unlisted class falls back to the env threshold",
          matcher.sql_threshold(rules, "person", 0.5) == 0.5)
    low = [{"match_rule": "name_probabilistic", "score": 0.80},
           {"match_rule": "name_probabilistic", "score": 0.90},
           {"match_rule": "unlisted_rule", "score": 0.10}]
    kept = matcher.apply_rules(low, rules, "facility")
    check("pairs under the rule threshold are dropped, unlisted rules pass",
          [p["score"] for p in kept] == [0.90, 0.10], str(kept))

    # disable name_ambiguous as version 2 (append-only supersede)
    conn.execute("""UPDATE ops_governance.mdm_match_rule SET recorded_to = now()
                     WHERE entity_class_code='facility' AND rule_code='name_ambiguous'
                       AND recorded_to IS NULL""")
    conn.execute("""INSERT INTO ops_governance.mdm_match_rule
                       (entity_class_code, rule_code, label, description, threshold, enabled, version)
                    VALUES ('facility','name_ambiguous','Ambiguous name','test',1.0,false,2)""")
    conn.commit()
    rules = matcher.load_rules(conn, "ET")
    check("current version is read (v2, disabled)",
          rules[("facility", "name_ambiguous")] == {"threshold": 1.0, "enabled": False, "version": 2},
          str(rules.get(("facility", "name_ambiguous"))))

    os.environ["VAULT_DSN"] = os.environ["OPS_DSN"] = dsn
    os.environ["MDM_TRGM_THRESHOLD"] = "0.5"
    s1 = matcher.run()
    run_row = q(conn, """SELECT status, finished_at IS NOT NULL, settings->>'source',
                                candidates_total, candidates_new, candidates_refreshed
                         FROM ops_governance.mdm_matcher_run WHERE run_uid = %s""", s1["run_uid"])
    check("run logged as succeeded with registry settings",
          run_row and run_row[0][:3] == ("succeeded", True, "registry"), str(run_row))
    sights = q(conn, """SELECT c.match_rule, c.status, s.outcome
                        FROM ops_governance.mdm_candidate_sighting s
                        JOIN ops_governance.mdm_match_candidate c USING (candidate_id)
                        WHERE s.run_uid = %s""", s1["run_uid"])
    check("new + refreshed counts match the sightings",
          run_row and run_row[0][4] + run_row[0][5] == len(sights), f"{run_row} vs {len(sights)}")
    check("decided candidates gain no sighting", all(st == "pending" for _, st, _ in sights), str(sights))
    check("disabled rule proposed nothing this run",
          not any(r == "name_ambiguous" for r, _, _ in sights), str(sights))
    amb = q(conn, """SELECT count(*) FROM ops_governance.mdm_match_candidate
                     WHERE match_rule='name_ambiguous' AND status='pending'""")[0][0]
    check("disabling keeps existing suggestions (nothing deleted)", amb == 2, str(amb))

    # re-enable (v3) and add a brand-new source record -> one new + refreshed
    conn.execute("""UPDATE ops_governance.mdm_match_rule SET recorded_to = now()
                     WHERE entity_class_code='facility' AND rule_code='name_ambiguous'
                       AND recorded_to IS NULL""")
    conn.execute("""INSERT INTO ops_governance.mdm_match_rule
                       (entity_class_code, rule_code, label, description, threshold, enabled, version)
                    VALUES ('facility','name_ambiguous','Ambiguous name','test',1.0,true,3)""")
    conn.execute("""INSERT INTO nhdw_vault_business.v_facility_resolution VALUES
                    (decode(md5('mb-5'),'hex'),'mBrana.dbo','Lonely Hospital','lonely hospital',
                     'lonely hospital','norm_name')""")
    conn.commit()
    s2 = matcher.run()
    out2 = q(conn, """SELECT c.match_rule, s.outcome FROM ops_governance.mdm_candidate_sighting s
                      JOIN ops_governance.mdm_match_candidate c USING (candidate_id)
                      WHERE s.run_uid = %s ORDER BY 1, 2""", s2["run_uid"])
    check("re-enabled rule refreshes its pending suggestions, new record is 'new'",
          out2 == [("name_ambiguous", "refreshed"), ("name_ambiguous", "refreshed"),
                   ("name_exact", "new")], str(out2))
    FIRST = """SELECT min(r.started_at) FROM ops_governance.mdm_candidate_sighting s
               JOIN ops_governance.mdm_matcher_run r USING (run_uid)
               JOIN ops_governance.mdm_match_candidate c USING (candidate_id)
               WHERE c.suggested_master_bk = 'FAC-AA-HO-l1'"""
    first = q(conn, FIRST)[0][0]
    s3 = matcher.run()
    out3 = q(conn, "SELECT DISTINCT outcome FROM ops_governance.mdm_candidate_sighting "
                   "WHERE run_uid = %s", s3["run_uid"])
    check("third run only refreshes", out3 == [("refreshed",)], str(out3))
    check("first-seen survives the rerun (the upsert no longer erases it)",
          first is not None and q(conn, FIRST)[0][0] == first, str(first))

    fail_uid = str(uuid.uuid4())
    matcher.start_run(conn, fail_uid, "ET", {})
    matcher.finish_run(conn, fail_uid, sightings=[], counts={}, total=0, error="boom")
    st = q(conn, "SELECT status, error FROM ops_governance.mdm_matcher_run WHERE run_uid = %s", fail_uid)
    check("a failed run is recorded as failed with its error", st == [("failed", "boom")], str(st))

    print(("PASS" if not fails else f"FAIL: {fails}"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
