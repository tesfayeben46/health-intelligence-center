#!/usr/bin/env bash
# regen-opa-policy.sh: deploy-time OPA policy regeneration.
#
# Regenerates the OPA policy from the normalized permission store (ops_perm)
# rather than the committed rego, so a permission edited through the perm-admin
# API (or the seed) becomes the deployed policy. Gated by `opa test`: a
# DB-sourced policy that fails the authz test suite does not reach OPA.
# Fail-closed: on any failure the running OPA is left untouched and the script
# exits non-zero.
#
# Flow (no host psycopg or opa needed; both run in-cluster):
#   1. compile.py --from-db  -> per-role rego                    (python Job)
#   2. opa test on {compiled rego + common.rego + authz_test}   (opa Job, gate)
#   3. publish common.rego + role_r_*.rego -> opa-policies CM + reload OPA
#
# Runs standalone, or from bootstrap after the RBAC seed that fills the store.
# Idempotent: on a fresh boot the DB policy equals the committed rego.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NS="${NHDW_NAMESPACE:-nhdw}"
STAGE="$(mktemp -d)"
cleanup() {
  rm -rf "$STAGE"
  kubectl -n "$NS" delete job opa-policy-compile opa-policy-test \
    configmap/opa-policy-src configmap/opa-policy-test-src \
    --ignore-not-found >/dev/null 2>&1
}
trap cleanup EXIT
step() { echo; echo "▸ $*"; }

echo "== regen OPA policy from the permission store (opa test gated) =="

# 1. compile --from-db (python Job prints a base64 tarball of the rego)
step "compile OPA rego from ops_perm (compile.py --from-db)"
kubectl -n "$NS" create configmap opa-policy-src \
  --from-file="$REPO/7_security/opa/scripts/compile.py" \
  --from-file="$REPO/7_security/opa/policies/common.rego" \
  --from-file="$REPO/7_security/opa/policies/authz_test.rego" \
  $(for r in "$REPO"/bundle/roles/r-*.yaml; do echo --from-file="$r"; done) \
  --dry-run=client -o yaml | kubectl -n "$NS" apply -f - >/dev/null
kubectl -n "$NS" delete job opa-policy-compile --ignore-not-found --wait=true >/dev/null 2>&1
cat <<EOF | kubectl -n "$NS" apply -f - >/dev/null
apiVersion: batch/v1
kind: Job
metadata: { name: opa-policy-compile }
spec:
  backoffLimit: 1
  ttlSecondsAfterFinished: 300
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: compile
          image: python:3.12-slim
          command: ["sh","-c"]
          args:
            - >
              pip install --quiet --no-cache-dir --retries 5 --timeout 90 'psycopg[binary]' PyYAML >&2 &&
              NHDW_ROLES_DIR=/src python /src/compile.py --from-db --out-dir /out --pg-db nhdw_ops >&2 &&
              cp /src/common.rego /src/authz_test.rego /out/ &&
              tar czf - -C /out . | base64 -w0
          env:
            - { name: POSTGRES_HOST, value: postgres-vault }
            - { name: POSTGRES_PORT, value: "5432" }
            - { name: POSTGRES_USER, value: vault_admin }
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef: { name: postgres-vault-secrets, key: POSTGRES_PASSWORD }
          volumeMounts: [{ name: s, mountPath: /src }]
      volumes: [{ name: s, configMap: { name: opa-policy-src } }]
EOF
if ! kubectl -n "$NS" wait --for=condition=complete job/opa-policy-compile --timeout=180s >/dev/null 2>&1; then
  echo "  FAIL: compile --from-db Job failed:" >&2
  kubectl -n "$NS" logs job/opa-policy-compile 2>/dev/null | tail -10 | sed 's/^/    /' >&2
  exit 1
fi
kubectl -n "$NS" logs job/opa-policy-compile 2>/dev/null | tail -1 | base64 -d | tar xz -C "$STAGE" 2>/dev/null
n=$(ls "$STAGE"/role_r_*.rego 2>/dev/null | wc -l)
[ "$n" -ge 1 ] || { echo "  FAIL: no rego captured from the compile Job" >&2; exit 1; }
echo "  ok: compiled $n role policies from ops_perm"

# 2. opa test the DB-sourced policy (fail-closed gate)
step "opa test the DB-sourced policy"
kubectl -n "$NS" create configmap opa-policy-test-src --from-file="$STAGE" \
  --dry-run=client -o yaml | kubectl -n "$NS" apply -f - >/dev/null
kubectl -n "$NS" delete job opa-policy-test --ignore-not-found --wait=true >/dev/null 2>&1
cat <<EOF | kubectl -n "$NS" apply -f - >/dev/null
apiVersion: batch/v1
kind: Job
metadata: { name: opa-policy-test }
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 300
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: test
          image: openpolicyagent/opa:1.5.0
          args: ["test","--ignore=.*","/policies"]
          volumeMounts: [{ name: p, mountPath: /policies }]
      volumes: [{ name: p, configMap: { name: opa-policy-test-src } }]
EOF
if kubectl -n "$NS" wait --for=condition=complete job/opa-policy-test --timeout=120s >/dev/null 2>&1; then
  echo "  ok: $(kubectl -n "$NS" logs job/opa-policy-test 2>/dev/null | grep -oE 'PASS: [0-9]+/[0-9]+' | tail -1)"
else
  echo "  FAIL: opa test FAILED on the DB-sourced policy, OPA left untouched (fail-closed):" >&2
  kubectl -n "$NS" logs job/opa-policy-test 2>/dev/null | tail -12 | sed 's/^/    /' >&2
  exit 1
fi

# 3. publish common + role_r_* (not the test), then reload OPA
step "publish -> opa-policies ConfigMap + reload OPA"
rm -f "$STAGE"/authz_test.rego
kubectl -n "$NS" create configmap opa-policies --from-file="$STAGE" \
  --dry-run=client -o yaml | kubectl -n "$NS" apply -f - >/dev/null
kubectl -n "$NS" rollout restart deploy/opa >/dev/null 2>&1
if kubectl -n "$NS" rollout status deploy/opa --timeout=120s >/dev/null 2>&1; then
  echo "  ok: OPA reloaded with the store-sourced policy"
else
  echo "  FAIL: OPA did not become ready after reload" >&2
  exit 1
fi

echo
echo "== OPA policy regenerated from the permission store =="
