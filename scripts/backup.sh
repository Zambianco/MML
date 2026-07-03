#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"

cd "$ROOT_DIR"
mkdir -p backups
set -a
source .env
set +a

docker compose exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > "backups/${POSTGRES_DB}_${STAMP}.sql"
