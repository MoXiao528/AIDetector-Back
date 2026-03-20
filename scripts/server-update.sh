#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[AIDetector] root: $ROOT_DIR"

if ! command -v git >/dev/null 2>&1; then
  echo "[AIDetector] git not found"
  exit 1
fi

if [[ ! -d .git ]]; then
  echo "[AIDetector] current directory is not a git repository"
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "[AIDetector] .env not found"
  echo "[AIDetector] run: cp .env.example .env && edit .env first"
  exit 1
fi

echo "[AIDetector] pulling latest code..."
git pull --ff-only

echo "[AIDetector] rebuilding containers..."
docker compose up -d --build

echo "[AIDetector] running migrations..."
docker compose exec api alembic upgrade head

echo "[AIDetector] checking container status..."
docker compose ps

if command -v curl >/dev/null 2>&1; then
  echo "[AIDetector] health:"
  curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/health
  echo

  echo "[AIDetector] readiness:"
  curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/ready
  echo
else
  echo "[AIDetector] curl not found, skipped health checks"
fi

echo "[AIDetector] server-update finished"
