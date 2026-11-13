#!/usr/bin/env python3
"""MDM matcher: proposes source-record -> master-entity candidates for stewardship.

Reads source facility records from the vault's resolution view
(`nhdw_vault_business.v_facility_resolution`), runs the match passes, and
upserts scored candidates into `nhdw_ops.ops_governance.mdm_match_candidate`.
It never merges anything itself: conformed keys change only through rows in
mdm_match_decision (steward verdicts via the console, or an explicitly
reviewed batch accept), which the vault resolution views read. An empty
decision log leaves today's conformed keys byte-identical.

Candidate shape (record -> master):
  left  = one un-conformed source record: (source_system, hex(hk_source_facility)).
          The hex hub hash is the only stable per-record identity both the
          matcher and the resolution view can derive; source_system must ride
          along because HCMIS/mBrana integer BK spaces overlap (identical
          hub hashes occur across them).
  right = the conformed master it may belong to:
          ('NHDW.conformed', conformed_bk): the FAC- slug entity.

Match passes (facility):
  name_exact         norm_name equals exactly one conformed master's name.
                     Score 1.0, still a candidate: applying it re-keys
                     rows, a deliberate stewarded change, never automatic.
  name_ambiguous     norm_name matches >1 master. One
                     candidate per possible master, geo distance_km in
                     match_features as the steward's tie-break (mBrana sat
                     carries GPS; MFR is the FAC-side coordinate authority).
  name_probabilistic pg_trgm similarity >= MDM_TRGM_THRESHOLD (default 0.85)
                     where no exact twin exists. False positives occur, e.g. at
                     0.89 ("Nejashi II" vs "Nejashi"), so it is never auto-accepted.

Commodity pass (name_probabilistic only): a commodity master is a vocabulary
concept, and concept_code == the normalised name by the parity rule, so an
exact-name commodity conforms implicitly (hash equality) and needs no
candidate. What needs stewardship is the near-name: a source commodity the
crosswalk missed whose norm_name is trigram-close to (but not equal to) a
NHDW-COMMODITY concept ("amoxicilin 500mg" vs "amoxicillin 500mg"). When the
source sets are fully crosswalked this pass emits nothing; it catches a new
source commodity that arrives misspelt. Accepting maps the record
onto the concept in v_commodity_resolution (steward_decision strategy);
curated-bulk mappings stay the vocabulary supplements' job.

Config (env):
  VAULT_DSN            postgres DSN for nhdw_vault  (reads the vault)
  OPS_DSN              postgres DSN for nhdw_ops    (writes candidates)
  MDM_TRGM_THRESHOLD   default 0.85
  MDM_CONTEXT          deployment context, default ET

Rules registry + run log (init 45):
  When ops_governance.mdm_match_rule exists, its current rows decide each
  rule's threshold and whether it runs at all; MDM_TRGM_THRESHOLD is then
  only the fallback for a rule the registry does not list. Each run writes
  mdm_matcher_run (settings used, counts) and one mdm_candidate_sighting per
  candidate it inserted or refreshed. Without those tables the matcher uses
  the env threshold for every rule and writes no run log.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone

import psycopg

MATCHER_VERSION = "0.2.0"

# Set-based candidate generation over the deployed resolution view. The
# coordinate sats are typed (MFR: explicit landing schema; mBrana: typed by
# the staging/bridge layer), which these casts rely on.
SQL_FACILITY_CANDIDATES = """
WITH fac AS (               -- conformed masters (FAC- slug side)
    SELECT DISTINCT conformed_bk, norm_name
    FROM nhdw_vault_business.v_facility_resolution
    WHERE match_strategy = 'facility_id'
),
fac_names AS (              -- how many masters share each normalised name
    SELECT norm_name, count(DISTINCT conformed_bk) AS n_targets
    FROM fac GROUP BY norm_name
),
fac_geo AS (                -- master coordinates: MFR location is the authority
                            -- (sat columns are typed by the landing schema:
                            -- double precision live)
    SELECT r.conformed_bk,
           avg(s.latitude::float8)  AS lat,
           avg(s.longitude::float8) AS lng
    FROM nhdw_vault_business.v_facility_resolution r
    JOIN nhdw_vault_business.v_sat_mfr_facility_current s
      ON s.hk_mfr_facility = r.hk_source_facility
    WHERE r.source_system = 'MFR.location'
      AND r.match_strategy = 'facility_id'
    GROUP BY r.conformed_bk
),
nameonly AS (               -- un-conformed records (no slug anywhere)
    SELECT hk_source_facility, source_system, source_name, norm_name
    FROM nhdw_vault_business.v_facility_resolution
    WHERE match_strategy = 'norm_name'
),
left_geo AS (               -- mBrana master records carry their own GPS and
                            -- admin area (numeric/text live, typed upstream).
                            -- The admin fields ride into match_features: with
                            -- the MFR Location sat empty live (a recorded
                            -- source-data gap), they are the tie-break signal
                            -- a steward actually has today.
    SELECT hk_mbrana_facility AS hk,
           latitude::float8  AS lat,
           longitude::float8 AS lng,
           woreda, zone, town
    FROM nhdw_vault_business.v_sat_mbrana_facility_current
),
exact AS (                  -- name_exact + name_ambiguous
    SELECT n.source_system,
           encode(n.hk_source_facility, 'hex') AS left_key,
           f.conformed_bk                      AS master_bk,
           1.0::numeric                        AS score,
           CASE WHEN fn.n_targets = 1 THEN 'name_exact'
                ELSE 'name_ambiguous' END      AS match_rule,
           jsonb_strip_nulls(jsonb_build_object(
               'source_name',  n.source_name,
               'norm_name',    n.norm_name,
               'n_targets',    fn.n_targets,
               'left_woreda',  lg.woreda,
               'left_zone',    lg.zone,
               'left_town',    lg.town,
               'distance_km',  round((2 * 6371 * asin(sqrt(
                   power(sin(radians((fg.lat - lg.lat) / 2)), 2)
                   + cos(radians(lg.lat)) * cos(radians(fg.lat))
                     * power(sin(radians((fg.lng - lg.lng) / 2)), 2)
               )))::numeric, 2)
           ))                                  AS match_features
    FROM nameonly n
    JOIN fac f        ON f.norm_name = n.norm_name
    JOIN fac_names fn ON fn.norm_name = n.norm_name
    LEFT JOIN left_geo lg ON lg.hk = n.hk_source_facility
    LEFT JOIN fac_geo  fg ON fg.conformed_bk = f.conformed_bk
),
probabilistic AS (          -- trigram, only where no exact twin exists
    SELECT n.source_system,
           encode(n.hk_source_facility, 'hex')          AS left_key,
           f.conformed_bk                               AS master_bk,
           similarity(n.norm_name, f.norm_name)::numeric AS score,
           'name_probabilistic'                          AS match_rule,
           jsonb_build_object(
               'source_name', n.source_name,
               'norm_name',   n.norm_name,
               'master_name', f.norm_name,
               'similarity',  round(similarity(n.norm_name, f.norm_name)::numeric, 3)
           )                                             AS match_features
    FROM nameonly n
    JOIN fac f ON f.norm_name %% n.norm_name
              AND similarity(n.norm_name, f.norm_name) >= %(threshold)s
    WHERE NOT EXISTS (SELECT 1 FROM fac fx WHERE fx.norm_name = n.norm_name)
)
SELECT source_system AS left_source_system_code,
       left_key      AS left_source_record_key,
       master_bk, score, match_rule, match_features
FROM exact
UNION ALL
SELECT source_system, left_key, master_bk, score, match_rule, match_features
FROM probabilistic
ORDER BY 1, 2, 3
"""

SQL_COMMODITY_CANDIDATES = """
WITH concepts AS (       -- the commodity master universe = the vocabulary
    SELECT c.concept_code
    FROM nhdw_ref.concept c
    WHERE c.vocabulary_id = 'NHDW-COMMODITY'
      AND c.invalid_reason IS NULL
      AND CURRENT_DATE >= c.valid_from AND CURRENT_DATE < c.valid_to
),
unmatched AS (           -- crosswalk misses that also aren't an exact concept
    SELECT r.hk_source_commodity, r.source_system, r.source_name, r.norm_name
    FROM nhdw_vault_business.v_commodity_resolution r
    WHERE r.match_strategy = 'norm_name'
      AND NOT EXISTS (SELECT 1 FROM concepts c WHERE c.concept_code = r.norm_name)
)
SELECT u.source_system                       AS left_source_system_code,
       encode(u.hk_source_commodity, 'hex')  AS left_source_record_key,
       c.concept_code                        AS master_bk,
       similarity(u.norm_name, c.concept_code)::numeric AS score,
       'name_probabilistic'                  AS match_rule,
       jsonb_build_object(
           'source_name', u.source_name,
           'norm_name',   u.norm_name,
           'master_name', c.concept_code,
           'similarity',  round(similarity(u.norm_name, c.concept_code)::numeric, 3)
       )                                     AS match_features
FROM unmatched u
JOIN concepts c ON c.concept_code %% u.norm_name
              AND similarity(u.norm_name, c.concept_code) >= %(threshold)s
ORDER BY 1, 2, 3
"""

UPSERT = """
INSERT INTO ops_governance.mdm_match_candidate
      (deployment_context, entity_class_code,
       left_source_system_code, left_source_record_key,
       right_source_system_code, right_source_record_key,
       suggested_master_bk, score, match_rule, match_features,
       matcher_run_uid, matcher_version)
VALUES (%(context)s, %(entity_class)s,
        %(left_sys)s, %(left_key)s,
        %(right_sys)s, %(master_bk)s,
        %(master_bk)s, %(score)s, %(match_rule)s, %(features)s::jsonb,
        %(run_uid)s, %(version)s)
ON CONFLICT (deployment_context, entity_class_code,
             left_source_system_code, left_source_record_key,
             right_source_system_code, right_source_record_key)
DO UPDATE SET
      suggested_master_bk = EXCLUDED.suggested_master_bk,
      score               = EXCLUDED.score,
      match_rule          = EXCLUDED.match_rule,
      match_features      = EXCLUDED.match_features,
      matcher_run_uid     = EXCLUDED.matcher_run_uid,
      matcher_version     = EXCLUDED.matcher_version,
      discovered_at       = now()
WHERE ops_governance.mdm_match_candidate.status = 'pending'
RETURNING candidate_id, (xmax = 0) AS inserted
"""


_ENTITY_SQL = {"facility": SQL_FACILITY_CANDIDATES,
               "commodity": SQL_COMMODITY_CANDIDATES}

# right-hand identity per entity class: facilities master onto a conformed
# hub entity; commodities master onto a vocabulary concept.
_RIGHT_SYS = {"facility": "NHDW.conformed", "commodity": "NHDW.vocabulary"}


def generate_candidates(vault_conn, threshold: float, *,
                        entity_class: str = "facility") -> list[dict]:
    """Run one entity class's match passes; returns candidate dicts (pure read)."""
    with vault_conn.cursor() as cur:
        cur.execute(_ENTITY_SQL[entity_class], {"threshold": threshold})
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def store_candidates(ops_conn, pairs: list[dict], *, context: str,
                     run_uid: str, entity_class: str = "facility",
                     sightings: list[dict] | None = None) -> dict[str, int]:
    """Upsert candidates; pending rows refresh, steward-decided rows are
    never touched (the status guard). Returns counts per match rule.
    With `sightings`, appends one entry per row inserted or refreshed."""
    by_rule: dict[str, int] = {}
    with ops_conn.cursor() as cur:
        for p in pairs:
            by_rule[p["match_rule"]] = by_rule.get(p["match_rule"], 0) + 1
            cur.execute(UPSERT, {
                "context": context, "run_uid": run_uid,
                "version": MATCHER_VERSION,
                "entity_class": entity_class,
                "right_sys": _RIGHT_SYS[entity_class],
                "left_sys": p["left_source_system_code"],
                "left_key": p["left_source_record_key"],
                "master_bk": p["master_bk"],
                "score": p["score"], "match_rule": p["match_rule"],
                "features": json.dumps(p["match_features"], default=str),
            })
            row = cur.fetchone()            # None = decided row, left untouched
            if row is not None and sightings is not None:
                sightings.append({"candidate_id": row[0], "score": p["score"],
                                  "match_rule": p["match_rule"],
                                  "outcome": "new" if row[1] else "refreshed"})
    ops_conn.commit()
    return by_rule


# Rules registry + run log (init 45)
# Everything below falls back to env thresholds and no run log when the tables are absent,
# so an image newer than its database never fails a run.

def _has_table(conn, name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
        return bool(cur.fetchone()[0])


def load_rules(ops_conn, context: str) -> dict[tuple[str, str], dict] | None:
    """Current registry rows keyed by (entity_class, rule_code); None when
    the registry table does not exist."""
    if not _has_table(ops_conn, "ops_governance.mdm_match_rule"):
        return None
    with ops_conn.cursor() as cur:
        cur.execute("""SELECT entity_class_code, rule_code, threshold, enabled, version
                       FROM ops_governance.mdm_match_rule
                       WHERE deployment_context = %s AND recorded_to IS NULL""", (context,))
        return {(r[0], r[1]): {"threshold": float(r[2]), "enabled": r[3], "version": r[4]}
                for r in cur.fetchall()}


def sql_threshold(rules: dict | None, entity_class: str, fallback: float) -> float:
    """The similarity floor the candidate SQL runs with: the registry's
    name_probabilistic threshold, else the env fallback."""
    rule = (rules or {}).get((entity_class, "name_probabilistic"))
    return rule["threshold"] if rule else fallback


def apply_rules(pairs: list[dict], rules: dict | None, entity_class: str) -> list[dict]:
    """Drop pairs whose rule is disabled or scores under its threshold.
    Rules the registry does not list pass through unchanged."""
    if not rules:
        return pairs
    kept = []
    for p in pairs:
        rule = rules.get((entity_class, p["match_rule"]))
        if rule is None or (rule["enabled"] and float(p["score"]) >= rule["threshold"]):
            kept.append(p)
    return kept


def start_run(ops_conn, run_uid: str, context: str, settings: dict) -> bool:
    """Open the run's log row; False (and no trail) when init 45 is absent."""
    if not _has_table(ops_conn, "ops_governance.mdm_matcher_run"):
        return False
    with ops_conn.cursor() as cur:
        cur.execute("""INSERT INTO ops_governance.mdm_matcher_run
                           (run_uid, deployment_context, matcher_version, settings)
                       VALUES (%s, %s, %s, %s::jsonb)""",
                    (run_uid, context, MATCHER_VERSION, json.dumps(settings)))
    ops_conn.commit()
    return True


def finish_run(ops_conn, run_uid: str, *, sightings: list[dict], counts: dict,
               total: int, error: str | None = None) -> None:
    with ops_conn.cursor() as cur:
        for s in sightings:
            cur.execute("""INSERT INTO ops_governance.mdm_candidate_sighting
                               (run_uid, candidate_id, score, match_rule, outcome)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT DO NOTHING""",
                        (run_uid, s["candidate_id"], s["score"], s["match_rule"], s["outcome"]))
        cur.execute("""UPDATE ops_governance.mdm_matcher_run
                          SET finished_at = now(), status = %s, counts = %s::jsonb,
                              candidates_total = %s, candidates_new = %s,
                              candidates_refreshed = %s, error = %s
                        WHERE run_uid = %s""",
                    ("failed" if error else "succeeded", json.dumps(counts), total,
                     sum(1 for s in sightings if s["outcome"] == "new"),
                     sum(1 for s in sightings if s["outcome"] == "refreshed"),
                     error, run_uid))
    ops_conn.commit()
# end rules registry + run log


def run() -> dict:
    threshold = float(os.environ.get("MDM_TRGM_THRESHOLD", "0.85"))
    context = os.environ.get("MDM_CONTEXT", "ET")
    run_uid = str(uuid.uuid4())

    by_entity: dict[str, dict[str, int]] = {}
    total = 0
    with psycopg.connect(os.environ["VAULT_DSN"]) as vault, \
         psycopg.connect(os.environ["OPS_DSN"]) as ops:
        # rules registry + run log
        rules = load_rules(ops, context)
        settings = {"source": "registry" if rules is not None else "env",
                    "env_threshold": threshold,
                    "rules": {f"{c}/{r}": v for (c, r), v in (rules or {}).items()}}
        tracing = start_run(ops, run_uid, context, settings)
        sightings: list[dict] = []
        try:
            for entity_class in ("facility", "commodity"):
                pairs = generate_candidates(
                    vault, sql_threshold(rules, entity_class, threshold),
                    entity_class=entity_class)
                pairs = apply_rules(pairs, rules, entity_class)
                seen: list[dict] = []       # kept only once this class commits
                by_entity[entity_class] = store_candidates(
                    ops, pairs, context=context, run_uid=run_uid,
                    entity_class=entity_class, sightings=seen)
                sightings += seen
                total += len(pairs)
        except Exception as exc:
            if tracing:
                ops.rollback()
                finish_run(ops, run_uid, sightings=sightings, counts=by_entity,
                           total=total, error=f"{type(exc).__name__}: {exc}"[:2000])
            raise
        if tracing:
            finish_run(ops, run_uid, sightings=sightings, counts=by_entity, total=total)
        # end

    summary = {"run_uid": run_uid, "context": context, "threshold": threshold,
               "rules_source": settings["source"],
               "candidates": total, "by_entity": by_entity,
               "at": datetime.now(timezone.utc).isoformat()}
    print(json.dumps(summary))
    return summary


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
