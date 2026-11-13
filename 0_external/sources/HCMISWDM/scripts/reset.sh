#!/usr/bin/env bash
# Destroy and rebuild the HCMIS WDM replica.
set -euo pipefail
cd "$(dirname "$0")/.."

read -p "This will DELETE the HCMIS WDM data volume. Continue? [y/N] " yn
case "$yn" in [Yy]*) ;; *) echo "Aborted."; exit 1 ;; esac

echo "==> Stopping containers..."
docker compose down -v || true

DATA_DIR=$(grep -E '^HCMISWDM_DATA_DIR=' .env 2>/dev/null | cut -d= -f2-)
DATA_DIR="${DATA_DIR:-./data}"
if [ -d "$DATA_DIR" ]; then
  echo "==> Removing $DATA_DIR (requires sudo)..."
  sudo rm -rf "$DATA_DIR"
  mkdir -p "$DATA_DIR"
  # SQL Server 2025 needs the data dir to be writable by the mssql user (uid 10001).
  sudo chown -R 10001:0 "$DATA_DIR"
fi

echo "==> Starting fresh..."
docker compose up -d
echo "==> Waiting for SQL Server healthy..."
for _ in $(seq 1 60); do
  if [ "$(docker inspect -f '{{.State.Health.Status}}' hcmiswdm-sqlserver 2>/dev/null)" = "healthy" ]; then
    break
  fi
  sleep 3
done

echo "==> Running restore..."
bash scripts/restore.sh

echo "==> Running base synthesis (Vaccine.* schema)..."
python3 scripts/synthesize.py

echo "==> Running analytics-layer synthesis (Dim.*, Commodity.*, Fact.*)..."
python3 scripts/synthesize_analytics.py

echo "==> Running dispatch synthesis (EPSS hub->facility movement plan)..."
python3 scripts/synthesize_dispatch.py

echo "Done. Verify: bash scripts/verify.sh"
