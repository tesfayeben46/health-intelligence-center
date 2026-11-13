#!/usr/bin/env bash
# run-history-from-host.sh: run the `history` lane with the same image the k8s Job uses,
# from the VM host (no root needed): the container joins the host network, reaches the
# eLMIS replica on 127.0.0.1:5434 and the lake through a kubectl port-forward to
# seaweedfs-s3. Credentials come from the source .env and the cluster Secret; nothing is printed.
# Use when the image is not yet imported into k3s; otherwise run Job `elmis-hist-ingest`.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"; NS=${NS:-nhdw}; PF_PORT=${PF_PORT:-19010}
set -a; . "$REPO/0_external/sources/OpenLMIS/.env"; set +a
AK=$(kubectl -n "$NS" get secret seaweedfs-secrets -o jsonpath='{.data.ADMIN_ACCESS_KEY}' | base64 -d)
SK=$(kubectl -n "$NS" get secret seaweedfs-secrets -o jsonpath='{.data.ADMIN_SECRET_KEY}' | base64 -d)
kubectl -n "$NS" port-forward svc/seaweedfs-s3 "$PF_PORT:8333" >/dev/null 2>&1 & PF=$!; trap 'kill $PF 2>/dev/null' EXIT
for _ in $(seq 1 15); do curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$PF_PORT/" && break; sleep 2; done
docker run --rm --network host \
  -e OPENLMIS_HIST_HOST=127.0.0.1 -e OPENLMIS_HIST_PORT="${ELMIS_DB_PORT:-5434}" -e OPENLMIS_HIST_DB="${ELMIS_DB_NAME:-elmis}" \
  -e OPENLMIS_HIST_USER="$ELMIS_READER_USER" -e OPENLMIS_HIST_PASSWORD="$ELMIS_READER_PASSWORD" \
  -e MINIO_ENDPOINT="http://127.0.0.1:$PF_PORT" -e MINIO_ACCESS_KEY="$AK" -e MINIO_SECRET_KEY="$SK" \
  -e MINIO_RAW_BUCKET=nhdw-raw -e MINIO_REGION=et-addis-1 -e BOTO_EXPERIMENTAL__NO_EMPTY_CONTINUE=true \
  -e DLT_PIPELINE_NAME=openlmis nhdw/openlmis-ingestor:1.0.0 history "$@"
