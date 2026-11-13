#!/usr/bin/env python3
"""End-to-end tests for MDM maker-checker (app/routes/mdm_approvals.py).

Runs the real routes against a real Postgres that carries init 35, 39 and 44
plus some match candidates. It writes, so it refuses to run unless the
database name ends in "_test". Never point it at a live ops database.

  OPS_PG_HOST=... OPS_PG_PORT=... OPS_PG_DATABASE=nhdw_ops_test \
  OPS_PG_USER=ops_gateway OPS_PG_PASSWORD=... python3 tests/test_mdm_approvals.py

The contract under test:
  propose      checked at once; one open proposal per record; queue hides it
  four eyes    the proposer can never approve; compared on the person
               (Keycloak subject), so a second username of the same person
               (e.g. a service token) cannot approve either; DB enforces it too
  identity     an action without a personal kc_sub is refused
  roles        approver-only cannot propose; ops admin cannot approve
  apply        approval writes the decision exactly like the one-step verdict
               (supersede current, insert, close candidate + pending siblings)
  check        expired -> 409 until re-checked; moved records -> 409 stale
  decline      needs a reason; never writes a decision
  review       not sure keeps the suggestion (flagged); dismiss hides it;
               neither touches the decision log
  restore      undo is itself a proposal; approval supersedes + re-opens
  bulk         one result per item; partial failure is reported
  audit        every change leaves a policy_log row in the same transaction
  capability   a person granted R-STEW-APR in ops_perm (console grant, no
               token role) can approve; without the mdm_approve capability
               they cannot. Needs TEST_ADMIN_DSN (to write the grant).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if not os.environ.get("OPS_PG_DATABASE", "").endswith("_test"):
    sys.exit("refusing to run: OPS_PG_DATABASE must name a *_test database")

import asyncpg                                  # noqa: E402
import httpx                                    # noqa: E402
from fastapi import FastAPI                     # noqa: E402

from app import ops_db                          # noqa: E402
from app.auth import UserCtx, get_user          # noqa: E402
from app.routes import mdm_approvals            # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILS.append(label)


def user(name: str, *roles: str, sub: str | None = None) -> UserCtx:
    return UserCtx(kc_sub=sub or str(uuid.uuid4()), username=name, realm="privileged-ops",
                   primary_role=roles[0] if roles else "", roles=list(roles))


STEWARD = user("steward.one", "R-STEW")
STEWARD2 = user("steward.two", "R-STEW")
BOTH = user("steward.both", "R-STEW", "R-STEW-APR")     # holds both roles
BOTH_TOKEN = user("pat:abcd1234", "R-STEW-APR", sub=BOTH.kc_sub)   # same person, another username
APPROVER = user("approver.one", "R-STEW-APR")
APPROVER2 = user("approver.two", "R-STEW-APR")
OPSADM = user("ops.admin", "R-OPS-ADM")
CAPAPPROVER = user("cap.approver", "R-CONS-ANA")        # approver only through ops_perm
NOCAP = user("plain.user", "R-CONS-ANA")
ADMIN_DSN = os.environ.get("TEST_ADMIN_DSN", "")
NOSUB = UserCtx(kc_sub="", username="dev:mock", realm="privileged-ops",
                primary_role="R-STEW", roles=["R-STEW", "R-STEW-APR"])

_current = {"u": STEWARD}
app = FastAPI()
app.include_router(mdm_approvals.router)
app.dependency_overrides[get_user] = lambda: _current["u"]


async def call(client, who: UserCtx, method: str, path: str, **kw):
    _current["u"] = who
    r = await client.request(method, f"/api/mdm{path}", **kw)
    return r.status_code, (r.json() if r.content else None)


async def q(sql: str, *args):
    pool = await ops_db.ensure_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(sql, *args)


async def pending_candidates(n: int) -> list:
    """n pending candidates on DIFFERENT source records, with a master."""
    return await q("""
        SELECT DISTINCT ON (left_source_system_code, left_source_record_key) *
        FROM ops_governance.mdm_match_candidate
        WHERE status = 'pending' AND suggested_master_bk IS NOT NULL
        ORDER BY left_source_system_code, left_source_record_key, score DESC
        LIMIT $1""", n)


async def main() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        cands = await pending_candidates(6)
        if len(cands) < 6:
            sys.exit(f"need 6 pending candidates on distinct records, found {len(cands)}")
        c1, c2, c3, c4, c5, c6 = [str(r["candidate_id"]) for r in cands]
        # give c1's record a current decision so that superseding it is exercised
        await q("""INSERT INTO ops_governance.mdm_match_decision
                     (deployment_context, entity_class_code, source_system_code, source_record_key,
                      decision, decided_by, note)
                   VALUES ($1, $2, $3, $4, 'reject', 'seed', 'test: earlier decision')""",
                cands[0]["deployment_context"], cands[0]["entity_class_code"],
                cands[0]["left_source_system_code"], cands[0]["left_source_record_key"])
        dec_before = (await q("SELECT count(*) n FROM ops_governance.mdm_match_decision"))[0]["n"]
        audit_start = (await q("SELECT COALESCE(max(id), 0) m FROM ops_audit.policy_log"))[0]["m"]

        print("queue")
        s, b = await call(c, STEWARD, "GET", "/review-queue", params={"limit": 500})
        hc = [x for x in b["candidates"] if x["score"] >= b["high_confidence_score"]
              and x["suggested_master_bk"] and x["match_rule"] != "name_ambiguous"]
        check(s == 200 and b["high_confidence"] == len(hc),
              "high-confidence count leaves out ambiguous-name suggestions")

        print("identity")
        s, _ = await call(c, NOSUB, "POST", f"/candidate/{c1}/propose", json={"verdict": "same"})
        check(s == 403, "no personal kc_sub -> 403")

        print("propose")
        s, b = await call(c, STEWARD, "POST", f"/candidate/{c1}/propose", json={"verdict": "same"})
        check(s == 200 and b["status"] == "checked" and b["valid_until"], "same -> checked with a validity window")
        p1 = b["proposal_id"]
        s, _ = await call(c, STEWARD2, "POST", f"/candidate/{c1}/propose", json={"verdict": "different"})
        check(s == 409, "second open proposal on the same record -> 409")
        s, b = await call(c, STEWARD, "GET", "/review-queue")
        check(s == 200 and c1 not in [x["candidate_id"] for x in b["candidates"]], "queue hides a candidate under proposal")
        s, _ = await call(c, STEWARD, "POST", f"/candidate/{c1}/propose", json={"verdict": "maybe"})
        check(s in (409, 422), "unknown verdict refused")
        n = (await q("SELECT count(*) n FROM ops_governance.mdm_match_decision"))[0]["n"]
        check(n == dec_before, "proposing writes no decision")

        print("roles and four eyes")
        s, _ = await call(c, APPROVER, "POST", f"/candidate/{c2}/propose", json={"verdict": "same"})
        check(s == 403, "approver-only cannot propose")
        s, b = await call(c, BOTH, "POST", f"/candidate/{c2}/propose", json={"verdict": "same"})
        p2 = b["proposal_id"]
        s, _ = await call(c, BOTH, "POST", f"/proposal/{p2}/approve", json={})
        check(s == 403, "proposer holding the approver role still cannot approve")
        s, _ = await call(c, BOTH_TOKEN, "POST", f"/proposal/{p2}/approve", json={})
        check(s == 403, "same person under another username (token) cannot approve")
        s, _ = await call(c, BOTH_TOKEN, "POST", f"/proposal/{p2}/decline", json={"note": "x"})
        check(s == 403, "same person under another username cannot decline")
        try:
            await q("""UPDATE ops_governance.mdm_change_proposal
                          SET reviewed_by = 'x', reviewed_by_sub = proposed_by_sub
                        WHERE proposal_id = $1::uuid""", p2)
            check(False, "DB refuses reviewer = proposer (constraint)")
        except Exception as exc:                                  # noqa: BLE001
            check("mdm_proposal_four_eyes" in str(exc), "DB refuses reviewer = proposer (constraint)")
        s, b = await call(c, BOTH, "GET", f"/proposal/{p2}")
        check(s == 200 and b["can_approve"] is False, "detail tells the proposer they cannot approve")
        s, _ = await call(c, OPSADM, "POST", f"/proposal/{p1}/approve", json={})
        check(s == 403, "ops admin cannot approve")
        s, b = await call(c, APPROVER, "GET", "/proposals", params={"view": "approvals"})
        ids = [x["proposal_id"] for x in b["proposals"]]
        check(s == 200 and p1 in ids and p2 in ids, "approvals queue lists checked proposals of others")
        s, b = await call(c, BOTH, "GET", "/proposals", params={"view": "approvals"})
        check(s == 200 and p2 not in [x["proposal_id"] for x in b["proposals"]], "approvals queue never lists my own")

        print("approve = apply")
        cur_before = await q("""
            SELECT decision_id FROM ops_governance.mdm_match_decision
            WHERE source_system_code = $1 AND source_record_key = $2 AND recorded_to IS NULL
        """, cands[0]["left_source_system_code"], cands[0]["left_source_record_key"])
        s, b = await call(c, APPROVER, "POST", f"/proposal/{p1}/approve", json={"note": "agreed"})
        check(s == 200 and b["status"] == "applied" and b["decision_id"], "approve -> applied with a decision id")
        d = (await q("SELECT * FROM ops_governance.mdm_match_decision WHERE decision_id = $1::uuid", b["decision_id"]))[0]
        check(d["decision"] == "accept" and d["master_bk"] == cands[0]["suggested_master_bk"]
              and str(d["candidate_id"]) == c1 and d["recorded_to"] is None
              and d["decided_by"] == "steward.one", "decision row = accept -> suggested master, current, decided_by proposer")
        check(len(cur_before) == 1, "the record had a current decision before approval (seeded)")
        old = (await q("SELECT recorded_to FROM ops_governance.mdm_match_decision WHERE decision_id = $1",
                       cur_before[0]["decision_id"]))[0]
        check(old["recorded_to"] is not None, "earlier current decision superseded")
        check(float(d["score"]) == float(cands[0]["score"]), "decision score = the candidate's score")
        st = (await q("SELECT status FROM ops_governance.mdm_match_candidate WHERE candidate_id = $1::uuid", c1))[0]["status"]
        check(st == "decided", "candidate closed")
        sib = await q("""SELECT count(*) n FROM ops_governance.mdm_match_candidate
                         WHERE left_source_system_code = $1 AND left_source_record_key = $2
                           AND status = 'pending'""",
                      cands[0]["left_source_system_code"], cands[0]["left_source_record_key"])
        check(sib[0]["n"] == 0, "pending siblings of the record superseded")
        s, b = await call(c, STEWARD, "GET", f"/proposal/{p1}")
        check([e["event"] for e in b["events"]] == ["proposed", "checked", "approved", "applied"],
              "history: proposed -> checked -> approved -> applied")
        s, _ = await call(c, APPROVER, "POST", f"/proposal/{p1}/approve", json={})
        check(s == 409, "approving twice -> 409")

        print("expiry and stale checks")
        await q("UPDATE ops_governance.mdm_change_proposal SET valid_until = now() - interval '1 minute' WHERE proposal_id = $1::uuid", p2)
        s, b = await call(c, APPROVER, "POST", f"/proposal/{p2}/approve", json={})
        check(s == 409 and "expired" in str(b), "expired check -> 409 expired")
        s, b = await call(c, BOTH, "GET", "/proposals", params={"view": "attention"})
        check(p2 in [x["proposal_id"] for x in b["proposals"] if x["status"] == "expired"], "expired shows in the proposer's attention list")
        s, b = await call(c, BOTH, "POST", f"/proposal/{p2}/check")
        check(s == 200 and b["status"] == "checked", "re-check renews the window")
        await q("UPDATE ops_governance.mdm_match_candidate SET score = score - 0.01 WHERE candidate_id = $1::uuid", c2)
        s, b = await call(c, APPROVER, "POST", f"/proposal/{p2}/approve", json={})
        check(s == 409 and "stale" in str(b), "candidate moved after the check -> 409 stale")
        s, b = await call(c, BOTH, "POST", f"/proposal/{p2}/check")
        s, b = await call(c, APPROVER, "GET", f"/proposal/{p2}")
        newscore = (await q("SELECT score FROM ops_governance.mdm_match_candidate WHERE candidate_id = $1::uuid", c2))[0]["score"]
        check(b["evidence"]["score"] == float(newscore), "re-check refreshes the evidence the approver sees")
        s, b = await call(c, APPROVER, "POST", f"/proposal/{p2}/approve", json={})
        check(s == 200 and b["status"] == "applied", "after a fresh check approval succeeds")
        d2 = (await q("SELECT score FROM ops_governance.mdm_match_decision WHERE decision_id = $1::uuid", b["decision_id"]))[0]
        check(float(d2["score"]) == float(newscore), "decision carries the score as of approval")

        print("check failed")
        s, b = await call(c, STEWARD, "POST", f"/candidate/{c6}/propose", json={"verdict": "same"})
        p6 = b["proposal_id"]
        await q("UPDATE ops_governance.mdm_match_candidate SET suggested_master_bk = 'MOVED' WHERE candidate_id = $1::uuid", c6)
        s, b = await call(c, STEWARD, "POST", f"/proposal/{p6}/check")
        check(s == 200 and b["status"] == "check_failed", "master moved -> re-check fails")
        s, b = await call(c, APPROVER, "POST", f"/proposal/{p6}/approve", json={})
        check(s == 409, "a failed check cannot be approved")
        s, b = await call(c, STEWARD, "GET", "/proposals", params={"view": "attention"})
        check(p6 in [x["proposal_id"] for x in b["proposals"]], "failed check shows in the attention list")
        s, _ = await call(c, STEWARD, "POST", f"/proposal/{p6}/withdraw")

        print("concurrent approvals")
        s, b = await call(c, STEWARD2, "POST", f"/candidate/{c5}/propose", json={"verdict": "different"})
        pc = b["proposal_id"]
        _current["u"] = APPROVER2       # both requests race as approvers
        r1 = c.post(f"/api/mdm/proposal/{pc}/approve", json={})
        r2 = c.post(f"/api/mdm/proposal/{pc}/approve", json={})
        res = await asyncio.gather(r1, r2)
        codes = sorted(r.status_code for r in res)
        check(codes == [200, 409], f"two approvals at once -> one applies, one 409 (got {codes})")
        n = (await q("SELECT count(*) n FROM ops_governance.mdm_match_decision WHERE candidate_id = $1::uuid AND recorded_to IS NULL", c5))[0]["n"]
        check(n == 1, "exactly one current decision written")
        dd = (await q("SELECT decision, master_bk FROM ops_governance.mdm_match_decision WHERE candidate_id = $1::uuid AND recorded_to IS NULL", c5))[0]
        check(dd["decision"] == "reject" and dd["master_bk"] is None, "'different' applies as reject with no master")

        print("decline")
        s, b = await call(c, STEWARD, "POST", f"/candidate/{c3}/propose", json={"verdict": "different"})
        p3 = b["proposal_id"]
        s, _ = await call(c, APPROVER, "POST", f"/proposal/{p3}/decline", json={"note": "  "})
        check(s == 422, "decline without a reason -> 422")
        n0 = (await q("SELECT count(*) n FROM ops_governance.mdm_match_decision"))[0]["n"]
        s, b = await call(c, APPROVER, "POST", f"/proposal/{p3}/decline", json={"note": "same building, two wards"})
        check(s == 200 and b["status"] == "declined", "decline with a reason -> declined")
        n1 = (await q("SELECT count(*) n FROM ops_governance.mdm_match_decision"))[0]["n"]
        check(n0 == n1, "decline writes no decision")
        s, b = await call(c, STEWARD, "GET", "/review-queue")
        check(c3 in [x["candidate_id"] for x in b["candidates"]], "declined suggestion returns to the queue")

        print("not sure and dismiss")
        n0 = (await q("SELECT count(*) n FROM ops_governance.mdm_match_decision"))[0]["n"]
        s, _ = await call(c, STEWARD, "POST", f"/candidate/{c4}/review", json={"outcome": "not_sure"})
        s, b = await call(c, STEWARD, "GET", "/review-queue")
        row = [x for x in b["candidates"] if x["candidate_id"] == c4]
        check(s == 200 and row and row[0]["review"]["outcome"] == "not_sure", "not sure stays in the queue, flagged")
        s, _ = await call(c, STEWARD, "POST", f"/candidate/{c4}/review", json={"outcome": "dismissed"})
        s, b = await call(c, STEWARD, "GET", "/review-queue")
        check(c4 not in [x["candidate_id"] for x in b["candidates"]], "dismissed leaves the queue")
        n1 = (await q("SELECT count(*) n FROM ops_governance.mdm_match_decision"))[0]["n"]
        check(n0 == n1, "reviews write no decision")

        print("restore (undo) through four eyes")
        dec1 = (await q("SELECT applied_decision_id FROM ops_governance.mdm_change_proposal WHERE proposal_id = $1::uuid", p1))[0]["applied_decision_id"]
        s, b = await call(c, STEWARD2, "POST", f"/decision/{dec1}/propose-restore", json={"note": "wrong master"})
        check(s == 200 and b["status"] == "checked", "restore proposal checked")
        pr = b["proposal_id"]
        s, b = await call(c, APPROVER, "POST", f"/proposal/{pr}/approve", json={})
        check(s == 200, "restore approved")
        d = (await q("SELECT recorded_to FROM ops_governance.mdm_match_decision WHERE decision_id = $1", dec1))[0]
        st = (await q("SELECT status FROM ops_governance.mdm_match_candidate WHERE candidate_id = $1::uuid", c1))[0]["status"]
        check(d["recorded_to"] is not None and st == "pending", "decision superseded, candidate re-opened")

        print("withdraw")
        s, b = await call(c, STEWARD, "POST", f"/candidate/{c3}/propose", json={"verdict": "same"})
        p3b = b["proposal_id"]
        s, _ = await call(c, STEWARD2, "POST", f"/proposal/{p3b}/withdraw")
        check(s == 403, "only the proposer can withdraw")
        s, b = await call(c, STEWARD, "POST", f"/proposal/{p3b}/withdraw")
        check(s == 200 and b["status"] == "withdrawn", "proposer withdraws")

        print("bulk")
        s, b = await call(c, STEWARD, "POST", "/proposals/bulk",
                          json={"candidate_ids": [c3, c2, "not-a-uuid", c3]})
        res = {r["candidate_id"]: r for r in b["results"]}
        check(s == 200 and b["requested"] == 3, "duplicates collapsed")
        check(res[c3]["ok"] and not res[c2]["ok"] and not res["not-a-uuid"]["ok"],
              "partial success reported per item (decided and bad id fail, open one succeeds)")

        print("approver by capability (console role grant, no token role)")
        if not ADMIN_DSN:
            print("  skip TEST_ADMIN_DSN not set")
        else:
            admin = await asyncpg.connect(ADMIN_DSN)
            rid = await admin.fetchval("""
                INSERT INTO ops_perm.role (code, name) VALUES ('R-STEW-APR', 'MDM Approver')
                ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name RETURNING id""")
            await admin.execute("""INSERT INTO ops_perm.role_capability (role_id, capability_code)
                                   VALUES ($1, 'mdm_approve') ON CONFLICT DO NOTHING""", rid)
            for u in (CAPAPPROVER, NOCAP):
                await admin.execute("""INSERT INTO ops_identity."user" (kc_sub, realm, username)
                                       VALUES ($1::uuid, 'privileged-ops', $2)
                                       ON CONFLICT (kc_sub) DO NOTHING""", u.kc_sub, u.username)
            await admin.execute("""INSERT INTO ops_perm.user_role (kc_sub, role_id, source)
                                   VALUES ($1::uuid, $2, 'admin_grant')""", CAPAPPROVER.kc_sub, rid)
            await admin.close()
            free = await q("""
                SELECT c.candidate_id FROM ops_governance.mdm_match_candidate c
                WHERE c.status = 'pending' AND c.suggested_master_bk IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM ops_governance.mdm_change_proposal p
                                   WHERE p.source_system_code = c.left_source_system_code
                                     AND p.source_record_key = c.left_source_record_key
                                     AND p.status IN ('proposed', 'checked'))
                LIMIT 1""")
            check(len(free) == 1, "a free suggestion for the capability check")
            s, b = await call(c, STEWARD, "POST", f"/candidate/{free[0]['candidate_id']}/propose",
                              json={"verdict": "same"})
            pc = b["proposal_id"]
            s, _ = await call(c, NOCAP, "POST", f"/proposal/{pc}/approve", json={})
            check(s == 403, "no token role and no capability -> cannot approve")
            s, b = await call(c, CAPAPPROVER, "GET", "/proposals", params={"view": "approvals"})
            check(s == 200 and pc in [x["proposal_id"] for x in b["proposals"]],
                  "granted in ops_perm -> sees the approvals queue")
            s, b = await call(c, CAPAPPROVER, "GET", f"/proposal/{pc}")
            check(s == 200 and b["can_approve"] is True, "granted in ops_perm -> detail offers approve")
            s, b = await call(c, CAPAPPROVER, "POST", f"/proposal/{pc}/approve", json={})
            check(s == 200 and b["status"] == "applied", "granted in ops_perm -> approves (no Keycloak role)")

        print("audit")
        rows = await q("SELECT action, explanation FROM ops_audit.policy_log WHERE id > $1", audit_start)
        acts = {r["action"] for r in rows}
        check({"mdm.propose", "mdm.approve", "mdm.decline", "mdm.review", "mdm.check",
               "mdm.withdraw"} <= acts, "this run wrote audit rows for every kind of change")
        check(all(isinstance(r["explanation"], dict) and r["explanation"].get("actor") for r in rows),
              "every new audit row is a JSON object naming the actor")
        events = (await q("SELECT count(*) n FROM ops_governance.mdm_proposal_event"))[0]["n"]
        check(events > 0, "proposal history recorded")

    await ops_db.close_pool()
    print(f"\n{'ALL PASSED' if not FAILS else f'{len(FAILS)} FAILED'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    asyncio.run(main())
