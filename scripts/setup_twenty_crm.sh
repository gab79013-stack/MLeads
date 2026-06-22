#!/usr/bin/env bash
# Install or refresh a self-hosted Twenty CRM instance for 0brix.
#
# Usage:
#   sudo SERVER_URL=http://2.25.162.58:3000 ./scripts/setup_twenty_crm.sh
#
# The script keeps Twenty isolated under /opt/twenty and uses Twenty's
# official Docker Compose bundle. Existing secrets are preserved when present.

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/twenty}"
SERVER_URL="${SERVER_URL:-http://2.25.162.58:3000}"
COMPOSE_URL="${COMPOSE_URL:-https://raw.githubusercontent.com/twentyhq/twenty/refs/heads/main/packages/twenty-docker/docker-compose.yml}"
ENV_EXAMPLE_URL="${ENV_EXAMPLE_URL:-https://raw.githubusercontent.com/twentyhq/twenty/refs/heads/main/packages/twenty-docker/.env.example}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo so Docker volumes and /opt/twenty are writable." >&2
  exit 1
fi

command -v docker >/dev/null 2>&1 || {
  echo "Docker is required before installing Twenty." >&2
  exit 1
}

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required: docker compose version failed." >&2
  exit 1
fi

mkdir -p "${INSTALL_DIR}"
cd "${INSTALL_DIR}"

curl -fsSLo docker-compose.yml "${COMPOSE_URL}"
curl -fsSLo .env.example.remote "${ENV_EXAMPLE_URL}"

existing_value() {
  local key="$1"
  if [[ -f .env ]]; then
    grep -E "^${key}=" .env | tail -n 1 | cut -d= -f2- || true
  fi
}

ENCRYPTION_KEY="$(existing_value ENCRYPTION_KEY)"
PG_DATABASE_PASSWORD="$(existing_value PG_DATABASE_PASSWORD)"
FALLBACK_ENCRYPTION_KEY="$(existing_value FALLBACK_ENCRYPTION_KEY)"

if [[ -z "${ENCRYPTION_KEY}" ]]; then
  ENCRYPTION_KEY="$(openssl rand -base64 32)"
fi

if [[ -z "${PG_DATABASE_PASSWORD}" ]]; then
  PG_DATABASE_PASSWORD="$(openssl rand -base64 24 | tr -d '/+=')"
fi

if [[ -z "${FALLBACK_ENCRYPTION_KEY}" ]]; then
  FALLBACK_ENCRYPTION_KEY="${ENCRYPTION_KEY}"
fi

awk '/^[A-Za-z_][A-Za-z0-9_]*=.*/ { print }' .env.example.remote > .env.generated

upsert_env() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" .env.generated; then
    sed -i "s#^${key}=.*#${key}=${value}#" .env.generated
  else
    printf '%s=%s\n' "${key}" "${value}" >> .env.generated
  fi
}

upsert_env ENCRYPTION_KEY "${ENCRYPTION_KEY}"
upsert_env FALLBACK_ENCRYPTION_KEY "${FALLBACK_ENCRYPTION_KEY}"
upsert_env PG_DATABASE_PASSWORD "${PG_DATABASE_PASSWORD}"
upsert_env SERVER_URL "${SERVER_URL}"

mv .env.generated .env
chmod 600 .env

docker compose up -d

echo "Twenty CRM requested at ${SERVER_URL}"
echo "Set TWENTY_URL=${SERVER_URL} in the 0brix web environment."
docker compose ps
