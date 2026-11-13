#!/usr/bin/env bash
# test-country-guard.sh: a cold boot must not seed the wrong country.
#
# The seed-only-if-current check in sources-up.sh compares the synth version.
# Without a country check, a packed dump of one country's rows stamped with the
# current version makes every skip branch fire, and the warehouse comes up with
# that country's data whichever profile was requested, while the boot reports
# success. Only non-default profiles exercise this path, so it is tested
# explicitly.
#
#   bash bundle/scripts/test-country-guard.sh
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$REPO/bundle/scripts/sources-up.sh"
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  ok: $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL: $1"; [ -n "${2:-}" ] && echo "      $2"; }

# Evaluate profile_matches from lib-country-guard.sh, the same file sources-up.sh
# sources, so the test covers the shipped logic rather than a copy.
match_for() {
  ( export NHDW_PROFILE="${1:-}" NHDW_ENTITY_BUILD="${2:-}"
    . "$REPO/bundle/scripts/lib-country-guard.sh"
    country_guard_init
    profile_matches "${3:-}" >/dev/null 2>&1 && echo trust || echo synth )
}

echo "== the stamped-country contract =="
[ "$(match_for '' '' '')"            = "trust" ] && ok "default boot, unstamped pack -> trusted (pre-stamp behaviour)" \
                                                || bad "default + unstamped should be trusted"
[ "$(match_for 'zm-v1' '' '')"       = "synth" ] && ok "zm-v1 boot, unstamped pack -> rebuilt (cannot tell its country)" \
                                                || bad "zm-v1 + unstamped must rebuild"
[ "$(match_for 'zm-v1' '' 'zm-v1')"  = "trust" ] && ok "zm-v1 boot, zm-v1-stamped pack -> TRUSTED (the durable upgrade)" \
                                                || bad "a matching stamp must be trusted"
[ "$(match_for 'zm-v1' '' 'et-v3')"  = "synth" ] && ok "zm-v1 boot, et-v3-stamped pack -> rebuilt" \
                                                || bad "a mismatched stamp must rebuild"
[ "$(match_for '' '' 'zm-v1')"       = "synth" ] && ok "default boot, zm-v1-stamped pack -> rebuilt" \
                                                || bad "a foreign stamp must rebuild even on default"
[ "$(match_for '' '/x/_build-entity-model.zm' 'zm')" = "trust" ] \
    && ok "entity-build boot matches the stem it stamps" \
    || bad "NHDW_ENTITY_BUILD stem should match its own stamp"

echo "== every skip branch consults it =="
# Every branch that decides whether to skip synthesis must be gated; a new
# source added without the guard reintroduces the defect.
BRANCHES=$(grep -cE 'skipping synthesize|already seeded .*skipping' "$SRC" || true)
GATED=$(grep -cE 'profile_matches |COUNTRY_FORCES_SYNTH' "$SRC" || true)
[ "$GATED" -ge 5 ] && ok "country consulted $GATED times for $BRANCHES skip messages" \
                   || bad "only $GATED country checks: a skip branch is unguarded"

# The eLMIS branch is not guarded: that archive is the Zambian
# data, so restoring it again is wasted work, not wrong data. Assert that the
# exemption is still present.
grep -q 'eLMIS already carries history' "$SRC" \
  && ok "the eLMIS exemption is still present" \
  || bad "the eLMIS branch changed: re-check whether the exemption still holds"

echo
echo "  $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
