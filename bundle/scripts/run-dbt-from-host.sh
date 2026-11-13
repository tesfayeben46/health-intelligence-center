#!/usr/bin/env bash
# run-dbt-from-host.sh: run a pipeline image (staging-dbt | vault-dbt | marts-dbt | processed-zone)
# on the host with docker against the running cluster through kubectl port-forwards.
# Same images and env contract as the Jobs in 1_infrastructure/k8s/base/dbt-jobs.yaml;
# used when a rebuilt image is not yet imported into k3s (the import needs root).
#   bundle/scripts/run-dbt-from-host.sh staging build --target dev --select elmis_hist
#   bundle/scripts/run-dbt-from-host.sh vault   build --target dev --select tag:elmis
#   bundle/scripts/run-dbt-from-host.sh marts   build --target dev --select supply_chain mdm
#   bundle/scripts/run-dbt-from-host.sh processed-zone
# Credentials are read from the cluster Secrets into the container env; never printed.
set -euo pipefail
NS=${NS:-nhdw}; PROJECT=$1; shift
SW="http://seaweedfs-s3.$NS.svc.cluster.local:8333"
sec() { kubectl -n "$NS" get secret "$1" -o jsonpath="{.data.$2}" | base64 -d; }
PFS=()
pf() { kubectl -n "$NS" port-forward "svc/$1" "$2:$3" >/dev/null 2>&1 & PFS+=($!); for _ in $(seq 1 20); do (echo > /dev/tcp/127.0.0.1/$2) 2>/dev/null && return 0; sleep 1; done; echo "port-forward $1 failed" >&2; exit 1; }
trap 'for p in "${PFS[@]:-}"; do kill $p 2>/dev/null || true; done' EXIT
case "$PROJECT" in
  staging|processed-zone)
    pf clickhouse 18123 8123
    ENV=(-e CH_HOST=127.0.0.1 -e CH_PORT=18123 -e CH_USER=nhdw -e "CH_PASSWORD=$(sec clickhouse-secrets PASSWORD)" -e CH_SCHEMA=nhdw_staging -e "LAKE_BASE=$SW" -e BOTO_EXPERIMENTAL__NO_EMPTY_CONTINUE=true)
    if [ "$PROJECT" = processed-zone ]; then
      exec docker run --rm --network host "${ENV[@]}" --entrypoint python nhdw/staging-dbt:1.0.0 /dbt/scripts/build_processed_zone.py "$@"
    fi
    exec docker run --rm --network host "${ENV[@]}" nhdw/staging-dbt:1.0.0 "$@" ;;
  vault|marts)
    pf postgres-vault 15432 5432
    ENV=(-e PG_HOST=127.0.0.1 -e PG_PORT=15432 -e PG_USER=vault_writer -e "PG_PASSWORD=$(sec postgres-vault-secrets VAULT_WRITER_PASSWORD)" -e PG_DATABASE=nhdw_vault)
    exec docker run --rm --network host "${ENV[@]}" "nhdw/${PROJECT}-dbt:1.0.0" "$@" ;;
  *) echo "unknown project $PROJECT" >&2; exit 2 ;;
esac
