#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/scripts/lib/common.sh"

cd "$ROOT_DIR"

echo "[AIDetector] root: $ROOT_DIR"

require_docker_compose
require_env_file
require_ops_env_file
load_env_file
guard_production_override
warn_if_runtime_uses_postgres_in_production

echo "[AIDetector] container status:"
docker compose ps

run_http_checks

echo "[AIDetector] server-check finished"
