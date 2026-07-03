#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "uso: $0 caminho/do/backup.sql" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_FILE="$1"

cd "$ROOT_DIR"
set -a
source .env
set +a

docker compose exec -T db psql -U "$POSTGRES_USER" "$POSTGRES_DB" < "$BACKUP_FILE"
