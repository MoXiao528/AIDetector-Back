#!/usr/bin/env bash
set -euo pipefail

app_user="${APP_DB_USER:-}"
app_password="${APP_DB_PASSWORD:-}"
app_db="${APP_DB_NAME:-${POSTGRES_DB:-AIDetector}}"
admin_user="${POSTGRES_USER:-postgres}"

if [[ -z "$app_user" || "$app_user" == "$admin_user" ]]; then
  echo "[AIDetector][db-init] runtime user bootstrap skipped"
  exit 0
fi

if [[ -z "$app_password" ]]; then
  echo "[AIDetector][db-init] APP_DB_PASSWORD is empty, runtime user bootstrap skipped"
  exit 0
fi

echo "[AIDetector][db-init] ensuring runtime user exists: $app_user"

psql -v ON_ERROR_STOP=1 --username "$admin_user" --dbname postgres \
  -v app_user="$app_user" \
  -v app_password="$app_password" \
  -v app_db="$app_db" <<'SQL'
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
