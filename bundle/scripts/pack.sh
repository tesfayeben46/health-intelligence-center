#!/usr/bin/env bash
# pack.sh: export the live system's runtime state back into the bundle, so
# that unpack and rebuild reproduce every change applied to the system.
#
# The pack/unpack contract (each exporter is the inverse of one boot step):
#   · Pipeline-derived data (lake, ClickHouse, landing, vault, marts) is never
#     packed; the boot re-derives it from sources through the pipeline.
#   · Source state is packed as each source's native restore artifact, so
#     runtime changes to sources survive. The boot restores the artifact and
#     seeds only if empty (sources-up.sh), so a packed artifact wins and a
#     factory artifact still self-seeds.
#   · Non-derivable app state (ops DBs, MLflow registry, object-store
#     artifacts) goes to bundle/state/, machine-written and optional (a factory
#     bundle has none). The boot restores state/ first, then applies the
#     editable bundle seed YAML on top (managed_by provenance keeps the YAML
#     authoritative for what it declares).
#     This one dump pair covers the whole ML tier and tokens: ops_ml
#     (predictions, runs, model_deployment, drift, performance),
#     ops_perm.api_token and ops_governance.access_request live in nhdw_ops;
#     the MLflow registry lives in nhdw_mlflow; model artifacts, frozen
#     ml-packages and reference profiles live under
#     nhdw-artifacts/{mlflow,ml-packages}/ (the mc mirror below). Issued API
#     keys, scheduled models and trained weights therefore survive a pack and
#     unpack. On unpack, seed-ml is convergent (skips training when a model is
#     already @champion), so nothing is retrained.
#   · Re-derived, not packed: the vault's accumulated insert-only history.
#     Unpack rebuilds the vault from the restored source snapshots, so history
#     depth resets to the restore point. Also nhdw_airflow (scheduler and
#     DAG-run history, volatile operational metadata; which models are
#     scheduled lives in ops_ml.model_deployment, which is packed).
#   · Keycloak: not exported; there are no runtime Keycloak mutations to keep.
#   · MFR: not exported; it reseeds idempotently from the entity model, so
#     runtime facility additions are not preserved.
#
# Usage:  bundle/scripts/pack.sh              # refresh artifacts + state in place
#         bundle/scripts/pack.sh --zip out.zip  # ...then zip the whole bundle tree
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NS=nhdw
STATE="$REPO/bundle/state"
SRC="$REPO/0_external/sources"
export PATH="$HOME/.local/bin:$PATH"

ZIP_OUT=""
WITH_DERIVED=0
for a in "$@"; do case "$a" in
  --zip) ZIP_OUT="__next__";;
  --with-derived) WITH_DERIVED=1;;
  *) [ "$ZIP_OUT" = "__next__" ] && ZIP_OUT="$a";;
esac; done

step() { echo; echo "▸ $*"; }
ok()   { echo "  ok: $*"; }
warn() { echo "  ! $*" >&2; }

echo "== pack: exporting live system state into the bundle =="
mkdir -p "$STATE/ops" "$STATE/minio"

# 0. sync the served docs into the bundle (keeps bundle/ self-contained)
step "docs -> bundle/docs (sync developer docs/ into the self-contained bundle)"
bash "$REPO/bundle/scripts/sync-docs.sh" 2>&1 | sed 's/^/  /' || warn "sync-docs failed"

# 1. sources -> native restore artifacts
step "EthioEMR -> mysqldump (full openmrs, incl. runtime changes)"
if docker exec ethioemr-mysql mysqldump -uroot -p"${ETHIOEMR_MYSQL_ROOT_PASSWORD:-changeme-ethioemr-root}" --set-gtid-purged=OFF \
     --single-transaction --routines openmrs > "$SRC/EthioEMR/sample-data/clean_openmrs.sql" 2>/dev/null; then
  ok "clean_openmrs.sql refreshed ($(du -h "$SRC/EthioEMR/sample-data/clean_openmrs.sql" | cut -f1))"
else warn "EthioEMR export failed (container down?)"; fi

step "HCMISWDM + mBrana -> BACKUP DATABASE (.baks)"
SA="${HCMISWDM_SA_PASSWORD:-ChangeMe-MUST-be-strong-Aa1!}"
# The boot's restore `docker cp` leaves /var/opt/mssql/backup root-owned while
# SQL Server runs as mssql, so BACKUP fails with OS error 5 (silently, because
# stderr is discarded below). Fix ownership first.
docker exec -u root hcmiswdm-sqlserver sh -c \
  'mkdir -p /var/opt/mssql/backup && chown -R mssql:root /var/opt/mssql/backup' 2>/dev/null
if docker exec hcmiswdm-sqlserver /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$SA" -C -Q \
     "BACKUP DATABASE [HCMISWDM] TO DISK='/var/opt/mssql/backup/pack_wdm.bak' WITH INIT, COMPRESSION; BACKUP DATABASE [HCMISWCloud] TO DISK='/var/opt/mssql/backup/pack_cloud.bak' WITH INIT, COMPRESSION;" >/dev/null 2>&1; then
  docker cp hcmiswdm-sqlserver:/var/opt/mssql/backup/pack_wdm.bak   "$SRC/HCMISWDM/sample-data/HCMISWDM.bak"
  docker cp hcmiswdm-sqlserver:/var/opt/mssql/backup/pack_cloud.bak "$SRC/mBrana/sample-data/HCMISWCloud.bak"
  ok "HCMISWDM.bak + HCMISWCloud.bak refreshed"
else warn "SQL Server export failed (container down?)"; fi

step "DHIS2 -> pg_dump (custom format)"
if docker exec dhis2-db pg_dump -U dhis -d dhis2 -Fc -f /tmp/dhis2_seeded.pgdump 2>/dev/null; then
  docker cp dhis2-db:/tmp/dhis2_seeded.pgdump "$SRC/DHIS2/sample-data/dhis2_seeded.pgdump"
  docker exec dhis2-db rm -f /tmp/dhis2_seeded.pgdump
  ok "dhis2_seeded.pgdump refreshed ($(du -h "$SRC/DHIS2/sample-data/dhis2_seeded.pgdump" | cut -f1))"
else warn "DHIS2 export failed (container down?)"; fi

# 2. non-derivable app state -> bundle/state
step "ops databases -> bundle/state/ops (nhdw_ops + nhdw_mlflow, history included)"
PGPW=$(kubectl -n "$NS" get secret postgres-vault-secrets -o jsonpath='{.data.POSTGRES_PASSWORD}' 2>/dev/null | base64 -d)
if [ -n "${PGPW:-}" ]; then
  for db in nhdw_ops nhdw_mlflow; do
    kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGPW" \
      pg_dump -U vault_admin -d "$db" -Fc > "$STATE/ops/${db}.dump" 2>/dev/null \
      && ok "${db}.dump ($(du -h "$STATE/ops/${db}.dump" | cut -f1))" \
      || warn "${db} dump failed"
  done
else warn "cluster not reachable: ops dumps skipped"; fi

step "object-store non-derivable artifacts -> bundle/state/minio (lake excluded: re-derived)"
# The object store is SeaweedFS; access goes through bundle/scripts/lib/objstore.sh
# (bundled host-side mc + port-forward). The state/minio directory name is
# historical and kept so existing packed bundles restore unchanged.
. "$REPO/bundle/scripts/lib/objstore.sh"
if objstore_mc && objstore_creds; then
  objstore_pf 19000
  rm -rf "$STATE/minio/nhdw-artifacts" && mkdir -p "$STATE/minio/nhdw-artifacts"
  objstore_alias \
    && "$MC" --config-dir "$OS_MC_CFG" mirror --quiet L/nhdw-artifacts "$STATE/minio/nhdw-artifacts" \
    && ok "state/minio/nhdw-artifacts mirrored from $OBJSTORE_SVC ($(du -sh "$STATE/minio/nhdw-artifacts" | cut -f1))" \
    || warn "artifact mirror failed"
  objstore_pf_stop
else warn "object-store creds or mc unavailable: artifact mirror skipped"; fi

# 2b. optional derived snapshot -> bundle/state/derived (a cache)
# The marts, vault and landing are normally never packed: the boot re-derives
# them from sources through the pipeline, and every cold boot tests that path.
# This optional snapshot is a fast-boot cache: `bootstrap.sh --fast` restores it
# and skips the pipeline. A normal `bootstrap.sh` still re-derives from source,
# so the cache never becomes a second source of truth. Produced only on request.
if [ "$WITH_DERIVED" = 1 ]; then
  step "derived snapshot -> bundle/state/derived (fast-boot CACHE; --fast only)"
  if [ -n "${PGPW:-}" ]; then
    mkdir -p "$STATE/derived"
    # bi_* marts, the vault and landing: everything the pipeline derives in Postgres.
    SCHEMAS=$(kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGPW" \
      psql -U vault_admin -d nhdw_vault -At -c \
      "SELECT string_agg('-n '||quote_ident(nspname),' ') FROM pg_namespace \
       WHERE nspname LIKE 'bi\_%' OR nspname LIKE 'nhdw\_vault%' OR nspname='nhdw_landing'" 2>/dev/null)
    if kubectl -n "$NS" exec postgres-vault-0 -- env PGPASSWORD="$PGPW" \
         sh -c "pg_dump -U vault_admin -d nhdw_vault -Fc $SCHEMAS" > "$STATE/derived/derived.dump" 2>/dev/null; then
      ok "derived.dump ($(du -h "$STATE/derived/derived.dump" | cut -f1)): restore with bootstrap.sh --fast"
    else warn "derived snapshot failed"; fi
  else warn "cluster not reachable: derived snapshot skipped"; fi
fi

# 3. bundle machinery refresh
step "image tars (only missing ones are saved)"
bash "$REPO/bundle/scripts/cache-images.sh" --save-only 2>&1 | grep -E "^save|sanitiz" | sed 's/^/  /' || true
ok "image cache refreshed"

date -u +"packed_at: %Y-%m-%dT%H:%M:%SZ" > "$STATE/PACKED.txt"
echo "state layout:" >> "$STATE/PACKED.txt"; ls -R "$STATE" >> "$STATE/PACKED.txt" 2>/dev/null

# 4. optional zip
if [ -n "$ZIP_OUT" ] && [ "$ZIP_OUT" != "__next__" ]; then
  step "zipping bundle -> $ZIP_OUT"
  ( cd "$REPO" && zip -qr "$ZIP_OUT" . -x "_archive/*" -x ".git/*" ) && ok "$ZIP_OUT"
fi

echo; echo "== pack complete: unpack test: teardown --wipe-sources, then Rebuild =="
