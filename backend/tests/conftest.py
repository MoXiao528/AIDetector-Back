import sys
import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app.db.base_class import Base  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def configure_test_settings():
    from app.api.v1 import auth as auth_api
    from app.core import security
    from app.db import deps

    strong_secret = "test-secret-key-with-at-least-32-characters"
    security.settings.secret_key = strong_secret
    deps.settings.secret_key = strong_secret
    auth_api.settings.secret_key = strong_secret


@pytest.fixture(scope="session")
def engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db_session(engine) -> Generator[Session, None, None]:
    connection = engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def unique_email() -> str:
    return f"test-{uuid.uuid4()}@example.com"
