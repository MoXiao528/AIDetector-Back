#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib/common.sh"

cd "$ROOT_DIR"

echo "[AIDetector] root: $ROOT_DIR"

require_command git "[AIDetector] git not found"
require_docker_compose

if [[ ! -d .git ]]; then
  echo "[AIDetector] current directory is not a git repository"
  exit 1
fi

require_env_file
require_ops_env_file
load_env_file
load_ops_env_file
guard_production_override
warn_if_runtime_uses_postgres_in_production

echo "[AIDetector] pulling latest code..."
git pull --ff-only

start_database
wait_for_database
ensure_runtime_db_user
build_api_image
run_migrations
start_api
print_container_status
run_http_checks

echo "[AIDetector] server-update finished"
