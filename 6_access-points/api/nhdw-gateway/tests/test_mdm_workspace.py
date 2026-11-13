#!/usr/bin/env python3
"""End-to-end tests for the MDM workspace read routes (app/routes/mdm_workspace.py).

Runs the real routes against a real ops Postgres (init 35/39 + candidates) and
a real Trino whose `marts` catalog serves a copy of the business vault objects
the routes read. It inserts fixtures into both, so it refuses to run unless
both databases are named *_test. Never point it at live databases.

  OPS_PG_HOST=... OPS_PG_PORT=... OPS_PG_DATABASE=nhdw_ops_s0_test OPS_PG_USER=... \
  OPS_PG_PASSWORD=... TRINO_HOST=... TRINO_PORT=... \
  TEST_VAULT_DSN=postgresql://.../nhdw_vault_s0_test python3 tests/test_mdm_workspace.py

The contract under test:
  gate        stewards (R-STEW, R-OPS-ADM, R-SUPER) and approvers read; anyone
              else is refused
  overview    counts equal the warehouse and the ops store, per list
  facilities  one row per master; search, registry filter and paging agree
              with the warehouse; possible_match = a pending suggestion waits
  compare     source values come from each source's own satellite; a field a
              source does not carry is not_reported, an empty one is missing,
              disagreement is difference; non-facility suggestions are 422
  history     members, revisions and verdicts land on the right lanes in time
              order; published_state follows the latest release's two pins
  exposure    coordinates and phone numbers appear only in compare (stewards
              only, a deliberate exception); overview, facilities and history carry
              neither the fields nor the values
  read-only   no route changes a row in the ops store
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

VAULT_DSN = os.environ.get("TEST_VAULT_DSN", "")
if not os.environ.get("OPS_PG_DATABASE", "").endswith("_test") or not VAULT_DSN.endswith("_test"):
    sys.exit("refusing to run: OPS_PG_DATABASE and TEST_VAULT_DSN must name *_test databases")

import asyncpg                                  # noqa: E402
import httpx                                    # noqa: E402
from fastapi import FastAPI                     # noqa: E402

from app import ops_db                          # noqa: E402
from app.auth import UserCtx, get_user          # noqa: E402
from app.routes import mdm_workspace            # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILS.append(label)


def user(name: str, *roles: str) -> UserCtx:
    return UserCtx(kc_sub=str(uuid.uuid4()), username=name, realm="privileged-ops",
                   primary_role=roles[0] if roles else "", roles=list(roles))


STEWARD = user("steward.one", "R-STEW")
OPSADM = user("ops.admin", "R-OPS-ADM")
APPROVER = user("approver.one", "R-STEW-APR")
ANALYST = user("analyst.one", "R-CONS-ANA")

_current = {"u": STEWARD}
app = FastAPI()
app.include_router(mdm_workspace.router)
app.dependency_overrides[get_user] = lambda: _current["u"]

BV = "nhdw_vault_business"
# a fixture mBrana record: its hub hash is derived from a test-only key
MB_KEY = hashlib.sha256(b"TEST-MBRANA-WORKSPACE-1").hexdigest()
TEST_RUN = "00000000-0000-4000-8000-0000000000aa"   # marks fixture suggestions


async def main() -> None:
    vault = await asyncpg.connect(VAULT_DSN)
    pool = await ops_db.ensure_pool()

    # a master that several sources already resolve to, and one of its names
    master = await vault.fetchrow(f"""
        SELECT r.conformed_bk, min(r.source_name) AS name, count(*) AS n
        FROM {BV}.v_facility_resolution r
        JOIN {BV}.dim_facility d ON d.facility_id = r.conformed_bk
        WHERE d.mdm_state = 'conformed'
        GROUP BY r.conformed_bk HAVING count(DISTINCT r.source_system) >= 2
        ORDER BY r.conformed_bk LIMIT 1""")
    check(master is not None, "fixture: a conformed master with two or more sources exists")
    bk, master_name = master["conformed_bk"], master["name"]

    # fixture: an mBrana record spelling the name differently, with coordinates
    await vault.execute(f"""
        INSERT INTO {BV}.v_sat_mbrana_facility_current
            (hk_mbrana_facility, name, phone, woreda, zone, ru_type, ownership,
             operational_status, latitude, longitude, alternate_name, load_ts, record_source)
        VALUES (decode($1, 'hex'), $2, '', 'W-17', 'Z-3', 'HP', 'GOV', 'Functional',
                '9.0301', '38.7468', NULL, now(), 'TEST')""", MB_KEY, master_name.upper() + " HP")

    async with pool.acquire() as conn:
        cand_id = await conn.fetchval("""
            INSERT INTO ops_governance.mdm_match_candidate
                (deployment_context, entity_class_code, left_source_system_code,
                 left_source_record_key, right_source_system_code, right_source_record_key,
                 suggested_master_bk, score, match_rule, match_features, status,
                 matcher_run_uid, matcher_version)
            VALUES ('ET', 'facility', 'mBrana.dbo', $1, 'NHDW.conformed', $2, $2, 0.91,
                    'name_probabilistic', '{}'::jsonb, 'pending', $3::uuid, '0.1.0')
            RETURNING candidate_id""", MB_KEY, bk, TEST_RUN)
        com_id = await conn.fetchval("""
            INSERT INTO ops_governance.mdm_match_candidate
                (deployment_context, entity_class_code, left_source_system_code,
                 left_source_record_key, right_source_system_code, right_source_record_key,
                 suggested_master_bk, score, match_rule, match_features, status,
                 matcher_run_uid)
            VALUES ('ET', 'commodity', 'HCMIS', 'deadbeef', 'NHDW.vocabulary', 'X', 'X', 0.5,
                    'name_probabilistic', '{}'::jsonb, 'pending', $1::uuid)
            RETURNING candidate_id""", TEST_RUN)
        snap_before = await conn.fetchval("""
            SELECT md5(string_agg(t::text, '' ORDER BY t::text)) FROM (
              SELECT candidate_id::text, status FROM ops_governance.mdm_match_candidate
              UNION ALL SELECT decision_id::text, coalesce(recorded_to::text, 'cur')
              FROM ops_governance.mdm_match_decision
              UNION ALL SELECT release_id, status FROM ops_governance.mdm_release) t""")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as c:
        print("gate")
        _current["u"] = ANALYST
        for path in ("/api/mdm/overview", "/api/mdm/facilities",
                     f"/api/mdm/compare/{cand_id}", f"/api/mdm/master/facility/{bk}/history",
                     "/api/mdm/setup"):
            r = await c.get(path)
            check(r.status_code == 403, f"non-steward refused: {path.split('/')[3]}")
        _current["u"] = OPSADM
        check((await c.get("/api/mdm/overview")).status_code == 200, "ops admin allowed")
        _current["u"] = APPROVER
        check((await c.get("/api/mdm/overview")).status_code == 200
              and (await c.get(f"/api/mdm/compare/{cand_id}")).status_code == 200,
              "approver reads overview and the comparison it judges")
        _current["u"] = STEWARD

        print("overview")
        ov = (await c.get("/api/mdm/overview")).json()
        fac = next(x for x in ov["lists"] if x["entity_class"] == "facility")
        com = next(x for x in ov["lists"] if x["entity_class"] == "commodity")
        n_dim = await vault.fetchval(f"SELECT count(*) FROM {BV}.dim_facility")
        n_conf = await vault.fetchval(f"SELECT count(*) FROM {BV}.dim_facility WHERE mdm_state='conformed'")
        n_res = await vault.fetchval(f"SELECT count(*) FROM {BV}.v_facility_resolution")
        n_com = await vault.fetchval(f"SELECT count(*) FROM {BV}.hub_commodity_conformed")
        async with pool.acquire() as conn:
            n_wait = await conn.fetchval("""SELECT count(*) FROM ops_governance.mdm_match_candidate
                                            WHERE status='pending' AND entity_class_code='facility'""")
        check(fac["masters"] == n_dim, f"facility masters = dim_facility ({n_dim})")
        check(fac["conformed"] == n_conf, "conformed count matches")
        check(fac["source_records"] == n_res, f"source records = resolution rows ({n_res})")
        check(sum(s["n"] for s in fac["sources"]) == n_res, "per-source counts add up")
        check(fac["waiting"] == n_wait, f"waiting = pending candidates ({n_wait})")
        check(com["masters"] == n_com and com["waiting"] >= 1, "commodity masters and waiting")
        check(fac["last_release"] is None, "no release yet")

        print("setup")
        su = (await c.get("/api/mdm/setup")).json()
        by_src = {r["source_system"]: r for r in su["sources"]}
        check({s["source_system"]: s["n"] for s in fac["sources"]}
              == {k: v["records"] for k, v in by_src.items()},
              "setup sources = overview per-source counts")
        n_last = await vault.fetchval(f"""SELECT count(DISTINCT source_system)
                                          FROM {BV}.v_facility_resolution WHERE load_ts IS NOT NULL""")
        check(sum(1 for r in su["sources"] if r["last_received"]) == n_last,
              "last received comes from the vault load time")
        check(all(r["status"] == "Receiving" or r["status"].startswith(("No new data in ", "No data"))
                  for r in su["sources"]), "every source has a plain status")
        check(su["registry"] is True and len(su["rules"]) == 4, f"four seeded rules ({len(su['rules'])})")
        rmap = {(r["entity_class"], r["rule_code"]): r for r in su["rules"]}
        check(rmap[("facility", "name_exact")]["version"] == "facility-name-exact-v1"
              and rmap[("commodity", "name_probabilistic")]["version"] == "product-name-probabilistic-v1"
              and rmap[("facility", "name_exact")]["threshold"] == 1.0, "rule version and threshold")
        check(rmap[("facility", "name_probabilistic")]["threshold"] == 0.85
              and rmap[("commodity", "name_probabilistic")]["list"] == "Products",
              "similarity threshold 0.85; commodity rule listed under Products")
        check([r["rule_code"] for r in su["rules"][:3]] == ["name_exact", "name_ambiguous", "name_probabilistic"]
              and su["rules"][3]["entity_class"] == "commodity", "facility rules first, exact -> ambiguous -> probabilistic")

        print("facilities")
        page = max(1, min(10, n_dim // 2))            # two full pages at any data size
        r = (await c.get("/api/mdm/facilities", params={"limit": page})).json()
        check(r["total"] == n_dim and len(r["facilities"]) == page, f"all masters, paged by {page}")
        names = [f["name"] or "" for f in r["facilities"]]
        check(names == sorted(names, key=str.lower) and all(n == n.strip() for n in names),
              "ordered by name, case-insensitive, names trimmed")
        r2 = (await c.get("/api/mdm/facilities", params={"limit": page, "offset": page})).json()
        check(not {f["master_bk"] for f in r["facilities"]} & {f["master_bk"] for f in r2["facilities"]},
              "pages do not overlap")
        term = master_name.split()[0].lower()
        rs = (await c.get("/api/mdm/facilities", params={"q": term, "limit": 200})).json()
        n_q = await vault.fetchval(f"SELECT count(*) FROM {BV}.dim_facility WHERE lower(facility_name) LIKE $1",
                                   f"%{term}%")
        check(rs["total"] == n_q and all(term in (f["name"] or "").lower() for f in rs["facilities"]),
              f"search '{term}' matches the warehouse ({n_q})")
        async with pool.acquire() as conn:
            pend = [r[0] for r in await conn.fetch("""SELECT DISTINCT suggested_master_bk
                FROM ops_governance.mdm_match_candidate WHERE entity_class_code = 'facility'
                  AND status = 'pending' AND suggested_master_bk IS NOT NULL""")]
        n_pend = await vault.fetchval(f"SELECT count(*) FROM {BV}.dim_facility WHERE facility_id = ANY($1)", pend)
        n_link = await vault.fetchval(f"""SELECT count(*) FROM {BV}.dim_facility
            WHERE mdm_state = 'conformed' AND NOT (facility_id = ANY($1))""", pend)
        rl = (await c.get("/api/mdm/facilities", params={"registry": "linked", "limit": 200})).json()
        check(rl["total"] == n_link and rl["summary"]["linked"] == n_link,
              f"linked = conformed masters without a waiting suggestion ({n_link})")
        check(all(f["registry"] == "linked" for f in rl["facilities"]), "the linked filter only returns Linked rows")
        sm = rl["summary"]
        check(sm["linked"] + sm["single_source"] + sm["possible_match"] == n_dim
              and sm["possible_match"] == n_pend, "summary counts add up to the masters")
        rp = (await c.get("/api/mdm/facilities", params={"registry": "possible_match", "limit": 200})).json()
        check(any(f["master_bk"] == bk and f["registry"] == "possible_match" for f in rp["facilities"]),
              "the fixture's master shows as a possible match")
        check(rp["summary"]["possible_match"] >= 1, "summary counts possible matches")
        check((await c.get("/api/mdm/facilities", params={"registry": "bogus"})).status_code == 422,
              "unknown registry filter is 422")

        print("compare")
        check((await c.get("/api/mdm/compare/not-a-uuid")).status_code == 404, "bad id is 404")
        check((await c.get(f"/api/mdm/compare/{uuid.uuid4()}")).status_code == 404, "unknown id is 404")
        check((await c.get(f"/api/mdm/compare/{com_id}")).status_code == 422, "commodity is 422")
        cp = (await c.get(f"/api/mdm/compare/{cand_id}")).json()
        recs = cp["records"]
        check(recs[0]["role"] == "suggested" and recs[0]["source_system"] == "mBrana.dbo"
              and recs[0]["found"], "suggested record read from its own satellite")
        check(recs[0]["name"] == master_name.upper() + " HP", "source value shown as the source holds it")
        check(len(recs) == min(master["n"], mdm_workspace.MAX_COMPARE_RECORDS - 1) + 1
              and all(x["role"] == "master_member" and x["found"] for x in recs[1:]),
              "master's member records follow, all found")
        by = {f["field"]: f for f in cp["fields"]}
        check(by["name"]["result"] == "difference", "differently spelt name is a difference")
        check(by["coordinates"]["cells"][0]["state"] == "value"
              and by["coordinates"]["result"] in ("not_reported", "missing"),
              "coordinates only one source carries: not a false agreement")
        dh = [i for i, x in enumerate(recs) if x["source_system"] == "DHIS2"]
        if dh:
            check(by["coordinates"]["cells"][dh[0]]["state"] == "not_reported",
                  "DHIS2 does not carry coordinates: not_reported")
        check(by["phone"]["cells"][0]["state"] == "missing" if "phone" in by else True,
              "an empty phone is missing, not a value")
        check("Preferred name" in cp["summary"]["difference"], "summary lists the difference")
        check(cp["candidate"]["matcher_version"] == "0.1.0", "matcher version shown")
        if "code" in by:
            vals = [c["value"] for c in by["code"]["cells"] if c["state"] == "value"]
            check(all(not v.startswith(("[", "{")) for v in vals),
                  "identifier lists compared by their values, not raw JSON")

        print("history")
        check((await c.get("/api/mdm/master/facility/NO-SUCH-MASTER/history")).status_code == 404,
              "unknown master is 404")
        check((await c.get(f"/api/mdm/master/commodity/{bk}/history")).status_code == 422,
              "commodity history is 422")
        h = (await c.get(f"/api/mdm/master/facility/{bk}/history")).json()
        n_rev = await vault.fetchval(f"""
            SELECT count(*) FROM {BV}.sat_facility_canonical s
            JOIN {BV}.hub_facility_conformed hub USING (hk_facility_conformed)
            WHERE hub.conformed_bk = $1""", bk)
        check(len(h["members"]) == master["n"], f"members = resolution rows ({master['n']})")
        check(len(h["revisions"]) == n_rev, f"revisions = golden rows ({n_rev})")
        lanes = {e["lane"] for e in h["events"]}
        check({"sources", "matching", "master"} <= lanes, "sources, matching and master lanes present")
        check(not any("run " in (e["detail"] or "") or "_" in (e["detail"] or "")
                      for e in h["events"]), "timeline details have no run ids or raw codes")
        ats = [e["at"] for e in h["events"]]
        check(ats == sorted(ats), "events in time order")
        check(h["published_state"] == "not_published", "no release: not published")

        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO ops_governance.mdm_release
                    (release_id, entity_class_code, decisions_as_of, source_watermark,
                     status, created_by, published_at)
                VALUES ('test-rel-1', 'facility', now(), now(), 'published', 'test', now())""")
        h2 = (await c.get(f"/api/mdm/master/facility/{bk}/history")).json()
        check(h2["published_state"] == "published" and h2["latest_release"] == "test-rel-1",
              "a release pinned after the master: published")
        check(any(e["lane"] == "releases" for e in h2["events"]), "release lands on the releases lane")
        ov2 = (await c.get("/api/mdm/overview")).json()
        check(next(x for x in ov2["lists"] if x["entity_class"] == "facility")["last_release"]
              ["release_id"] == "test-rel-1", "overview shows the latest release")

        print("exposure")
        golden = await vault.fetch(f"""
            SELECT telecom, latitude::text AS lat, longitude::text AS lng
            FROM {BV}.sat_facility_canonical s
            JOIN {BV}.hub_facility_conformed hub USING (hk_facility_conformed)
            WHERE hub.conformed_bk = $1""", bk)
        secrets = {v for g in golden for v in (g["telecom"], g["lat"], g["lng"]) if v}
        bodies = {
            "overview": (await c.get("/api/mdm/overview")).text,
            "facilities": (await c.get("/api/mdm/facilities",
                                       params={"q": master_name, "limit": 200})).text,
            "history": (await c.get(f"/api/mdm/master/facility/{bk}/history")).text,
            "setup": (await c.get("/api/mdm/setup")).text,
        }
        for name, body in bodies.items():
            keys = set()
            def walk(o):
                if isinstance(o, dict):
                    keys.update(o.keys()); [walk(v) for v in o.values()]
                elif isinstance(o, list):
                    [walk(v) for v in o]
            walk(json.loads(body))
            check(not keys & {"latitude", "longitude", "telecom", "phone", "coordinates"},
                  f"{name}: no coordinate or phone fields")
            check(not any(v in body for v in secrets),
                  f"{name}: none of the master's golden coordinates or phone values")
        _current["u"] = ANALYST
        r = await c.get(f"/api/mdm/compare/{cand_id}")
        check(r.status_code == 403 and "Preferred name" not in r.text,
              "compare (the only route with coordinates) refuses a non-steward before any data")
        _current["u"] = STEWARD

        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM ops_governance.mdm_release WHERE release_id = 'test-rel-1'")
            snap_after = await conn.fetchval("""
                SELECT md5(string_agg(t::text, '' ORDER BY t::text)) FROM (
                  SELECT candidate_id::text, status FROM ops_governance.mdm_match_candidate
                  UNION ALL SELECT decision_id::text, coalesce(recorded_to::text, 'cur')
                  FROM ops_governance.mdm_match_decision
                  UNION ALL SELECT release_id, status FROM ops_governance.mdm_release) t""")
        print("read-only")
        check(snap_after == snap_before, "routes changed nothing in the ops store")

        print("other suggestions in the store")
        async with pool.acquire() as conn:
            live = await conn.fetch("""SELECT candidate_id FROM ops_governance.mdm_match_candidate
                                       WHERE entity_class_code = 'facility'
                                         AND matcher_run_uid IS DISTINCT FROM $1::uuid""", TEST_RUN)
        ok = 0
        for row in live:
            rr = await c.get(f"/api/mdm/compare/{row['candidate_id']}")
            ok += rr.status_code == 200 and rr.json()["records"][0]["found"]
        check(ok == len(live), f"every other suggestion compares with its record found ({ok}/{len(live)})")
        raw = 0
        for row in live:
            cj = (await c.get(f"/api/mdm/compare/{row['candidate_id']}")).json()
            code = next((f for f in cj["fields"] if f["field"] == "code"), None)
            raw += sum(1 for cell in (code["cells"] if code else [])
                       if cell["state"] == "value" and cell["value"].startswith(("[", "{")))
        check(raw == 0, "no other suggestion shows an identifier as raw JSON")

    await vault.close()
    print(f"\n{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
