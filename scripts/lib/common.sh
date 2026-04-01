#!/usr/bin/env bash
set -euo pipefail

require_command() {
  local command_name="$1"
  local error_message="$2"

  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "$error_message"
    exit 1
  fi
}

require_docker_compose() {
  require_command docker "[AIDetector] docker not found"

  if ! docker compose version >/dev/null 2>&1; then
    echo "[AIDetector] docker compose plugin not found"
    exit 1
  fi
}

require_env_file() {
  if [[ ! -f "$ROOT_DIR/.env" ]]; then
    echo "[AIDetector] .env not found"
    echo "[AIDetector] run: cp .env.example .env && edit .env first"
    exit 1
  fi
}

require_ops_env_file() {
  if [[ ! -f "$ROOT_DIR/.env.ops" ]]; then
    echo "[AIDetector] .env.ops not found"
    echo "[AIDetector] run: cp .env.ops.example .env.ops && edit .env.ops first"
    exit 1
  fi
}

load_env_file() {
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a

  ENVIRONMENT="${ENVIRONMENT:-development}"
  APP_DB_USER="${POSTGRES_USER:-aidetector_app}"
  APP_DB_PASSWORD="${POSTGRES_PASSWORD:-}"
  APP_DB_NAME="${POSTGRES_DB:-AIDetector}"
}

load_ops_env_file() {
  local runtime_user="$APP_DB_USER"
  local runtime_password="$APP_DB_PASSWORD"
  local runtime_db_name="$APP_DB_NAME"

  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env.ops"
  set +a

  DB_ADMIN_USER="${POSTGRES_USER:-postgres}"
  DB_ADMIN_PASSWORD="${POSTGRES_PASSWORD:-}"
  DB_ADMIN_DB="${POSTGRES_DB:-$runtime_db_name}"

  POSTGRES_USER="$runtime_user"
  POSTGRES_PASSWORD="$runtime_password"
  POSTGRES_DB="$runtime_db_name"
}

guard_production_override() {
  if [[ "$ENVIRONMENT" == "production" && -f "$ROOT_DIR/docker-compose.override.yml" ]]; then
    echo "[AIDetector] production environment detected"
    echo "[AIDetector] docker-compose.override.yml exists in the current directory"
    echo "[AIDetector] remove that file before running production commands"
    exit 1
  fi
}

warn_if_runtime_uses_postgres_in_production() {
  if [[ "$ENVIRONMENT" == "production" && "$APP_DB_USER" == "postgres" ]]; then
    echo "[AIDetector] warning: POSTGRES_USER is still postgres in production"
    echo "[AIDetector] runtime traffic should be switched to aidetector_app after migrations"
  fi
}

start_database() {
  echo "[AIDetector] starting database container..."
  docker compose up -d db
}

wait_for_database() {
  local max_attempts=30
  local attempt=1

  echo "[AIDetector] waiting for database..."
  until docker compose exec -T db pg_isready -U "$DB_ADMIN_USER" -d "$DB_ADMIN_DB" >/dev/null 2>&1; do
    if (( attempt >= max_attempts )); then
      echo "[AIDetector] database did not become ready in time"
      exit 1
    fi
    attempt=$((attempt + 1))
    sleep 2
  done
}

ensure_runtime_db_user() {
  if [[ "$APP_DB_USER" == "$DB_ADMIN_USER" ]]; then
    return
  fi

  if [[ -z "$APP_DB_PASSWORD" ]]; then
    echo "[AIDetector] POSTGRES_PASSWORD is empty in .env"
    echo "[AIDetector] runtime database user cannot be created without a password"
    exit 1
  fi

  echo "[AIDetector] ensuring runtime database user exists: $APP_DB_USER"
  docker compose exec -T db psql \
    -v ON_ERROR_STOP=1 \
    -v "app_user=$APP_DB_USER" \
    -v "app_password=$APP_DB_PASSWORD" \
    -v "app_db=$APP_DB_NAME" \
    -U "$DB_ADMIN_USER" \
    -d postgres <<'SQL'
DO $do$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') THEN
    EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password');
  ELSE
    EXECUTE format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'app_user', :'app_password');
  END IF;
END
$do$;
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'app_db', :'app_user') \gexec
\connect :app_db
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'app_user') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', :'app_user') \gexec
SELECT format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', :'app_user') \gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
  current_user,
  :'app_user'
) \gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO %I',
  current_user,
  :'app_user'
) \gexec
SQL
}

build_api_image() {
  echo "[AIDetector] building api image..."
  docker compose build api
}

start_api() {
  echo "[AIDetector] starting api container..."
  docker compose up -d api
}

run_migrations() {
  local runtime_user="$APP_DB_USER"
  local admin_user="$DB_ADMIN_USER"
  local admin_password="${DB_ADMIN_PASSWORD:-}"

  if [[ "$runtime_user" != "$admin_user" ]]; then
    if [[ -z "$admin_password" ]]; then
      if [[ "$ENVIRONMENT" == "production" ]]; then
        echo "[AIDetector] .env.ops with POSTGRES_PASSWORD is required in production when runtime user differs from admin user"
        exit 1
      fi

      echo "[AIDetector] warning: .env.ops admin password is empty, falling back to runtime user for migrations"
      echo "[AIDetector] running migrations with runtime user: $runtime_user"
      docker compose run --rm --no-deps api alembic upgrade head
      return
    fi

    echo "[AIDetector] running migrations with admin user: $admin_user"
    docker compose run --rm --no-deps \
      -e POSTGRES_USER="$admin_user" \
      -e POSTGRES_PASSWORD="$admin_password" \
      api alembic upgrade head
    return
  fi

  echo "[AIDetector] running migrations with runtime user: $runtime_user"
  docker compose run --rm --no-deps api alembic upgrade head
}

print_container_status() {
  echo "[AIDetector] checking container status..."
  docker compose ps
}

run_http_checks() {
  if command -v curl >/dev/null 2>&1; then
    local max_attempts=20
    local attempt=1

    until curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1; do
      if (( attempt >= max_attempts )); then
        echo "[AIDetector] api did not become ready in time"
        return 1
      fi
      attempt=$((attempt + 1))
      sleep 2
    done

    echo "[AIDetector] health:"
    curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/health
    echo

    echo "[AIDetector] readiness:"
    curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/ready
    echo
  else
    echo "[AIDetector] curl not found, skipped health checks"
  fi
}
