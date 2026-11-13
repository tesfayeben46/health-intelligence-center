#!/usr/bin/env bash
# S3-level backup of every nhdw-* bucket in the object store (SeaweedFS) to a
# timestamped directory with a sha256 manifest.
#
#   BACKUP_ROOT=/mnt/backups bash 3_storage/seaweedfs/ops/backup-objstore.sh
#   -> $BACKUP_ROOT/objstore-<UTC stamp>/{objects/<bucket>/..., manifest/SHA256SUMS, manifest/counts.txt}
#
# Store-agnostic (plain S3 via the bundled mc through a port-forward); the
# OBJSTORE_* overrides in bundle/scripts/lib/objstore.sh select the endpoint.
# Restore = `mc mirror --overwrite objects/<bucket> L/<bucket>` per bucket, or
# 3_storage/seaweedfs/migrate/restore_from_backup.py for the verified path.
# Called by 7_security/scripts/backup.sh; safe to run on its own. Never deletes.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
NS="${NS:-nhdw}"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/nhdw-backups}"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$BACKUP_ROOT/objstore-$STAMP"
mkdir -p "$OUT/objects" "$OUT/manifest"
. "$REPO/bundle/scripts/lib/objstore.sh"

echo "== objstore backup to $OUT (from svc/$OBJSTORE_SVC) =="
objstore_mc || { echo "  ! no mc client; aborting" >&2; exit 1; }
objstore_creds || { echo "  ! cannot read $OBJSTORE_SECRET; aborting" >&2; exit 1; }
objstore_pf 19002
objstore_alias || { echo "  ! mc alias failed" >&2; objstore_pf_stop; exit 1; }

BUCKETS=$("$MC" --config-dir "$OS_MC_CFG" ls L/ 2>/dev/null | awk '{print $NF}' | sed 's#/$##' | grep '^nhdw-' || true)
[ -n "$BUCKETS" ] || { echo "  ! no nhdw-* buckets visible" >&2; objstore_pf_stop; exit 1; }
FAIL=0
for b in $BUCKETS; do
  mkdir -p "$OUT/objects/$b"
  if "$MC" --config-dir "$OS_MC_CFG" mirror --quiet "L/$b" "$OUT/objects/$b" >/dev/null 2>&1; then
    n=$(find "$OUT/objects/$b" -type f | wc -l); echo "  ok $b  $n object(s)"; echo "$b $n" >> "$OUT/manifest/counts.txt"
  else
    echo "  ! $b mirror FAILED" >&2; FAIL=1
  fi
done
objstore_pf_stop
( cd "$OUT/objects" && find . -type f -print0 | sort -z | xargs -0 sha256sum ) > "$OUT/manifest/SHA256SUMS"
echo "  manifest: $(wc -l < "$OUT/manifest/SHA256SUMS") files hashed in $OUT/manifest/SHA256SUMS"
echo "$OUT" > "$BACKUP_ROOT/.latest-objstore"
[ "$FAIL" = 0 ] && echo "== objstore backup OK ==" || { echo "== objstore backup had FAILURES ==" >&2; exit 1; }
