#!/usr/bin/env bash
# Regression test for the public access-request submit endpoint.
#
# Guards app/routes/access_requests.py::submit_public_access_request:
#   - the anonymous public endpoint accepts a valid submission (no Bearer) and
#     returns an AR-YYYY-NNNN tracking id;
#   - the duplicate guard: a second active (pending) request from the same
#     requester (email matched case-insensitively) with an overlapping
#     requested scope returns 409; a disjoint scope, or a different
#     requester, is accepted;
#   - schema validation returns 422 (with a parseable `detail` array) for a
#     short purpose, a name below the minimum, or a malformed email;
#   - identity storage: verified_via='fayda' and fayda_id only on a Fayda
#     verification; a National ID or passport number is stored as
#     '<type>_pending' with fayda_id NULL (never presented as verified).
#
# Self-contained: drives the deployed gateway through a port-forward, asserts
# database state via psql, then deletes its own rows. Exits non-zero on any
# failure.
#
# Prerequisites: a running `nhdw` k3d cluster with the gateway and Postgres
# up, built from the current access_requests.py. Run from anywhere:
#   bash 6_access-points/api/nhdw-gateway/tests/test_public_access_request.sh
set -uo pipefail

NS=nhdw
PGPOD=postgres-vault-0
PGCMD="kubectl -n $NS exec -i $PGPOD -- psql -U vault_admin -d nhdw_ops -tA"
PORT=18092
GW="http://127.0.0.1:${PORT}"

# All rows this test creates share this email domain marker so cleanup is exact.
E1="regtest.pub.one@ar-regtest.example"
E2="regtest.pub.two@ar-regtest.example"

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); echo "  ok $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  FAIL $1"; }
eq()   { if [ "$1" = "$2" ]; then ok "$3 ($1)"; else bad "$3; got '$1' expected '$2'"; fi; }

clean_db() {
  $PGCMD -c "DELETE FROM ops_governance.access_request
             WHERE lower(email) LIKE '%@ar-regtest.example';" >/dev/null 2>&1
}

PF_PID=""
cleanup() {
  [ -n "$PF_PID" ] && kill "$PF_PID" 2>/dev/null
  clean_db
}
trap cleanup EXIT

# helpers
# post: submit a JSON payload and print only the HTTP status.
# postj: submit a JSON payload and print the response body.
post() { curl -s -o /dev/null -w '%{http_code}' -X POST "$GW/api/public/access-requests" \
           -H 'Content-Type: application/json' -d "$1"; }
postj() { curl -s -X POST "$GW/api/public/access-requests" \
            -H 'Content-Type: application/json' -d "$1"; }
jval() { python3 -c "import sys,json
try: print(json.load(sys.stdin).get('$1',''))
except Exception: print('')"; }
detail_is_array() { python3 -c "import sys,json
try: d=json.load(sys.stdin); print('yes' if isinstance(d.get('detail'),list) else 'no')
except Exception: print('no')"; }

# port-forward the gateway
kubectl -n $NS port-forward svc/nhdw-gateway ${PORT}:8000 >/dev/null 2>&1 &
PF_PID=$!
for _ in $(seq 1 30); do curl -sf -o /dev/null "$GW/openapi.json" 2>/dev/null && break; sleep 0.5; done

clean_db   # fresh start

echo "first request (anonymous, valid) "
R1=$(postj "{\"full_name\":\"Regtest One\",\"email\":\"$E1\",\"purpose\":\"Testing the public access-request submit path end to end.\",\"dashboards\":[\"internal-bi\"]}")
T1=$(echo "$R1" | jval tracking_id)
echo "$T1" | grep -Eq '^AR-[0-9]{4}-[0-9]{4}$' \
  && ok "unauthenticated submit accepted -> $T1" \
  || bad "unauthenticated submit did not return an AR- tracking id (got '$T1')"

echo "duplicate: same requester, overlapping scope -> 409 "
eq "$(post "{\"full_name\":\"Regtest One\",\"email\":\"$E1\",\"purpose\":\"A second attempt at the very same access scope.\",\"dashboards\":[\"internal-bi\"]}")" \
   "409" "same email + same scope is a duplicate"
# case-insensitive email match still trips the guard
eq "$(post "{\"full_name\":\"Regtest One\",\"email\":\"$(echo "$E1" | tr a-z A-Z)\",\"purpose\":\"Same person, upper-cased email, same scope again.\",\"dashboards\":[\"internal-bi\"]}")" \
   "409" "email match is case-insensitive"

echo "same requester, DISJOINT scope -> accepted "
eq "$(post "{\"full_name\":\"Regtest One\",\"email\":\"$E1\",\"purpose\":\"A different access scope for the same person.\",\"dashboards\":[\"research-api\"]}")" \
   "200" "same email + disjoint scope is accepted"
# research-api is now pending, so repeating it overlaps and returns 409
eq "$(post "{\"full_name\":\"Regtest One\",\"email\":\"$E1\",\"purpose\":\"Repeating the research-api scope which is now pending.\",\"dashboards\":[\"research-api\"]}")" \
   "409" "repeating a now-pending scope is a duplicate"

echo "different requester, same scope -> accepted "
eq "$(post "{\"full_name\":\"Regtest Two\",\"email\":\"$E2\",\"purpose\":\"A different applicant asking for the same dashboard.\",\"dashboards\":[\"internal-bi\"]}")" \
   "200" "a different email is not a duplicate"

echo "schema validation -> 422 (parseable detail array) "
eq "$(post "{\"full_name\":\"Shorty\",\"email\":\"nope@ar-regtest.example\",\"purpose\":\"too short\"}")" \
   "422" "purpose shorter than 10 chars is rejected"
eq "$(postj "{\"full_name\":\"Shorty\",\"email\":\"nope@ar-regtest.example\",\"purpose\":\"too short\"}" | detail_is_array)" \
   "yes" "422 detail is a JSON array (frontend-parseable)"
eq "$(post "{\"full_name\":\"A\",\"email\":\"nope@ar-regtest.example\",\"purpose\":\"A perfectly long enough purpose string here.\"}")" \
   "422" "full_name shorter than 2 chars is rejected"
eq "$(post "{\"full_name\":\"Valid Name\",\"email\":\"not-an-email\",\"purpose\":\"A perfectly long enough purpose string here.\"}")" \
   "422" "malformed email is rejected"

echo "identity storage "
clean_db
NIDMAIL="regtest.pub.nid@ar-regtest.example"
postj "{\"full_name\":\"Nat IdCard\",\"email\":\"$NIDMAIL\",\"purpose\":\"National ID number provided, awaiting manual verify.\",\"id_type\":\"national-id\",\"id_number\":\"NID-90210\"}" >/dev/null
eq "$($PGCMD -c "SELECT verified_via FROM ops_governance.access_request WHERE lower(email)='$NIDMAIL'")" \
   "national-id_pending" "national-id stored as '<type>_pending' (not verified)"
eq "$($PGCMD -c "SELECT COALESCE(fayda_id,'NULL') FROM ops_governance.access_request WHERE lower(email)='$NIDMAIL'")" \
   "NULL" "national-id never writes fayda_id (no false verified badge)"

FAYMAIL="regtest.pub.fayda@ar-regtest.example"
postj "{\"full_name\":\"Fay Verified\",\"email\":\"$FAYMAIL\",\"purpose\":\"Fayda-verified citizen submitting an access request.\",\"id_type\":\"fayda\",\"fayda_id\":\"FIN-REG-1\",\"verified_via\":\"fayda\"}" >/dev/null
eq "$($PGCMD -c "SELECT verified_via FROM ops_governance.access_request WHERE lower(email)='$FAYMAIL'")" \
   "fayda" "fayda verify stores verified_via='fayda'"
eq "$($PGCMD -c "SELECT fayda_id FROM ops_governance.access_request WHERE lower(email)='$FAYMAIL'")" \
   "FIN-REG-1" "fayda verify stores the verified fayda_id"

echo
echo ""
echo "  PASS=$PASS  FAIL=$FAIL"
echo ""
[ "$FAIL" -eq 0 ]
