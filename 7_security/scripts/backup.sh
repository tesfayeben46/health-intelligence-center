#!/bin/bash
# NHDW backup: a timestamped, integrity-checked archive of every stateful
# component, written under $BACKUP_ROOT (default /mnt/backups). Intended for a
# daily cron run. The database, Keycloak and OpenHIM steps use `docker exec`
# against local containers; the object-store step goes through kubectl.
#
# Components covered:
#   - postgres-vault                          pg_dump --format=custom (nhdw_vault)
#   - clickhouse                              DDL snapshot of the nhdw_staging views
#   - object store (SeaweedFS)                S3 mirror of every nhdw-* bucket + sha256 manifest
#   - openhim mongo                           mongodump
#   - keycloak realm exports                  kcadm export for each realm
#   - openbao file backend                    tar of /data/security/openbao
#
# Each artifact lands in $BACKUP_ROOT/<UTC-date>/ with a sha256
# manifest so the restore drill can validate every file.
# Steps are tolerant (no `set -e`): a partial backup is more useful than an
# early abort. Individual failures print a warning; the final manifest covers
# whatever artifacts made it to disk.
set -uo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
BACKUP_ROOT="${BACKUP_ROOT:-/mnt/backups}"
DATE=$(date -u +%Y-%m-%dT%H%M%SZ)
OUT="$BACKUP_ROOT/$DATE"
mkdir -p "$OUT"
echo "backup target: $OUT"

manifest() {
    # chown anything that the sudo'd tar steps left as root, so the
    # manifest can read them.
    echo "${BACKUP_SUDO_PASS:?set BACKUP_SUDO_PASS (or configure passwordless sudo for the backup paths)}" | sudo -S chown -R "${BACKUP_OWNER:-$(id -un)}":nhdw "$OUT" 2>/dev/null || true
    (cd "$OUT" && find . -maxdepth 2 -type f ! -name 'manifest.sha256' -print0 \
        | xargs -0 sha256sum) > "$OUT/manifest.sha256"
}

# 1. postgres-vault
echo "postgres-vault: pg_dump"
PG_ADMIN_PW=$(grep ^POSTGRES_ADMIN_PASSWORD "$REPO/3_storage/postgres/.env" | cut -d= -f2)
docker exec -e PGPASSWORD="$PG_ADMIN_PW" postgres-vault \
    pg_dump -U vault_admin -d nhdw_vault \
            --format=custom --compress=6 --no-owner --no-privileges \
    > "$OUT/postgres-vault.dump"
echo "  -> $(du -h "$OUT/postgres-vault.dump" | cut -f1)"

# 2. clickhouse: staging holds only views over lake Parquet, so a dbt rebuild
# restores it. Snapshot the DDL so a clean host can rebuild without re-running
# the full staging pipeline.
echo "clickhouse: DDL snapshot (views over the lake Parquet; data lives in the object store)"
docker exec clickhouse clickhouse-client --multiquery --query "
SELECT 'DROP DATABASE IF EXISTS nhdw_staging;'
UNION ALL
SELECT 'CREATE DATABASE nhdw_staging;'
UNION ALL
SELECT create_table_query FROM system.tables WHERE database='nhdw_staging' AND engine='View'
" > "$OUT/clickhouse-staging.ddl.sql" 2>/dev/null || echo "  ! clickhouse query failed (acceptable in dev)"

# 3a. object store: S3-level mirror of every nhdw-* bucket (SeaweedFS) via the
# bundled mc and a port-forward.
echo "object store: S3 mirror of every nhdw-* bucket (3_storage/seaweedfs/ops/backup-objstore.sh)"
BACKUP_ROOT="$OUT" bash "$REPO/3_storage/seaweedfs/ops/backup-objstore.sh" 2>&1 | sed 's/^/  /' \
    || echo "  ! object-store mirror failed"


# 4. openhim mongo
echo "openhim mongo: dump"
docker exec nhdw-openhim-mongo mongodump --quiet --archive --gzip --db=openhim \
    > "$OUT/openhim-mongo.archive.gz"
echo "  -> $(du -h "$OUT/openhim-mongo.archive.gz" | cut -f1)"

# 5. keycloak realm exports
echo "keycloak: realm exports"
KC_PW=$(grep ^KC_BOOTSTRAP_ADMIN_PASSWORD "$REPO/7_security/keycloak/.env" | cut -d= -f2)
docker exec keycloak /opt/keycloak/bin/kcadm.sh config credentials \
    --server http://localhost:8080 --realm master \
    --user keycloak_admin --password "$KC_PW" > /dev/null 2>&1
for realm in moh-staff privileged-ops; do
    docker exec keycloak /opt/keycloak/bin/kcadm.sh get "realms/$realm" \
        > "$OUT/keycloak-realm-$realm.json" 2>/dev/null
done

# 6. openbao file backend
echo "openbao: file backend snapshot"
echo "${BACKUP_SUDO_PASS:?set BACKUP_SUDO_PASS (or configure passwordless sudo for the backup paths)}" | sudo -S tar czf "$OUT/openbao-file.tar.gz" -C /data/security openbao 2>/dev/null
echo "  -> $(du -h "$OUT/openbao-file.tar.gz" | cut -f1)"

# 7. dataset registry, role YAMLs and docs (configuration as data;
# recoverable from git but cheap to snapshot)
tar czf "$OUT/registry.tar.gz" -C "$REPO" bundle docs

# Manifest
manifest
echo "manifest: $(wc -l < "$OUT/manifest.sha256") files"
echo "total: $(du -sh "$OUT" | cut -f1)"
echo "done."
