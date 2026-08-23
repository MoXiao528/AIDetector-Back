from __future__ import annotations

import importlib.util
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, MetaData, Table, UniqueConstraint
from sqlalchemy.exc import IntegrityError


EXPECTED_COLUMNS = {
    "id",
    "actor_type",
    "actor_id",
    "idempotency_key",
    "request_hash",
    "status",
    "usage_date",
    "reserved_chars",
    "owner_token",
    "lease_expires_at",
    "detection_id",
    "created_at",
    "updated_at",
}


def _load_migration():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20240921_0017_create_detection_requests.py"
    )
    assert migration_path.is_file()
    spec = importlib.util.spec_from_file_location("detection_requests_migration", migration_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _normalized_sql(expression) -> str:
    return " ".join(str(expression).replace('"', "").split()).lower()


def _request(*, actor_id: str, key: str, status: str = "processing", chars: int = 100):
    from app.models.detection_request import DetectionRequest

    return DetectionRequest(
        actor_type="user",
        actor_id=actor_id,
        idempotency_key=key,
        request_hash="a" * 64,
        status=status,
        usage_date=date.today(),
        reserved_chars=chars,
        owner_token="b" * 36,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )


def test_detection_request_model_declares_idempotency_and_lease_constraints():
    from app.models.detection_request import DetectionRequest

    table = DetectionRequest.__table__
    assert set(table.columns.keys()) == EXPECTED_COLUMNS
    assert table.c.actor_type.type.length == 20
    assert table.c.actor_id.type.length == 64
    assert table.c.idempotency_key.type.length == 64
    assert table.c.request_hash.type.length == 64
    assert table.c.status.type.length == 16
    assert table.c.owner_token.type.length == 36

    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert unique_constraints["uq_detection_requests_actor_key"] == (
        "actor_type",
        "actor_id",
        "idempotency_key",
    )

    check_constraints = {
        constraint.name: _normalized_sql(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert check_constraints["ck_detection_requests_status"] == (
        "status in ('processing', 'completed', 'failed')"
    )
    assert check_constraints["ck_detection_requests_reserved_chars_positive"] == "reserved_chars > 0"

    foreign_key = next(iter(table.c.detection_id.foreign_keys))
    assert foreign_key.target_fullname == "detections.id"
    assert foreign_key.ondelete == "SET NULL"


def test_detection_request_model_limits_each_actor_to_one_processing_row():
    from app.models.detection_request import DetectionRequest

    index = next(
        item
        for item in DetectionRequest.__table__.indexes
        if item.name == "uq_detection_requests_actor_processing"
    )
    assert index.unique is True
    assert tuple(expression.name for expression in index.expressions) == ("actor_type", "actor_id")
    assert _normalized_sql(index.dialect_options["postgresql"]["where"]) == "status = 'processing'"
    assert _normalized_sql(index.dialect_options["sqlite"]["where"]) == "status = 'processing'"


def test_detection_request_sqlite_enforces_one_processing_row_per_actor(db_session):
    first = _request(actor_id="schema-processing-actor", key="first")
    db_session.add(first)
    db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(_request(actor_id="schema-processing-actor", key="second"))
            db_session.flush()

    first.status = "completed"
    db_session.flush()
    db_session.add(_request(actor_id="schema-processing-actor", key="second"))
    db_session.flush()


@pytest.mark.parametrize(
    ("status", "chars"),
    [
        pytest.param("unknown", 100, id="unknown-status"),
        pytest.param("processing", 0, id="non-positive-reservation"),
    ],
)
def test_detection_request_sqlite_enforces_check_constraints(db_session, status, chars):
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                _request(
                    actor_id=f"schema-check-{status}-{chars}",
                    key="check",
                    status=status,
                    chars=chars,
                )
            )
            db_session.flush()


def test_detection_request_migration_matches_model_contract(monkeypatch):
    migration = _load_migration()
    calls: dict[str, list] = {"tables": [], "indexes": []}

    def record_table(name, *items, **kwargs):
        table = Table(name, MetaData(), *items, **kwargs)
        calls["tables"].append(table)
        return table

    def record_index(name, table_name, columns, **kwargs):
        calls["indexes"].append((name, table_name, tuple(columns), kwargs))

    monkeypatch.setattr(migration.op, "create_table", record_table)
    monkeypatch.setattr(migration.op, "create_index", record_index)

    migration.upgrade()

    assert migration.revision == "20240921_0017"
    assert migration.down_revision == "20240920_0016"
    assert len(calls["tables"]) == 1
    table = calls["tables"][0]
    assert table.name == "detection_requests"
    columns = {column.name: column for column in table.columns}
    assert set(columns) == EXPECTED_COLUMNS
    assert columns["detection_id"].nullable is True
    detection_fk = next(iter(columns["detection_id"].foreign_keys))
    assert detection_fk.target_fullname == "detections.id"
    assert detection_fk.ondelete == "SET NULL"

    unique_constraints = {
        item.name: tuple(column.name for column in item.columns)
        for item in table.constraints
        if isinstance(item, UniqueConstraint)
    }
    assert unique_constraints["uq_detection_requests_actor_key"] == (
        "actor_type",
        "actor_id",
        "idempotency_key",
    )
    check_constraints = {
        item.name: _normalized_sql(item.sqltext)
        for item in table.constraints
        if isinstance(item, CheckConstraint)
    }
    assert check_constraints["ck_detection_requests_status"] == (
        "status in ('processing', 'completed', 'failed')"
    )
    assert check_constraints["ck_detection_requests_reserved_chars_positive"] == "reserved_chars > 0"

    assert len(calls["indexes"]) == 1
    index_name, index_table, index_columns, index_kwargs = calls["indexes"][0]
    assert (index_name, index_table, index_columns, index_kwargs["unique"]) == (
        "uq_detection_requests_actor_processing",
        "detection_requests",
        ("actor_type", "actor_id"),
        True,
    )
    assert _normalized_sql(index_kwargs["postgresql_where"]) == "status = 'processing'"
    assert _normalized_sql(index_kwargs["sqlite_where"]) == "status = 'processing'"


def test_detection_request_migration_downgrade_drops_index_before_table(monkeypatch):
    migration = _load_migration()
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        migration.op,
        "drop_index",
        lambda name, **kwargs: calls.append(("index", name)),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_table",
        lambda name, **kwargs: calls.append(("table", name)),
    )

    migration.downgrade()

    assert calls == [
        ("index", "uq_detection_requests_actor_processing"),
        ("table", "detection_requests"),
    ]
