#!/usr/bin/env bash
# Destroy and rebuild the mBrana replica from scratch.
set -euo pipefail
cd "$(dirname "$0")/.."

read -p "Delete mBrana SQL Server volume and rebuild? [y/N] " yn
case "$yn" in [Yy]*) ;; *) exit 1 ;; esac

docker compose down -v || true
DATA_DIR=$(grep -E '^MBRANA_DATA_DIR=' .env 2>/dev/null | cut -d= -f2-)
DATA_DIR="${DATA_DIR:-./data}"
[ -d "$DATA_DIR" ] && sudo rm -rf "$DATA_DIR"
sudo mkdir -p "$DATA_DIR"
sudo chown -R 10001:0 "$DATA_DIR"

docker compose up -d
for _ in $(seq 1 40); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' mbrana-sqlserver 2>/dev/null)" = "healthy" ] && break
  sleep 3
done

bash scripts/restore.sh
python3 scripts/synthesize.py

echo "Done. Verify: bash scripts/verify.sh"
