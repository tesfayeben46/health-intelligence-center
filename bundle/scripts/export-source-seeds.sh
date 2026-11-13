#!/usr/bin/env bash
# Export every nhdw_landing.stg_<system>__<table> as a CSV into
# bundle/seeds/sources/<system>/<table>.csv. These are the
# human-readable seeds that bundle/scripts/bootstrap.sh replays through the
# dbt pipeline.
#
# Usage:
#   ./export-source-seeds.sh                # from k3d (default)
#   ./export-source-seeds.sh --from=compose # from docker-compose
#
# Layout:
#   bundle/seeds/sources/
# ├ dhis2/{table}.csv
# ├ ethioemr/{table}.csv
# ├ hcmiswdm/{table}.csv
# ├ mbrana/{table}.csv
# └ mfr/{table}.csv

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT_DIR="${REPO_ROOT}/bundle/seeds/sources"

FROM="k3d"
for arg in "$@"; do
    case "$arg" in
        --from=k3d|--from=compose) FROM="${arg#--from=}";;
        *) echo "unknown arg: $arg" >&2; exit 2;;
    esac
done

case "$FROM" in
    k3d)     PG_EXEC() { kubectl -n nhdw exec -i postgres-vault-0 -- "$@"; } ;;
    compose) PG_EXEC() { docker exec -i postgres-vault "$@"; } ;;
esac

step() { echo; echo "── $* ──"; }

# Discover landing tables and group them by system prefix.
TABLES=$(PG_EXEC psql -U vault_admin -d nhdw_vault -tAc \
    "SELECT tablename FROM pg_tables WHERE schemaname='nhdw_landing'
     AND tablename LIKE 'stg_%' ORDER BY 1" | tr -d '\r')

count=0
total_bytes=0
for tbl in $TABLES; do
    [ -z "$tbl" ] && continue
    # Parse stg_<system>__<table>.
    rest="${tbl#stg_}"
    system="${rest%%__*}"
    table="${rest#*__}"
    out_dir="${OUT_DIR}/${system}"
    out="${out_dir}/${table}.csv"
    mkdir -p "$out_dir"
    PG_EXEC psql -U vault_admin -d nhdw_vault -c \
        "\copy (SELECT * FROM nhdw_landing.${tbl}) TO STDOUT WITH CSV HEADER" \
        > "$out" 2>/dev/null
    bytes=$(stat -c%s "$out" 2>/dev/null || echo 0)
    total_bytes=$((total_bytes + bytes))
    count=$((count + 1))
    if [ "$bytes" -gt 10240 ]; then
        echo "  ok: $system/$table.csv  $(du -h "$out" | cut -f1)"
    else
        echo "  · $system/$table.csv  ($(numfmt --to=iec $bytes 2>/dev/null || echo "$bytes B"))"
    fi
done

step "DONE: ${count} CSVs · $(numfmt --to=iec $total_bytes 2>/dev/null || echo "$total_bytes B") total"
echo
echo "Layout:"
find "$OUT_DIR" -maxdepth 2 -type d | sed "s|${OUT_DIR}/*|  |" | grep -v '^  $'
echo
echo "Replay with:"
echo "  bundle/scripts/bootstrap.sh (or the console Rebuild button)"
