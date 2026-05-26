from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_quota_usage_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20240916_0012_create_quota_usage_table.py"
    )
    spec = importlib.util.spec_from_file_location("quota_usage_migration", migration_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_quota_usage_backfill_matches_runtime_quota_semantics():
    migration = _load_quota_usage_migration()
    sql = migration.QUOTA_USAGE_BACKFILL_SQL

    assert "(created_at AT TIME ZONE 'UTC')::date" in sql
    assert "chars_used > 0" in sql
    assert "title IS NULL" in sql
    assert "COALESCE(meta_json, '{}'::jsonb) ? 'method'" in sql
    assert "CAST(created_at AS DATE)" not in sql
