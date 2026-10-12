#!/usr/bin/env bash
# Restore a JobHub database backup. Usage: scripts/restore.sh <backup-file-on-host | backup-name-in-volume>
set -euo pipefail
cd "$(dirname "$0")/.."
if [ $# -ne 1 ]; then
  echo "Usage: $0 <path/to/jobhub-YYYYMMDD-HHMMSS.db | jobhub-YYYYMMDD-HHMMSS.db>" >&2
  exit 1
fi
target="$1"
read -r -p "This replaces the live JobHub database (a safety copy is made first). Continue? [y/N] " ok
[ "${ok,,}" = "y" ] || { echo "Aborted"; exit 1; }
docker compose stop jobhub
if [ -f "$target" ]; then
  docker compose run --rm --no-deps -v "$(realpath "$target")":/restore/backup.db:ro \
    jobhub python -m app.cli restore /restore/backup.db --yes
else
  docker compose run --rm --no-deps jobhub python -m app.cli restore "$target" --yes
fi
docker compose up -d
echo "Restore complete. Check: docker compose ps"
