#!/usr/bin/env bash
# Bring up the dev stack, wait until healthy, migrate the dev and test databases.
set -euo pipefail
cd "$(dirname "$0")/.."
DC=(docker compose -f deploy/dev/compose.yaml)

wait_for() { # name timeout_s cmd...
  local name=$1 timeout=$2 i=0; shift 2
  until "$@" >/dev/null 2>&1; do
    i=$((i + 2))
    if ((i >= timeout)); then echo "timeout waiting for $name" >&2; exit 1; fi
    sleep 2
  done
  echo "ok: $name"
}

"${DC[@]}" up -d
wait_for postgres 60 "${DC[@]}" exec -T postgres pg_isready -U roracle
wait_for minio 60 curl -sf http://localhost:9000/minio/health/live
wait_for elasticsearch 120 curl -sf 'http://localhost:9200/_cluster/health?wait_for_status=yellow&timeout=1s'
wait_for keycloak 180 curl -sf http://localhost:8080/realms/roracle
wait_for docling 180 curl -sf http://localhost:5001/health

set -a; source backend/.env.example; set +a
cd backend
for db in roracle roracle_test; do
  ROR_DATABASE_URL="${ROR_DATABASE_URL%/*}/$db" uv run alembic upgrade head
  echo "ok: migrated $db"
done
