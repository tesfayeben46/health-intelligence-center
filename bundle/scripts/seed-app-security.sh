#!/usr/bin/env bash
# seed-app-security.sh: seed the application and security state into nhdw_ops.
#
# This is not data-pipeline state: the RBAC and permission model (roles,
# dashboards, pages, widgets, filters, mart attrs, role grants, users) and the
# workshop groups cannot be derived from source data, so they are always seeded
# from files. The data pipeline is seeded separately by seed-realistic.sh.
#
# Order matters: the security loader (18) upserts ops_identity.user from
# users.yaml, and 15_workshop_groups_seed references those users in its group
# rosters, so the security loader runs first.
#
# Prereqs (created at postgres DB-init): nhdw_ops + ops_identity/ops_artifact/
# ops_perm schemas (05_ops_schemas.sql, 17_perm_schemas.sql, 19_api_tokens.sql).
#
# Both steps are idempotent (ON CONFLICT). Safe to re-run.
#
# Usage:  bundle/scripts/seed-app-security.sh [--namespace nhdw]
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NS=nhdw
for a in "$@"; do case "$a" in --namespace=*) NS="${a#--namespace=}";; esac; done

OPS_DB=nhdw_ops
SEC_DIR="$REPO/bundle/seed-pack/app/security"
LOADER="$REPO/7_security/scripts/load_security_seed.py"
PGADMIN_PW=$(kubectl -n "$NS" get secret postgres-vault-secrets -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)

echo "== APP/SECURITY seeding (-> ${OPS_DB}) =="

# compatibility gate: refuse a seed pack that targets a different system
# The pack declares targets_system; the system stamps VERSION. If they disagree,
# refuse before writing anything (fail-closed: seeds cannot break the system).
# Skipped if the VERSION file, manifest or validator is absent.
_VER_FILE="$REPO/VERSION"; _MANIFEST="$REPO/bundle/seed-pack/manifest.yaml"
_VALIDATE="$REPO/bundle/scripts/seed-pack-engine/validate.py"
if [ -f "$_VER_FILE" ] && [ -f "$_MANIFEST" ] && [ -f "$_VALIDATE" ]; then
  _SYSVER="$(cat "$_VER_FILE")"
  if python3 "$_VALIDATE" "$_MANIFEST" --system-version "$_SYSVER" >/dev/null 2>&1; then
    echo "▸ [compat-gate] seed pack compatible with system ${_SYSVER} ok"
  else
    echo "== FATAL: seed pack incompatible with system ${_SYSVER}: refusing to seed =="
    python3 "$_VALIDATE" "$_MANIFEST" --system-version "$_SYSVER" 2>&1 | sed 's/^/  /'
    exit 1
  fi
fi

# 0. reconcile the ops_perm / ops_identity schema before the RBAC loader
# restore-state (bootstrap) runs `pg_restore --clean` with an older packed
# nhdw_ops dump over the fresh initdb schema, so column and table additions made
# after the dump are silently dropped, including ops_perm.widget.source and the
# plugin columns from 17_perm_schemas.sql, which the RBAC loader below reads
# (`DELETE ... WHERE source='core'`). The step-4 migration re-apply runs after
# the loader, too late. initdb never re-runs on an initialised data dir, so the
# idempotent perm-schema DDL is re-applied here, before the loader. All of it is
# CREATE/ALTER ... IF NOT EXISTS (best-effort; duplicate-object errors are
# skipped), so it is also safe on a fresh boot where initdb already ran it.
echo "▸ [perm-schema-heal] re-apply ops_perm schema (survives restore-state --clean)"
# 38_governance.sql is in the same set: the DQ threshold store and incident
# history (fingerprint, dq_flag_event, triggers) must survive restore-state
# --clean and warm clusters, or POST /api/dq/detect returns 500.
for sql in 17_perm_schemas.sql 23_perm_admin_grants.sql 25_perm_scope_migration.sql 32_permission_store.sql 38_governance.sql; do
  f="$REPO/3_storage/postgres/init/${sql}"
  [ -f "$f" ] || { echo "  ! ${sql} missing: skipping"; continue; }
  kubectl -n "$NS" exec -i postgres-vault-0 -- \
    env PGPASSWORD="$PGADMIN_PW" psql -U vault_admin -d "$OPS_DB" -q \
    < "$f" >/dev/null 2>&1
  echo "  ok: ${sql} reconciled"
done

# 1. RBAC / permission model + users (18): in-cluster Job
# The host has no pip and the postgres pod has no python, so run the loader as a
# K8s Job: python:3.12-slim, the loader + security YAML mounted from a configMap,
# talking to the postgres-vault Service over cluster DNS. Idempotent (ON CONFLICT).
echo "▸ [security-rbac] load_security_seed.py over bundle/seed-pack/app/security/"
if [ ! -f "$LOADER" ] || [ ! -d "$SEC_DIR" ]; then
  echo "  ! loader or seed dir missing; skipping"
else
  # Collect the distributed per-dashboard catalog files (dashboard.yaml, pages,
  # sections, per-widget widget.yaml) into catalog.json; the loader treats it as
  # authoritative for the seven catalog tables. A broken catalog fails the step
  # rather than falling back to stale flat seeds.
  CATTMP=$(mktemp -d)
  if python3 "$REPO/bundle/scripts/collect-catalog.py" --repo "$REPO" \
       --out "$CATTMP/catalog.json"; then
    CATFILE="$CATTMP/catalog.json"
    echo "  ok: catalog.json collected from the distributed dashboard catalogs"
  else
    echo "  FAIL: collect-catalog.py FAILED: fix the authored catalog files" >&2
    exit 1
  fi
  # The permission store is normalised from bundle/roles/*.yaml (the OPA
  # data-permission source), which lives outside the seed dir: mount those too
  # (as r-*.yaml keys) and point the loader at /opt/seed via NHDW_ROLES_DIR.
  kubectl -n "$NS" create configmap security-seed-files \
    --from-file="$LOADER" --from-file="$CATFILE" \
    $(for y in "$SEC_DIR"/*.yaml; do echo "--from-file=$y"; done) \
    $(for r in "$REPO"/bundle/roles/r-*.yaml; do echo "--from-file=$r"; done) \
    --dry-run=client -o yaml | kubectl -n "$NS" apply -f - >/dev/null
  rm -rf "$CATTMP"

  kubectl -n "$NS" delete job security-seed --ignore-not-found --wait=true >/dev/null 2>&1
  # Which cast this seed loads. The loader inside the Job resolves the users
  # file itself, but the Job pod cannot see /opt/nhdw-profiles, so the host
  # resolves it (shared resolver) and passes the answer down as NHDW_USERS_SEED,
  # the loader's explicit override. Without it, a switch to another country
  # would silently re-seed the default (Ethiopian) cast.
  USERS_SEED=$(bash "$REPO/bundle/scripts/resolve-users-seed.sh")
  echo "  ▸ cast: $USERS_SEED"

  cat <<EOF | kubectl -n "$NS" apply -f - >/dev/null
apiVersion: batch/v1
kind: Job
metadata:
  name: security-seed
spec:
  backoffLimit: 3
  ttlSecondsAfterFinished: 600
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: security-seed
          image: python:3.12-slim
          command: ["sh","-c"]
          args:
            - >
              pip install --quiet --no-cache-dir --retries 5 --timeout 60 'psycopg[binary]' PyYAML &&
              python /opt/seed/load_security_seed.py --seed-dir /opt/seed --pg-db ${OPS_DB}
          env:
            - { name: POSTGRES_HOST, value: postgres-vault }
            - { name: POSTGRES_PORT, value: "5432" }
            - { name: POSTGRES_USER, value: vault_admin }
            - { name: OPS_DB_NAME,   value: ${OPS_DB} }
            # Role YAMLs are mounted alongside the seed files in /opt/seed.
            - { name: NHDW_ROLES_DIR, value: /opt/seed }
            # the host resolved the active profile's cast (see above)
            - { name: NHDW_USERS_SEED, value: ${USERS_SEED} }
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef: { name: postgres-vault-secrets, key: POSTGRES_PASSWORD }
            # The platform service-account PAT; the loader registers its hash
            # as an R-SERVICE api_token (act-as-owner). Optional, so a
            # secrets.yaml without it still works (no service token).
            - name: SERVICE_PAT
              valueFrom:
                secretKeyRef: { name: nhdw-service-token, key: SERVICE_PAT, optional: true }
          volumeMounts:
            - { name: seed, mountPath: /opt/seed }
      volumes:
        - name: seed
          configMap: { name: security-seed-files }
EOF
  if kubectl -n "$NS" wait --for=condition=complete job/security-seed --timeout=300s >/dev/null 2>&1; then
    kubectl -n "$NS" logs job/security-seed 2>/dev/null | grep -iE 'roles:|dashboards:|users|role_dashboard|ok:|seeded' | tail -8 | sed 's/^/    /'
    echo "  ok: security/RBAC model seeded"
  else
    echo "  FAIL: security seed Job failed: logs:"
    kubectl -n "$NS" logs job/security-seed 2>/dev/null | tail -12 | sed 's/^/    /'
    FAILED=1
  fi
fi

# 1b. public-dashboard articles CMS (ops_governance.public_article)
ART_LOADER="$REPO/7_security/scripts/load_public_articles.py"
ART_DIR="$REPO/bundle/seed-pack/app/articles"
# Country-specific newsroom: a profile provides its own articles.<key>.yaml
# (e.g. articles.zm.yaml for Zambia), else the default articles.yaml. Mounted as
# articles.yaml so the loader path is unchanged; same switch as the users cast.
_ap="${NHDW_PROFILE:-et-v3}"; ART_FILE="$ART_DIR/articles.${_ap%%-*}.yaml"
[ -f "$ART_FILE" ] || ART_FILE="$ART_DIR/articles.yaml"
echo "▸ [public-articles] load_public_articles.py over $(basename "$ART_FILE") (profile ${_ap})"
if [ ! -f "$ART_LOADER" ] || [ ! -f "$ART_FILE" ]; then
  echo "  ! articles loader or seed missing; skipping"
else
  kubectl -n "$NS" create configmap public-articles-files \
    --from-file="$ART_LOADER" \
    --from-file="articles.yaml=$ART_FILE" \
    --dry-run=client -o yaml | kubectl -n "$NS" apply -f - >/dev/null
  kubectl -n "$NS" delete job public-articles-seed --ignore-not-found --wait=true >/dev/null 2>&1
  cat <<EOF | kubectl -n "$NS" apply -f - >/dev/null
apiVersion: batch/v1
kind: Job
metadata:
  name: public-articles-seed
spec:
  backoffLimit: 3
  ttlSecondsAfterFinished: 600
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: public-articles-seed
          image: python:3.12-slim
          command: ["sh","-c"]
          args:
            - >
              pip install --quiet --no-cache-dir --retries 5 --timeout 60 'psycopg[binary]' PyYAML &&
              python /opt/seed/load_public_articles.py --seed-dir /opt/seed --pg-db ${OPS_DB}
          env:
            - { name: POSTGRES_HOST, value: postgres-vault }
            - { name: POSTGRES_PORT, value: "5432" }
            - { name: POSTGRES_USER, value: vault_admin }
            - { name: OPS_DB_NAME,   value: ${OPS_DB} }
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef: { name: postgres-vault-secrets, key: POSTGRES_PASSWORD }
          volumeMounts:
            - { name: seed, mountPath: /opt/seed }
      volumes:
        - name: seed
          configMap: { name: public-articles-files }
EOF
  if kubectl -n "$NS" wait --for=condition=complete job/public-articles-seed --timeout=300s >/dev/null 2>&1; then
    kubectl -n "$NS" logs job/public-articles-seed 2>/dev/null | grep -iE 'articles|ok:' | tail -4 | sed 's/^/    /'
    echo "  ok: public articles seeded"
  else
    echo "  FAIL: public-articles seed Job failed: logs:"
    kubectl -n "$NS" logs job/public-articles-seed 2>/dev/null | tail -12 | sed 's/^/    /'
    FAILED=1
  fi
fi

# 2. workshop groups (15): pure SQL, in the postgres pod (after users exist) ─
echo "▸ [workshop-groups] 15_workshop_groups_seed.sh"
if kubectl -n "$NS" exec -i postgres-vault-0 -- \
      env POSTGRES_USER=vault_admin OPS_DB_NAME="$OPS_DB" PGPASSWORD="$PGADMIN_PW" \
      bash -s < "$REPO/bundle/seed-pack/app/workshop-groups/15_workshop_groups_seed.sh" >/dev/null 2>&1; then
  n=$(kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGADMIN_PW" \
        psql -U vault_admin -d "$OPS_DB" -At -c "SELECT count(*) FROM ops_artifact.workshop_group" 2>/dev/null)
  m=$(kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGADMIN_PW" \
        psql -U vault_admin -d "$OPS_DB" -At -c "SELECT count(*) FROM ops_artifact.workshop_group_member" 2>/dev/null)
  echo "  ok: workshop groups seeded (${n} groups, ${m} members)"
else
  echo "  FAIL: workshop-groups seed failed"
  FAILED=1
fi

# 3. program-demo schema (init) + data (seed pack): directly seeded app data ─
# programme_metrics, submission_funnel, stock_status and the bi_programme views back
# the facility-detail BI components. The schema (tables, views, grants) lives in
# 3_storage/postgres/init/03_program_demo.sql (also in the initdb configMap); the
# sample data is in the seed pack (bundle/seed-pack/app/program-demo). initdb only
# runs on an empty data dir, so both are re-applied here, schema (CREATE OR REPLACE)
# then data (idempotent TRUNCATE + INSERT), keeping the tables and views current
# and populated on every boot in both modes.
echo "▸ [program-demo] schema (init) + data (seed-pack)"
if cat "$REPO/3_storage/postgres/init/03_program_demo.sql" \
       "$REPO/bundle/seed-pack/app/program-demo/program_demo_data.sql" \
     | kubectl -n "$NS" exec -i postgres-vault-0 -- \
       env PGPASSWORD="$PGADMIN_PW" psql -U vault_admin -d nhdw_vault -v ON_ERROR_STOP=1 -q >/dev/null 2>&1; then
  pm=$(kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGADMIN_PW" \
        psql -U vault_admin -d nhdw_vault -At -c "SELECT count(*) FROM bi_facility.programme_metrics" 2>/dev/null)
  echo "  ok: program-demo tables seeded (programme_metrics=${pm})"
else
  echo "  FAIL: program-demo seed failed"
  FAILED=1
fi

# 4. ops numbered migrations that must survive restore-state
# restore-state (bootstrap step 5.5) runs `pg_restore --clean` on nhdw_ops from
# the packed dump. If that dump predates a column or table these migrations add,
# --clean drops and recreates the old shape, losing the addition. initdb never
# re-runs on an initialised data dir, so after a packed rebuild this is the only
# place they are re-applied. All are idempotent (CREATE/ALTER ... IF NOT EXISTS),
# so they are also safe on a fresh boot (initdb already ran them).
#
#   22_project.sh          ops_workspace.project (the project entity). Applied
#                            first: 26's FK targets it.
#   26_artifact_project.sh ops_artifact.artifact.project_id + mlflow_model_name
#                            (the project-scoping columns). Depends on 22
#                            (cross-schema FK to ops_workspace.project).
#   28_dataset_project_link.sh ops_artifact.project_dataset (datasets as an
#                            independent M:N entity). Depends on 22 and 26
#                            (backfills from artifact.project_id, then clears it
#                            for datasets).
#   29_project_review.sh   project.review_status + ops_workspace.project_review
#                            (project-centric graduation). Depends on 22 (ALTERs
#                            ops_workspace.project).
#   27_ml_monitoring.sh    ops_ml.model_drift (+kind, psi_threshold, note) and
#                            model_performance (+run_id, sample_size).
#                            model_drift is created by 11 and altered by 27; the
#                            ALTER is not reliably applied at initdb (11/27
#                            split), and without it the runner's write_drift
#                            fails on the missing 'kind' column. Runs before
#                            seed-ml (7.5).
for mig in 22_project.sh 26_artifact_project.sh 28_dataset_project_link.sh 29_project_review.sh 27_ml_monitoring.sh 33_workshop_group_flows.sh; do
  echo "▸ [ops-migration] ${mig}"
  if kubectl -n "$NS" exec -i postgres-vault-0 -- \
        env POSTGRES_USER=vault_admin OPS_DB_NAME="$OPS_DB" PGPASSWORD="$PGADMIN_PW" \
        bash -s < "$REPO/3_storage/postgres/init/${mig}" >/dev/null 2>&1; then
    echo "  ok: ${mig} applied"
  else
    echo "  FAIL: ${mig} failed"
    FAILED=1
  fi
done

# group invite/request sample content (after the 33 tables and 15 groups exist) ─
echo "▸ [workshop-group-flows] 34_workshop_group_flows_seed.sh"
if kubectl -n "$NS" exec -i postgres-vault-0 -- \
      env POSTGRES_USER=vault_admin OPS_DB_NAME="$OPS_DB" PGPASSWORD="$PGADMIN_PW" \
      bash -s < "$REPO/bundle/seed-pack/app/workshop-groups/34_workshop_group_flows_seed.sh" >/dev/null 2>&1; then
  echo "  ok: group invite/request demo content seeded"
else
  echo "  ! group-flow demo seed skipped (non-fatal)"
fi

if [ "${FAILED:-0}" -ne 0 ]; then
  echo "== app/security seeding FAILED (see FAIL lines above) =="
  exit 1
fi
echo "== app/security seeding complete =="
