#!/usr/bin/env python3
"""Regression test: public access-request form guards and the Fayda email-OTP flow.

Pins the client-side behaviour that keeps a public requester from seeing a raw
422 or a link to the admin console:
  - field-level validation before submit (full name >= 2, valid email,
    purpose >= 10), shown inline without a round-trip;
  - Fayda verification by email OTP: an invalid FAN is refused inline; a
    challenge to a seeded FAN emails a code that reaches the verified state;
    a wrong code is rejected; an unknown FAN reports "no record matches";
  - the form resets after a successful submit;
  - the success view offers only "Return to datasets" / "Close" and no link
    to the admin dashboard.

The verified state uses the real flow: the test challenges a seeded FAN and
reads the one-time code from the Fayda replica's container log (the
[fayda][DEV-ONLY] line).

Requires a running system (bundle/scripts/verify-system.sh passing), the Fayda
replica on host port 8096, and Playwright. Run:
    python3 test_access_form.py   # exits non-zero on any failure
"""
import re
import subprocess
import time

from playwright.sync_api import sync_playwright

PUBLIC = "https://public.127.0.0.1.nip.io/"
# Seeded Fayda identity (0_external/fayda/seed/citizens.yaml); the replica
# logs every code it sends, so the test can read it back.
SEEDED_FAN = "1000000000000023"   # Genet Abera
SEEDED_EMAIL = "genet.abera@citizens.example.org"   # the email on that record (the pair)
UNKNOWN_FAN = "9999999999999999"
FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILURES.append(label)


def open_modal(pg):
    pg.goto(PUBLIC, wait_until="networkidle", timeout=30000)
    pg.click("text=Request access")
    pg.wait_for_selector("text=Verify your identity", timeout=10000)
    pg.wait_for_timeout(700)  # modal entry animation


def body(pg) -> str:
    return pg.inner_text("body")


def read_otp(fan: str) -> str | None:
    """Read the one-time code the Fayda replica generated + logged for this FAN."""
    out = subprocess.run(
        ["bash", "-lc",
         f"docker logs fayda-replica 2>&1 | grep 'DEV-ONLY' | grep 'FAN {fan}' | tail -1"],
        capture_output=True, text=True, timeout=30).stdout
    m = re.search(r"= (\d{6}) ", out)
    return m.group(1) if m else None


def main() -> int:
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(ignore_https_errors=True,
                            viewport={"width": 1400, "height": 2200})

        # ---- 1. invalid Fayda FAN: refused inline (client format check) ----
        pg = ctx.new_page()
        open_modal(pg)
        pg.fill("input[placeholder='e.g. 1000000000000001']", "abc123")  # not numeric
        pg.get_by_role("button", name="Send code", exact=True).click()
        pg.wait_for_timeout(300)
        check("valid Fayda number" in body(pg),
              "invalid FAN is refused inline (no request, no fake verify)")

        # ---- 2. missing required fields (submit guard, inline messages) ----
        pg.get_by_role("button", name="Submit request", exact=True).click()
        pg.wait_for_timeout(300)
        txt = body(pg)
        check("at least 2 characters" in txt, "empty name blocked inline")
        check("valid email address" in txt, "empty/invalid email blocked inline")
        check("at least 10 characters" in txt, "short purpose blocked inline")
        check("Request submitted" not in txt, "a guarded submit does not reach the success view")

        # ---- 3. unknown FAN -> "no record matches" (not a fake OK) ----
        pg2 = ctx.new_page()
        open_modal(pg2)
        pg2.fill("input[placeholder='e.g. 1000000000000001']", UNKNOWN_FAN)
        pg2.fill("input[placeholder='you@example.com']", "someone@example.com")
        pg2.get_by_role("button", name="Send code", exact=True).click()
        pg2.wait_for_timeout(1500)
        check("no fayda record matches" in body(pg2).lower(),
              "unknown FAN reports 'no record matches' (never a success)")
        check("Identity verified with Fayda" not in body(pg2),
              "an unknown FAN never shows the verified badge")

        # ---- 4. real OTP round-trip: seeded FAN -> code -> verified ----
        pg3 = ctx.new_page()
        open_modal(pg3)
        pg3.fill("input[placeholder='e.g. 1000000000000001']", SEEDED_FAN)
        pg3.fill("input[placeholder='you@example.com']", SEEDED_EMAIL)
        pg3.get_by_role("button", name="Send code", exact=True).click()
        pg3.wait_for_selector("text=We sent a 6-digit code", timeout=15000)
        pg3.wait_for_timeout(700)
        # a wrong code first: rejected, never a false success
        pg3.fill("input[maxlength='8']", "000000")
        pg3.get_by_role("button", name="Verify", exact=True).click()
        pg3.wait_for_timeout(500)
        check("not correct" in body(pg3).lower(),
              "a wrong code is rejected ('not correct')")
        check("Identity verified with Fayda" not in body(pg3),
              "a wrong code never shows the verified badge")
        # now the real code from the fayda log
        code = read_otp(SEEDED_FAN)
        check(bool(code), "Fayda emitted + logged a 6-digit OTP for the seeded FAN")
        if code:
            pg3.fill("input[maxlength='8']", code)
            pg3.get_by_role("button", name="Verify", exact=True).click()
            pg3.wait_for_selector("text=Identity verified with Fayda", timeout=15000)
            check("Identity verified with Fayda" in body(pg3),
                  "the emailed code reaches the verified state (real round-trip)")

        # ---- 5. successful submit + form reset + no admin link ----
        pg4 = ctx.new_page()
        open_modal(pg4)
        email = f"regtest.form.{int(time.time())}@ar-regtest.example"
        pg4.fill("input[placeholder='Dr Tigist Bekele']", "Reg Form Tester")
        pg4.fill("input[placeholder='tigist.bekele@moh.gov.et']", email)
        pg4.fill(".modal textarea", "Regression test of the public access-request form reset behaviour.")
        pg4.get_by_role("button", name="Submit request", exact=True).click()
        pg4.wait_for_selector("text=Request submitted", timeout=15000)
        done = body(pg4)
        check("Request submitted" in done, "valid submit reaches the success view")
        check(bool(re.search(r"AR-\d{4}-\d{4}", done)), "success view shows an AR- tracking id")
        # the success view never links to the admin console
        admin_links = pg4.eval_on_selector_all(
            "a[href]", "els => els.filter(e => /admin/i.test(e.getAttribute('href')||'')).length")
        check(admin_links == 0, "success view has no link to the admin console")
        check("Demo mockup" not in done and "admin queue" not in done,
              "the demo-mockup callout + admin-queue button are gone")

        # Return to datasets, reopen, assert the form is RESET (fields empty).
        pg4.get_by_role("button", name="Return to datasets", exact=True).click()
        pg4.wait_for_timeout(500)
        pg4.click("text=Request access")
        pg4.wait_for_selector("text=Verify your identity", timeout=10000)
        pg4.wait_for_timeout(500)
        vals = pg4.eval_on_selector_all(
            ".modal input, .modal textarea",
            "els => els.filter(e => e.type!=='checkbox' && e.type!=='file' && (e.value||'').trim()!=='').length")
        check(vals == 0, "form is reset after a successful submit (all fields empty)")

        b.close()

    # Best-effort DB cleanup of the row this test created (leaves the queue clean).
    try:
        subprocess.run(
            ["bash", "-lc",
             "kubectl -n nhdw exec -i postgres-vault-0 -- env "
             "PGPASSWORD=$(kubectl -n nhdw get secret postgres-vault-secrets "
             "-o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d) psql -U vault_admin "
             "-d nhdw_ops -c \"DELETE FROM ops_governance.access_request "
             "WHERE lower(email) LIKE '%@ar-regtest.example';\""],
            check=False, capture_output=True, timeout=60)
    except Exception:
        pass

    if FAILURES:
        print(f"\nFAILED: {len(FAILURES)} check(s)")
        return 1
    print("\nALL GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
