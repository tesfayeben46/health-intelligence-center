"""MDM workspace: the read side of the stewardship console.

Every route is read-only and answers the questions a steward asks before and
after a verdict, from the same objects the vault build produces:

  overview        the master-data lists that have a stewardship lane, with
                  masters / source records / waiting suggestions / releases
  facilities      the governed facility list (dim_facility + golden record +
                  resolution), searchable and paged, with a registry status
  compare         one suggestion's source records side by side, field by
                  field, from each source's current satellite
  history         one master's timeline across the five lanes: sources,
                  matching, stewardship, master record, releases
  setup           the sources feeding the facility list (records, last
                  received) and the matcher's current rules (init script 45)

                  Writes are in mdm.py (releases) and mdm_approvals.py (proposals). Readers are
                  stewards (mdm.py's direct gate; these reads have no OPA Rego rule) and
                  approvers, who judge a proposal from the same comparison and record. Warehouse
                  reads go through Trino as bi_reader (catalog `marts`), ops reads through the
                  asyncpg pool; neither is written.
                  """
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Query

from ..auth import UserCtx, get_user
from .. import ops_db, trino_client
from .mdm import _cand_out, _steward_gate
from .mdm_approvals import is_approver

log = logging.getLogger("gateway.mdm_workspace")
router = APIRouter(prefix="/api/mdm", tags=["mdm"])

BV = "marts.nhdw_vault_business"

# The display crosswalk: which column of each source's current satellite
# carries a comparable field. Mirrors the source branches of
# v_facility_resolution (same source_system codes, same hub hash keys). A
# field a source does not carry is "not reported"; a carried field that is
# empty is "missing". Coordinates are a (latitude, longitude) pair.
FIELDS = (
    ("name", "Preferred name"),
    ("alternate_name", "Alternate name"),
    ("code", "Source code"),
    ("facility_type", "Facility type"),
    ("operational_status", "Operational status"),
    ("ownership", "Ownership"),
    ("part_of", "Part of"),
    ("zone", "Zone"),
    ("woreda", "Woreda"),
    ("coordinates", "Coordinates"),
    ("phone", "Phone"),
)
SOURCES: dict[str, tuple[str, str, dict[str, str | tuple[str, str]]]] = {
    "DHIS2": ("v_sat_dhis2_org_unit_current", "hk_dhis2_org_unit",
              {"name": "name", "code": "code"}),
    "HCMIS": ("v_sat_hcmis_facility_current", "hk_hcmis_facility",
              {"name": "name"}),
    "mBrana.dbo": ("v_sat_mbrana_facility_current", "hk_mbrana_facility",
                   {"name": "name", "alternate_name": "alternate_name",
                    "facility_type": "ru_type",
                    "operational_status": "operational_status",
                    "ownership": "ownership", "zone": "zone", "woreda": "woreda",
                    "coordinates": ("latitude", "longitude"), "phone": "phone"}),
    "mBrana.ussd": ("v_sat_mbrana_ussd_facility_current", "hk_mbrana_ussd_facility",
                    {"name": "institution_name"}),
    "MFR.location": ("v_sat_mfr_facility_current", "hk_mfr_facility",
                     {"name": "name", "code": "identifier",
                      "facility_type": "facility_type_display",
                      "operational_status": "operational_status",
                      "part_of": "part_of_display",
                      "coordinates": ("latitude", "longitude"), "phone": "telecom"}),
    "MFR.organization": ("v_sat_mfr_organization_current", "hk_mfr_organization",
                         {"name": "name", "alternate_name": "alias", "code": "identifier",
                          "facility_type": "type_display", "part_of": "part_of_display",
                          "phone": "telecom"}),
}
MAX_COMPARE_RECORDS = 5

# Words the console uses for the matcher's vocabulary (same as the Review tab).
RULE_WORDS = {"name_exact": "Exact name", "name_ambiguous": "Ambiguous name",
              "name_probabilistic": "Probabilistic"}
MATCH_WORDS = {"facility_id": "Matched on facility ID", "norm_name": "Matched on name",
               "steward_decision": "Matched by a steward decision"}
STATUS_WORDS = {"pending": "Waiting for review", "decided": "Decided",
                "superseded": "Closed by another decision", "auto_accepted": "Accepted"}


async def _tq(sql: str, params: list | None = None) -> list[dict]:
    """A warehouse read; a failure is a 502 (the vault is unreachable), never
    an empty answer the console would show as 'nothing there'."""
    try:
        return (await trino_client.query(sql, params)).get("rows", []) or []
    except Exception as exc:                                          # noqa: BLE001
        log.warning("mdm workspace warehouse read failed: %s", exc)
        raise HTTPException(502, "could not read the warehouse")


async def _require_reader(user: UserCtx) -> None:
    if _steward_gate(user).allow or await is_approver(user):
        return
    raise HTTPException(403, "stewards and approvers only")


def _iso(v) -> str | None:
    return v.isoformat() if v is not None and hasattr(v, "isoformat") else (str(v) if v is not None else None)


def _num(v):
    return float(v) if isinstance(v, Decimal) else v


# Overview
@router.get("/overview")
async def overview(user: UserCtx = Depends(get_user)) -> dict:
    """The lists with a stewardship lane today (facility, commodity) and where
    each stands: masters, source records by source, suggestions waiting,
    current decisions and the latest published release."""
    await _require_reader(user)
    fac = (await _tq(f"""
        SELECT count(*) AS masters,
               count_if(mdm_state = 'conformed') AS conformed,
               count_if(mdm_state = 'single_source') AS single_source
        FROM {BV}.dim_facility"""))[0]
    fac_src = await _tq(f"""
        SELECT source_system, count(*) AS n
        FROM {BV}.v_facility_resolution GROUP BY source_system ORDER BY source_system""")
    com = (await _tq(f"SELECT count(*) AS masters FROM {BV}.hub_commodity_conformed"))[0]

    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        waiting = {r["entity_class_code"]: r["n"] for r in await conn.fetch("""
            SELECT entity_class_code, count(*) AS n FROM ops_governance.mdm_match_candidate
            WHERE status = 'pending' GROUP BY entity_class_code""")}
        decided = {r["entity_class_code"]: r["n"] for r in await conn.fetch("""
            SELECT entity_class_code, count(*) AS n FROM ops_governance.mdm_match_decision
            WHERE recorded_to IS NULL GROUP BY entity_class_code""")}
        releases = {r["entity_class_code"]: r for r in await conn.fetch("""
            SELECT DISTINCT ON (r.entity_class_code) r.entity_class_code, r.release_id,
                   r.published_at, s.stat_value AS masters
            FROM ops_governance.mdm_release r
            LEFT JOIN ops_governance.mdm_release_stat s
                   ON s.release_id = r.release_id AND s.stat_key = 'masters'
            WHERE r.status = 'published'
            ORDER BY r.entity_class_code, r.published_at DESC""")}

    def last_release(ec: str) -> dict | None:
        r = releases.get(ec)
        return None if r is None else {
            "release_id": r["release_id"], "published_at": _iso(r["published_at"]),
            "masters": int(r["masters"]) if r["masters"] is not None else None}

    return {"lists": [
        {"entity_class": "facility", "title": "Facilities",
         "masters": int(fac["masters"]), "conformed": int(fac["conformed"]),
         "single_source": int(fac["single_source"]),
         "source_records": sum(int(r["n"]) for r in fac_src),
         "sources": [{"source_system": r["source_system"], "n": int(r["n"])} for r in fac_src],
         "waiting": int(waiting.get("facility", 0)),
         "decisions": int(decided.get("facility", 0)),
         "last_release": last_release("facility")},
        {"entity_class": "commodity", "title": "Commodities",
         "masters": int(com["masters"]), "conformed": None, "single_source": None,
         "source_records": None, "sources": [],
         "waiting": int(waiting.get("commodity", 0)),
         "decisions": int(decided.get("commodity", 0)),
         "last_release": last_release("commodity")},
    ]}


# Facilities
@router.get("/facilities")
async def facilities(
    user: UserCtx = Depends(get_user),
    q: str = Query(default="", max_length=120),
    registry: str = Query(default="all"),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """The governed facility list, one row per master.

    registry: linked (more than one source agrees), single_source,
    possible_match (a suggestion waits to attach another record to this
    master), or all.
    """
    await _require_reader(user)
    if registry not in ("all", "linked", "single_source", "possible_match"):
        raise HTTPException(422, "registry must be all, linked, single_source or possible_match")

    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        pending = [r["suggested_master_bk"] for r in await conn.fetch("""
            SELECT DISTINCT suggested_master_bk FROM ops_governance.mdm_match_candidate
            WHERE entity_class_code = 'facility' AND status = 'pending'
              AND suggested_master_bk IS NOT NULL""")]

    where, params = ["(? = '' OR lower(d.facility_name) LIKE ?)"], [q.strip(), f"%{q.strip().lower()}%"]
    not_pending = (f"d.facility_id NOT IN ({', '.join('?' for _ in pending)})" if pending else "TRUE")
    if registry == "linked":
        where += ["d.mdm_state = 'conformed'", not_pending]
        params += pending
    elif registry == "single_source":
        where += ["d.mdm_state = 'single_source'", not_pending]
        params += pending
    elif registry == "possible_match":
        if not pending:
            return {"facilities": [], "total": 0, "limit": limit, "offset": offset,
                    "summary": await _facility_summary(pending)}
        where.append(f"d.facility_id IN ({', '.join('?' for _ in pending)})")
        params += pending

    rows = await _tq(f"""
        WITH canon AS (
            SELECT hk_facility_conformed, operational_status, woreda_name, zone_name,
                   row_number() OVER (PARTITION BY hk_facility_conformed
                                      ORDER BY load_ts DESC) AS rn
            FROM {BV}.sat_facility_canonical),
        src AS (
            SELECT hk_facility_conformed,
                   array_join(array_sort(array_agg(DISTINCT source_system)), ', ') AS sources
            FROM {BV}.v_facility_resolution GROUP BY hk_facility_conformed)
        SELECT d.facility_id AS master_bk, trim(d.facility_name) AS facility_name, d.mdm_state,
               d.n_source_systems, c.operational_status, c.woreda_name, c.zone_name,
               s.sources, count(*) OVER () AS total
        FROM {BV}.dim_facility d
        LEFT JOIN canon c ON c.hk_facility_conformed = d.hk_facility_conformed AND c.rn = 1
        LEFT JOIN src s ON s.hk_facility_conformed = d.hk_facility_conformed
        WHERE {' AND '.join(where)}
        ORDER BY lower(trim(d.facility_name)), d.facility_id
        OFFSET {offset} LIMIT {limit}""", params)

    pend = set(pending)
    out = [{
        "master_bk": r["master_bk"], "name": r["facility_name"],
        "registry": ("possible_match" if r["master_bk"] in pend
                     else "linked" if r["mdm_state"] == "conformed" else "single_source"),
        "n_source_systems": int(r["n_source_systems"] or 0),
        "sources": r["sources"], "operational_status": r["operational_status"],
        "woreda": r["woreda_name"], "zone": r["zone_name"],
    } for r in rows]
    return {"facilities": out, "total": int(rows[0]["total"]) if rows else 0,
            "limit": limit, "offset": offset,
            "summary": await _facility_summary(pending)}


async def _facility_summary(pending: list[str]) -> dict:
    """Counts with the same precedence as the rows: a master with a waiting
    suggestion is a possible match whatever its mdm_state, so the three counts
    always add up to the number of masters."""
    pend = (f"facility_id IN ({', '.join('?' for _ in pending)})" if pending else "FALSE")
    s = (await _tq(f"""
        SELECT count_if({pend}) AS possible_match,
               count_if(NOT ({pend}) AND mdm_state = 'conformed') AS linked,
               count_if(NOT ({pend}) AND mdm_state = 'single_source') AS single_source
        FROM {BV}.dim_facility""", pending * 3))[0]
    return {"linked": int(s["linked"]), "single_source": int(s["single_source"]),
            "possible_match": int(s["possible_match"])}


# Compare
async def _source_attrs(records: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """Current satellite attributes for (source_system, hex source key) pairs,
    read per source view. Unknown source systems come back empty."""
    by_src: dict[str, list[str]] = {}
    for sysc, key in records:
        if sysc in SOURCES:
            by_src.setdefault(sysc, []).append(key)
    found: dict[tuple[str, str], dict] = {}
    for sysc, keys in by_src.items():
        view, hk, cols = SOURCES[sysc]
        names = sorted({c for v in cols.values()
                        for c in ((v,) if isinstance(v, str) else v)})
        rows = await _tq(f"""
            SELECT lower(to_hex({hk})) AS k, {', '.join(names)}
            FROM {BV}.{view}
            WHERE {hk} IN ({', '.join('from_hex(?)' for _ in keys)})""", keys)
        for r in rows:
            found[(sysc, r["k"])] = r
    return found


def _identifiers(v: str) -> str:
    """MFR keeps identifiers as a FHIR list ([{"system": ..., "value": ...}]);
    other sources keep the bare code. Compare the values, not the JSON."""
    s = v.strip()
    if not s.startswith(("[", "{")):
        return s
    try:
        parsed = json.loads(s)
    except ValueError:
        return s
    items = parsed if isinstance(parsed, list) else [parsed]
    vals = [str(i.get("value")) for i in items if isinstance(i, dict) and i.get("value")]
    return ", ".join(vals) if vals else s


def _field_value(sysc: str, row: dict | None, field: str) -> tuple[str, str | None]:
    """(state, value): not_reported when the source does not carry the field,
    missing when it does but the record has no value."""
    col = SOURCES.get(sysc, ("", "", {}))[2].get(field)
    if col is None:
        return "not_reported", None
    if row is None:
        return "missing", None
    if isinstance(col, tuple):
        lat, lng = row.get(col[0]), row.get(col[1])
        if lat in (None, "") or lng in (None, ""):
            return "missing", None
        try:
            return "value", f"{float(lat):.6f}, {float(lng):.6f}"
        except (TypeError, ValueError):
            return "value", f"{lat}, {lng}"
    v = row.get(col)
    if v is None or (isinstance(v, str) and not v.strip()):
        return "missing", None
    text = str(_num(v)).strip()
    return "value", _identifiers(text) if field == "code" else text


def _norm(v: str) -> str:
    return " ".join(v.lower().split())


@router.get("/compare/{candidate_id}")
async def compare(candidate_id: str = PathParam(...),
                  user: UserCtx = Depends(get_user)) -> dict:
    """One suggestion, field by field: the suggested record next to the source
    records already resolved to the suggested master.

    Per field the result is difference (two sources report different values),
    missing (a source that carries the field left it empty), not_reported (a
    source does not carry the field at all) or agreement. Values are the
    sources' own, read from their current satellites, never rewritten."""
    await _require_reader(user)
    try:
        UUID(candidate_id)
    except (ValueError, TypeError):
        raise HTTPException(404, "not found")
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        cand = await conn.fetchrow("""
            SELECT * FROM ops_governance.mdm_match_candidate WHERE candidate_id = $1::uuid
        """, candidate_id)
    if cand is None:
        raise HTTPException(404, "not found")
    if cand["entity_class_code"] != "facility":
        raise HTTPException(422, "field comparison is available for facilities today")

    left = (cand["left_source_system_code"], cand["left_source_record_key"].lower())
    members = []
    if cand["suggested_master_bk"]:
        members = await _tq(f"""
            SELECT source_system, lower(to_hex(hk_source_facility)) AS k,
                   source_name, match_strategy
            FROM {BV}.v_facility_resolution
            WHERE conformed_bk = ?
            ORDER BY source_system, source_name""", [cand["suggested_master_bk"]])
    member_keys = [(m["source_system"], m["k"]) for m in members if (m["source_system"], m["k"]) != left]
    records = [left] + member_keys[:MAX_COMPARE_RECORDS - 1]
    attrs = await _source_attrs(records)
    strategy = {(m["source_system"], m["k"]): m["match_strategy"] for m in members}

    rec_out = [{
        "role": "suggested" if i == 0 else "master_member",
        "source_system": s, "record_key": k,
        "name": (_field_value(s, attrs.get((s, k)), "name")[1]),
        "match_strategy": strategy.get((s, k)),
        "found": (s, k) in attrs,
    } for i, (s, k) in enumerate(records)]

    fields, summary = [], {"agreement": [], "difference": [], "missing": [], "not_reported": []}
    for key, label in FIELDS:
        cells = [dict(zip(("state", "value"), _field_value(s, attrs.get((s, k)), key)))
                 for s, k in records]
        values = [c["value"] for c in cells if c["state"] == "value"]
        if not values:
            continue                      # no record carries a value: nothing to compare
        if len({_norm(v) for v in values}) > 1:
            result = "difference"
        elif any(c["state"] == "missing" for c in cells):
            result = "missing"
        elif any(c["state"] == "not_reported" for c in cells):
            result = "not_reported"
        else:
            result = "agreement"
        summary[result].append(label)
        fields.append({"field": key, "label": label, "cells": cells, "result": result})

    return {"candidate": _cand_out(cand) | {"matcher_version": cand["matcher_version"]},
            "master_bk": cand["suggested_master_bk"],
            "records": rec_out, "fields": fields, "summary": summary,
            "more_members": max(0, len(member_keys) - (MAX_COMPARE_RECORDS - 1))}


# History
@router.get("/master/{entity_class}/{master_bk}/history")
async def history(entity_class: str = PathParam(...),
                  master_bk: str = PathParam(...),
                  user: UserCtx = Depends(get_user)) -> dict:
    """One master's timeline across five lanes, oldest first:
    sources (each member record first seen), matching (suggestions found),
    stewardship (verdicts recorded and superseded), master record (golden
    revisions) and releases (published cuts that include it).

    published_state says whether the latest release already carries the
    master as it stands now (first seen before the release watermark and no
    verdict recorded after the release's decision cut)."""
    await _require_reader(user)
    if entity_class != "facility":
        raise HTTPException(422, "history is available for facilities today")

    hub = await _tq(f"""
        SELECT lower(to_hex(hk_facility_conformed)) AS hk, load_ts
        FROM {BV}.hub_facility_conformed WHERE conformed_bk = ?""", [master_bk])
    if not hub:
        raise HTTPException(404, "not found")
    hk = hub[0]["hk"]
    members = await _tq(f"""
        SELECT r.source_system, r.source_name, r.match_strategy,
               lower(to_hex(r.hk_source_facility)) AS k,
               l.load_ts AS first_seen, r.load_ts AS last_changed
        FROM {BV}.v_facility_resolution r
        LEFT JOIN {BV}.lnk_same_as_facility l ON l.hk_same_as_facility = r.hk_same_as_facility
        WHERE r.hk_facility_conformed = from_hex(?)
        ORDER BY first_seen, r.source_system""", [hk])
    revisions = await _tq(f"""
        SELECT canonical_name, name_source, operational_status, coord_source,
               admin_source, hashdiff, load_ts
        FROM {BV}.sat_facility_canonical
        WHERE hk_facility_conformed = from_hex(?)
        ORDER BY load_ts""", [hk])

    member_keys = [(m["source_system"], m["k"]) for m in members]
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        cands = await conn.fetch("""
            SELECT candidate_id, left_source_system_code, left_source_record_key,
                   match_rule, score, status, matcher_run_uid, discovered_at
            FROM ops_governance.mdm_match_candidate
            WHERE entity_class_code = $1 AND suggested_master_bk = $2
            ORDER BY discovered_at""", entity_class, master_bk)
        decs = await conn.fetch("""
            SELECT decision_id, source_system_code, source_record_key, decision,
                   master_bk, decided_by, note, recorded_from, recorded_to
            FROM ops_governance.mdm_match_decision
            WHERE entity_class_code = $1
              AND (master_bk = $2 OR (source_system_code, lower(source_record_key))
                   IN (SELECT * FROM unnest($3::text[], $4::text[])))
            ORDER BY recorded_from""", entity_class, master_bk,
            [s for s, _ in member_keys], [k for _, k in member_keys])
        rels = await conn.fetch("""
            SELECT release_id, decisions_as_of, source_watermark, published_at
            FROM ops_governance.mdm_release
            WHERE entity_class_code = $1 AND status = 'published'
            ORDER BY published_at""", entity_class)

    first_seen = hub[0]["load_ts"]
    events = []
    for m in members:
        events.append({"at": _iso(m["first_seen"]), "lane": "sources", "kind": "source_record",
                       "title": f"{m['source_system']} sent {m['source_name']}",
                       "detail": MATCH_WORDS.get(m["match_strategy"], m["match_strategy"])})
    for c in cands:
        events.append({"at": _iso(c["discovered_at"]), "lane": "matching", "kind": "suggestion",
                       "title": f"Suggested a {c['left_source_system_code']} record",
                       "detail": f"{RULE_WORDS.get(c['match_rule'], c['match_rule'])}, "
                                 f"{float(c['score']):.2f}, {STATUS_WORDS.get(c['status'], c['status'])}"})
    for d in decs:
        verdict = {"accept": "same facility", "reject": "different facilities",
                   "merge": "merged"}.get(d["decision"], d["decision"])
        events.append({"at": _iso(d["recorded_from"]), "lane": "stewardship", "kind": "decision",
                       "title": f"{d['decided_by']} decided: {verdict}",
                       "detail": f"{d['source_system_code']} record"
                                 + (f", {d['note']}" if d["note"] else "")})
        if d["recorded_to"] is not None:
            events.append({"at": _iso(d["recorded_to"]), "lane": "stewardship",
                           "kind": "decision_superseded",
                           "title": f"Decision by {d['decided_by']} superseded",
                           "detail": f"{d['source_system_code']} record"})
    for i, r in enumerate(revisions):
        events.append({"at": _iso(r["load_ts"]), "lane": "master", "kind": "revision",
                       "title": f"Golden record revision {i + 1}: {r['canonical_name']}",
                       "detail": f"name from {r['name_source']}"})
    in_rel = [r for r in rels if r["source_watermark"] >= first_seen]
    for r in in_rel:
        events.append({"at": _iso(r["published_at"]), "lane": "releases", "kind": "release",
                       "title": f"Release {r['release_id']} published", "detail": None})
    events.sort(key=lambda e: e["at"] or "")

    latest = rels[-1] if rels else None
    last_decision = max((d["recorded_from"] for d in decs), default=None)
    published = bool(latest and latest["source_watermark"] >= first_seen
                     and (last_decision is None or last_decision <= latest["decisions_as_of"]))
    return {
        "master_bk": master_bk, "entity_class": entity_class,
        "first_seen": _iso(first_seen),
        "published_state": "published" if published else "not_published",
        "latest_release": latest["release_id"] if latest else None,
        "revisions": [{"canonical_name": r["canonical_name"], "name_source": r["name_source"],
                       "operational_status": r["operational_status"],
                       "load_ts": _iso(r["load_ts"])} for r in revisions],
        "members": [{"source_system": m["source_system"], "source_name": m["source_name"],
                     "record_key": m["k"], "match_strategy": m["match_strategy"],
                     "first_seen": _iso(m["first_seen"]),
                     "last_changed": _iso(m["last_changed"])} for m in members],
        "events": events,
    }


# Setup: a narrow steward-readable route with only what the Setup screen shows,
# not the broad ops console. Read-only: rule changes are new registry versions
# written by a reviewed change, never from here.
QUIET_AFTER_DAYS = 7
LIST_WORDS = {"facility": "Facilities", "commodity": "Products"}
VERSION_PREFIX = {"facility": "facility", "commodity": "product"}


@router.get("/setup")
async def setup(user: UserCtx = Depends(get_user)) -> dict:
    """Sources feeding the facility list, and the matcher's current rules."""
    await _require_reader(user)
    src = await _tq(f"""
        SELECT source_system, count(*) AS records, max(load_ts) AS last_received
        FROM {BV}.v_facility_resolution GROUP BY source_system ORDER BY source_system""")
    now = datetime.now(timezone.utc)

    def status(last) -> str:
        if last is None:
            return "No data received"
        if isinstance(last, str):
            last = datetime.fromisoformat(last.replace(" ", "T"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        days = (now - last).days
        return "Receiving" if days < QUIET_AFTER_DAYS else f"No new data in {days} days"

    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        try:
            rules = await conn.fetch("""
                SELECT entity_class_code, rule_code, label, description, threshold,
                       enabled, version, recorded_from
                FROM ops_governance.mdm_match_rule
                WHERE deployment_context = 'ET' AND recorded_to IS NULL
                ORDER BY entity_class_code DESC, threshold DESC, rule_code DESC""")
            registry = True
        except (asyncpg.UndefinedTableError, asyncpg.InvalidSchemaNameError):
            rules, registry = [], False          # init 45 not applied yet

    return {
        "sources": [{"source_system": r["source_system"], "lists": [LIST_WORDS["facility"]],
                     "records": int(r["records"]), "last_received": _iso(r["last_received"]),
                     "status": status(r["last_received"])} for r in src],
        "registry": registry,
        "rules": [{"entity_class": r["entity_class_code"],
                   "list": LIST_WORDS.get(r["entity_class_code"], r["entity_class_code"]),
                   "rule_code": r["rule_code"], "label": r["label"],
                   "description": r["description"], "threshold": _num(r["threshold"]),
                   "enabled": r["enabled"],
                   "version": f"{VERSION_PREFIX.get(r['entity_class_code'], r['entity_class_code'])}-"
                              f"{r['rule_code'].replace('_', '-')}-v{r['version']}",
                   "updated": _iso(r["recorded_from"])} for r in rules],
    }
