#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
test -f .env.runtime || { echo 'Missing deploy/.env.runtime' >&2; exit 1; }
test -f .env || { echo 'Missing deploy/.env build settings' >&2; exit 1; }
mkdir -p secrets
touch secrets/pip_extra_index_url
chmod 600 .env .env.runtime secrets/pip_extra_index_url

docker compose --env-file .env -f compose.yml build
docker compose --env-file .env -f compose.yml up -d
docker compose --env-file .env -f compose.yml ps
curl --fail --silent http://127.0.0.1:8082/api/health/ready
printf '\n'
