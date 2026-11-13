#!/usr/bin/env bash
# Destroy and rebuild the EthioEMR replica from scratch.
# Removes the data volume and any changes made to it.
set -euo pipefail
cd "$(dirname "$0")/.."

read -p "This will DELETE the EthioEMR data volume. Continue? [y/N] " yn
case "$yn" in
  [Yy]*) ;;
  *) echo "Aborted."; exit 1 ;;
esac

echo "==> Stopping containers..."
docker compose down -v || true

# Remove only this replica's data directory.
DATA_DIR=$(grep -E '^ETHIOEMR_DATA_DIR=' .env 2>/dev/null | cut -d= -f2- | tr -d '"')
DATA_DIR="${DATA_DIR:-./data}"

if [ -d "$DATA_DIR" ]; then
  echo "==> Removing $DATA_DIR..."
  sudo rm -rf "$DATA_DIR"
fi

echo "==> Starting fresh..."
docker compose up -d mysql

echo "==> Waiting for MySQL to be healthy..."
for _ in $(seq 1 60); do
  if [ "$(docker inspect -f '{{.State.Health.Status}}' ethioemr-mysql 2>/dev/null)" = "healthy" ]; then
    break
  fi
  sleep 2
done

echo "==> Running restore..."
bash scripts/restore.sh

echo "==> Starting OpenMRS web..."
docker compose up -d openmrs
echo "Done. Wait 2-3 min then run: bash scripts/verify.sh"
