#!/usr/bin/env bash
# Create an online backup inside the volume and copy all backups to ./backups/
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T jobhub python -m app.cli backup --label "${1:-manual}"
mkdir -p ./backups
docker compose cp jobhub:/data/backups/. ./backups/
echo "Backups copied to $(pwd)/backups"
ls -lh ./backups | tail -n 5
