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


class FakeTokenizer:
    @staticmethod
    def _pieces(text: str) -> list[str]:
        return [piece for piece in str(text or "").replace("\n", " ").split(" ") if piece]

    def encode(
        self,
        text: str,
        *,
        add_special_tokens: bool = True,
        truncation: bool = False,
        max_length: int | None = None,
    ) -> list[int]:
        body = list(range(len(self._pieces(text))))
        if truncation and max_length is not None:
            special_count = 2 if add_special_tokens else 0
            body = body[: max(max_length - special_count, 0)]
        if not add_special_tokens:
            return body
        return [-1, *body, -2]

    def decode(
        self,
        token_ids: list[int],
        *,
        skip_special_tokens: bool = True,
        clean_up_tokenization_spaces: bool = False,
    ) -> str:
        _ = skip_special_tokens, clean_up_tokenization_spaces
        return " ".join(f"tok{token_id}" for token_id in token_ids if token_id >= 0)


@pytest.fixture(autouse=True)
def fake_tokenizer(monkeypatch):
    monkeypatch.setattr("app.services.token_chunker.get_tokenizer", lambda model_name=None: FakeTokenizer())


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
