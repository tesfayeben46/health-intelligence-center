#!/usr/bin/env bash
# test_masking_e2e.sh: the masking negative path against a live deployment.
#
# Unit tests cover the primitives and `opa test` covers the policy; this checks
# that the two are wired together on the paths that serve users. It reads the
# Supply Control Tower feed (bi_supply.mart_ct_nodes carries the classified
# facility GPS) as several identities and asserts what each one gets:
#
#   R-CONS-ANA  (reveal: restricted)   -> EXACT coordinates
#   R-SUPER     (reveal: wildcard)     -> EXACT coordinates
#   R-CONS-ANA  + LOW TRUST            -> GENERALIZED (1 decimal ~ 11 km)
#   R-CONS-PRT  (no read grant)        -> 403 (denied before masking matters)
#   unauthenticated                    -> 403
#
# The low-trust case shows that masking transforms values rather than only
# logging: same role, same mart, coordinates rounded.
#
# Two read paths are checked, the Control Tower feed and the generic mart
# reader, because each looks up the classification independently, and a
# denylist lookup that misses fails open without any error (for example, a
# Trino-qualified schema name where the classification store keys on the
# bare name).
#
# Usage (from the k3s host, or anywhere with cluster and ingress access):
#   NHDW_HOSTBASE=<public-ip>.nip.io 6_access-points/api/nhdw-gateway/tests/test_masking_e2e.sh
# The impersonation secret is read from the cluster; it never appears in args.
set -uo pipefail
NS="${NS:-nhdw}"
HOSTBASE="${NHDW_HOSTBASE:-127.0.0.1.nip.io}"
GW="https://gateway.${HOSTBASE}"
FAILED=0

pass(){ echo "  ok $*"; }
fail(){ echo "  FAIL $*"; FAILED=1; }

SECRET="$(kubectl -n "$NS" get secret nhdw-gateway-secrets \
            -o jsonpath='{.data.IMPERSONATION_SECRET}' 2>/dev/null | base64 -d)"
[ -n "$SECRET" ] || { echo "FATAL: no impersonation secret in $NS/nhdw-gateway-secrets"; exit 2; }

# $1=role  $2=trust(high|low)  -> first node's lat, or the HTTP status on failure
ct_lat() {
  local body status
  body="$(curl -sk --max-time 20 -w '\n%{http_code}' \
      -H "X-Mock-User: 00000000-0000-0000-0000-00000000000$3" \
      -H "X-Mock-Role: $1" -H "X-Mock-Trust: $2" -H "X-Mock-Secret: $SECRET" \
      "$GW/api/ct/plan")"
  status="$(printf '%s' "$body" | tail -1)"
  if [ "$status" != "200" ]; then printf 'HTTP-%s' "$status"; return; fi
  printf '%s' "$body" | sed '$d' | python3 -c "
import sys, json
n = (json.load(sys.stdin).get('nodes') or [])
print(n[0]['lat'] if n else 'NO-NODES')"
}

echo "== masking end-to-end (${GW}) =="

ana="$(ct_lat R-CONS-ANA high 1)"
case "$ana" in
  HTTP-*|NO-NODES) fail "analyst could not read the CT feed ($ana)" ;;
  *.*[0-9][0-9][0-9]*) pass "analyst (reveal: restricted) sees EXACT coordinates ($ana)" ;;
  *) fail "analyst coordinates look generalized; reveal grant not applied ($ana)" ;;
esac

sup="$(ct_lat R-SUPER high 2)"
case "$sup" in
  *.*[0-9][0-9][0-9]*) pass "super-user sees EXACT coordinates ($sup)" ;;
  *) fail "super-user coordinates unexpected ($sup)" ;;
esac

# The classification says generalize, so the correct answer is a coarsened
# number (one decimal, about 11 km), not an absent one. A dropped column leaks
# nothing, but accepting it here would hide a generalize class that does not
# work (for example, one that fails on Trino's Decimal values).
low="$(ct_lat R-CONS-ANA low 3)"
case "$low" in
  HTTP-*|NO-NODES) fail "low-trust analyst could not read the CT feed ($low)" ;;
  *.*[0-9][0-9][0-9]*) fail "LOW-TRUST IDENTITY SAW EXACT COORDINATES; masking not applied ($low)" ;;
  None|null|"") fail "low-trust coordinate was DROPPED, not generalized; generalize is not working ($low)" ;;
  *.[0-9]) pass "low-trust analyst sees GENERALIZED coordinates ($low vs $ana)" ;;
  *) fail "low-trust coordinate is neither exact nor 1-decimal generalized ($low)" ;;
esac

prt="$(ct_lat R-CONS-PRT high 4)"
case "$prt" in
  HTTP-403) pass "partner role denied the CT feed outright (403)" ;;
  *) fail "partner role expected 403, got $prt" ;;
esac

# the same classified columns through the generic mart reader
# Second, independent classification lookup; a per-route mistake shows up here
# and nowhere else.
mart_lat() {
  local body status
  body="$(curl -sk --max-time 20 -w '\n%{http_code}' \
      -H "X-Mock-User: 00000000-0000-0000-0000-00000000001$3" \
      -H "X-Mock-Role: $1" -H "X-Mock-Trust: $2" -H "X-Mock-Secret: $SECRET" \
      "$GW/api/marts/bi_supply/mart_ct_nodes")"
  status="$(printf '%s' "$body" | tail -1)"
  if [ "$status" != "200" ]; then printf 'HTTP-%s' "$status"; return; fi
  printf '%s' "$body" | sed '$d' | python3 -c "
import sys, json
r = (json.load(sys.stdin).get('rows') or [])
print(r[0].get('lat') if r else 'NO-ROWS')"
}

m_ana="$(mart_lat R-CONS-ANA high 1)"
case "$m_ana" in
  HTTP-*|NO-ROWS) fail "analyst could not read mart_ct_nodes ($m_ana)" ;;
  *.*[0-9][0-9][0-9]*) pass "mart reader: analyst sees EXACT coordinates ($m_ana)" ;;
  *) fail "mart reader: analyst coordinates look generalized ($m_ana)" ;;
esac

m_low="$(mart_lat R-CONS-ANA low 2)"
case "$m_low" in
  HTTP-*|NO-ROWS) fail "low-trust analyst could not read mart_ct_nodes ($m_low)" ;;
  *.*[0-9][0-9][0-9]*) fail "MART READER SERVED EXACT COORDINATES TO A LOW-TRUST IDENTITY ($m_low)" ;;
  None|null|"") fail "mart reader: low-trust coordinate DROPPED, not generalized ($m_low)" ;;
  *.[0-9]) pass "mart reader: low-trust sees GENERALIZED coordinates ($m_low vs $m_ana)" ;;
  *) fail "mart reader: low-trust coordinate neither exact nor generalized ($m_low)" ;;
esac

anon="$(curl -sk --max-time 20 -o /dev/null -w '%{http_code}' "$GW/api/ct/plan")"
[ "$anon" = "403" ] || [ "$anon" = "401" ] \
  && pass "unauthenticated denied ($anon)" \
  || fail "unauthenticated expected 401/403, got $anon"

echo
[ "$FAILED" = 0 ] && echo "== masking e2e: ALL PASS ==" || echo "== masking e2e: FAILURES above =="
exit $FAILED
