#!/usr/bin/env bash
# restore-state.sh: the unpack half of pack.sh. Restores bundle/state/ (exported
# runtime state) into the freshly booted cluster before the bundle/seeds YAML is
# applied on top (managed_by provenance keeps the YAML authoritative for what it
# declares; other runtime rows come from these dumps).
#
# Optional: a factory bundle ships no bundle/state/, so this script does nothing
# and the boot builds the system from seeds alone.
# Order: object-store objects before the DB dumps that reference them (MLflow
# registry rows and ops_artifact rows carry object URIs).
#
# bootstrap.sh calls it after the platform wait and before the data and app seeds.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NS=nhdw
STATE="$REPO/bundle/state"
export PATH="$HOME/.local/bin:$PATH"

[ -d "$STATE" ] || { echo "  (no bundle/state: factory bundle, nothing to restore)"; exit 0; }

echo "== restore-state: applying packed runtime state =="

# 1. object-store artifacts first (DB rows reference these objects)
# Restored into SeaweedFS via bundle/scripts/lib/objstore.sh (bundled host-side mc
# and a port-forward). The packed directory keeps its historical name,
# state/minio/nhdw-artifacts.
if [ -d "$STATE/minio/nhdw-artifacts" ]; then
  . "$REPO/bundle/scripts/lib/objstore.sh"
  if objstore_mc && objstore_creds; then
    objstore_pf 19000
    objstore_alias \
      && { "$MC" --config-dir "$OS_MC_CFG" mb --ignore-existing L/nhdw-artifacts >/dev/null 2>&1
           "$MC" --config-dir "$OS_MC_CFG" mirror --quiet --overwrite "$STATE/minio/nhdw-artifacts" L/nhdw-artifacts; } \
      && echo "  ok: artifacts restored into $OBJSTORE_SVC" || echo "  ! artifact restore failed" >&2
    objstore_pf_stop
  else echo "  ! mc or object-store creds unavailable, artifact restore SKIPPED" >&2; fi
fi

# 2. ops databases: fast boot only (a warm-start cache, not the source of truth)
# The default cold boot does not restore these. It seeds nhdw_ops and nhdw_mlflow
# from files and Jobs (the seed-app-security YAML loaders, seed-ml train and
# predict-batch, seed-projects, seed-graduation) on top of the current initdb
# schema: application state is seeded from files, never restored.
# `pg_restore --clean` of a packed dump reverts the schema to the dump's shape,
# silently dropping any column or table added since it was packed. So this runs
# only under `bootstrap.sh --fast` (NHDW_FAST_BOOT=1), like the derived cache
# below, where restoring the packed rows skips the seed and training work.
PGPW=$(kubectl -n "$NS" get secret postgres-vault-secrets -o jsonpath='{.data.POSTGRES_PASSWORD}' 2>/dev/null | base64 -d)
if [ "${NHDW_FAST_BOOT:-0}" = "1" ]; then
  for db in nhdw_ops nhdw_mlflow; do
    dump="$STATE/ops/${db}.dump"
    [ -f "$dump" ] || continue
    kubectl -n "$NS" exec -i postgres-vault-0 -- env PGPASSWORD="$PGPW" \
      pg_restore -U vault_admin -d "$db" --clean --if-exists --no-owner < "$dump" >/dev/null 2>&1
    echo "  FAST BOOT: ${db} restored from pack ($(du -h "$dump" | cut -f1))"
  done
else
  echo "  default boot: nhdw_ops / nhdw_mlflow seeded FRESH from files (packed dumps skipped; --fast to restore)"
fi

# 3. optional derived fast-boot cache, only under NHDW_FAST_BOOT
# The default boot never uses this; the pipeline derives the marts from source.
# `bootstrap.sh --fast` sets NHDW_FAST_BOOT=1 and, if the snapshot is present,
# restores it here so the pipeline stage can be skipped. Exports FAST_DERIVED=1
# on success so bootstrap knows the marts are already in place.
if [ "${NHDW_FAST_BOOT:-0}" = "1" ] && [ -f "$STATE/derived/derived.dump" ]; then
  echo "  FAST BOOT: restoring derived snapshot (marts/vault/landing): a CACHE, not truth"
  if kubectl -n "$NS" exec -i postgres-vault-0 -- env PGPASSWORD="$PGPW" \
       pg_restore -U vault_admin -d nhdw_vault --clean --if-exists --no-owner < "$STATE/derived/derived.dump" >/dev/null 2>&1; then
    echo "  ok: derived snapshot restored ($(du -h "$STATE/derived/derived.dump" | cut -f1)): pipeline will be SKIPPED"
    echo "FAST_DERIVED=1" > /tmp/nhdw-fast-boot.flag
  else
    echo "  ! derived restore failed: bootstrap will fall back to the full pipeline" >&2
  fi
fi

echo "== restore-state complete =="
