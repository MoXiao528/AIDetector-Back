"""PostgreSQL-only concurrency regressions for detection admission."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
from pathlib import Path
from threading import Barrier, Lock
from uuid import uuid4

import pytest
from sqlalchemy import URL, create_engine, delete, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.models.detection_request import DetectionRequest as DetectionRequestRecord
from app.models.quota_usage import QuotaUsage
from app.services.quota_service import (
    DetectionActorBusyError,
    DetectionRequestInProgressError,
    DetectionReservationLostError,
    USER_DAILY_LIMIT,
    lock_detection_settlement,
    reserve_detection_request,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_IDEMPOTENCY_TESTS") != "1",
    reason="set RUN_POSTGRES_IDEMPOTENCY_TESTS=1 to run destructive-isolated PostgreSQL idempotency tests",
)

THREAD_TIMEOUT_SECONDS = 15
LEASE_SECONDS = 60
RESERVED_CHARS = 300


def _postgres_url() -> URL:
    settings = Settings(_env_file=Path(__file__).resolve().parents[2] / ".env")
    return URL.create(
        "postgresql+psycopg2",
        username=settings.postgres_user,
        password=settings.postgres_password,
        host=os.getenv("POSTGRES_CLAIM_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_CLAIM_PORT", "15432")),
        database=settings.postgres_db,
        query={"client_encoding": "utf8"},
    )


@pytest.fixture(scope="module")
def postgres_engine():
    engine = create_engine(_postgres_url(), future=True, pool_pre_ping=True)
    with engine.connect() as connection:
        assert connection.dialect.name == "postgresql"
    try:
        yield engine
    finally:
        engine.dispose()


def _set_thread_timeouts(session: Session) -> None:
    session.execute(text("SET LOCAL lock_timeout = '5s'"))
    session.execute(text("SET LOCAL statement_timeout = '10s'"))


def _request_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _cleanup_actor(postgres_engine, actor_id: str) -> None:
    with Session(postgres_engine) as session:
        session.execute(
            delete(DetectionRequestRecord).where(
                DetectionRequestRecord.actor_type == "user",
                DetectionRequestRecord.actor_id == actor_id,
            )
        )
        session.execute(
            delete(QuotaUsage).where(
                QuotaUsage.actor_type == "user",
                QuotaUsage.actor_id == actor_id,
            )
        )
        session.commit()


def _inspect_actor(postgres_engine, actor_id: str) -> tuple[list[DetectionRequestRecord], QuotaUsage | None]:
    with Session(postgres_engine) as session:
        records = list(
            session.scalars(
                select(DetectionRequestRecord)
                .where(
                    DetectionRequestRecord.actor_type == "user",
                    DetectionRequestRecord.actor_id == actor_id,
                )
                .order_by(DetectionRequestRecord.id)
            ).all()
        )
        quota = session.scalar(
            select(QuotaUsage).where(
                QuotaUsage.actor_type == "user",
                QuotaUsage.actor_id == actor_id,
            )
        )
        for record in records:
            session.expunge(record)
        if quota is not None:
            session.expunge(quota)
        return records, quota


def test_postgres_different_keys_allow_only_one_actor_reservation(postgres_engine):
    actor_id = f"back04-different-{uuid4().hex}"
    keys = [str(uuid4()), str(uuid4())]
    barrier = Barrier(2)
    marker_lock = Lock()
    inference_markers = 0
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False, future=True)

    def reserve(key: str):
        nonlocal inference_markers
        with session_factory() as session:
            _set_thread_timeouts(session)
            barrier.wait(timeout=5)
            try:
                admission = reserve_detection_request(
                    session,
                    actor_type="user",
                    actor_id=actor_id,
                    idempotency_key=key,
                    request_hash=_request_hash(key),
                    chars=RESERVED_CHARS,
                    limit=USER_DAILY_LIMIT,
                    lease_seconds=LEASE_SECONDS,
                )
                session.commit()
            except DetectionActorBusyError as exc:
                session.rollback()
                return "busy", key, exc.retry_after

            with marker_lock:
                inference_markers += 1
            return "acquired", key, admission.owner_token

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(reserve, key) for key in keys]
            results = [future.result(timeout=THREAD_TIMEOUT_SECONDS) for future in futures]

        assert sorted(result[0] for result in results) == ["acquired", "busy"]
        assert inference_markers == 1
        assert all(result[2] >= 1 for result in results if result[0] == "busy")

        records, quota = _inspect_actor(postgres_engine, actor_id)
        assert len(records) == 1
        assert records[0].status == "processing"
        assert records[0].idempotency_key in keys
        assert quota is not None and quota.used == 0
    finally:
        _cleanup_actor(postgres_engine, actor_id)


def test_postgres_same_key_and_hash_run_one_reservation(postgres_engine):
    actor_id = f"back04-same-{uuid4().hex}"
    key = str(uuid4())
    request_hash = _request_hash("same-logical-request")
    barrier = Barrier(2)
    marker_lock = Lock()
    inference_markers = 0
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False, future=True)

    def reserve():
        nonlocal inference_markers
        with session_factory() as session:
            _set_thread_timeouts(session)
            barrier.wait(timeout=5)
            try:
                admission = reserve_detection_request(
                    session,
                    actor_type="user",
                    actor_id=actor_id,
                    idempotency_key=key,
                    request_hash=request_hash,
                    chars=RESERVED_CHARS,
                    limit=USER_DAILY_LIMIT,
                    lease_seconds=LEASE_SECONDS,
                )
                session.commit()
            except DetectionRequestInProgressError as exc:
                session.rollback()
                return "in_progress", exc.retry_after

            with marker_lock:
                inference_markers += 1
            return "acquired", admission.owner_token

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(reserve) for _ in range(2)]
            results = [future.result(timeout=THREAD_TIMEOUT_SECONDS) for future in futures]

        assert sorted(result[0] for result in results) == ["acquired", "in_progress"]
        assert inference_markers == 1
        assert all(result[1] >= 1 for result in results if result[0] == "in_progress")

        records, quota = _inspect_actor(postgres_engine, actor_id)
        assert len(records) == 1
        assert records[0].status == "processing"
        assert records[0].idempotency_key == key
        assert quota is not None and quota.used == 0
    finally:
        _cleanup_actor(postgres_engine, actor_id)


def test_postgres_expired_owner_cannot_settle_against_takeover(postgres_engine):
    actor_id = f"back04-takeover-{uuid4().hex}"
    key = str(uuid4())
    request_hash = _request_hash("expired-owner-race")
    barrier = Barrier(2)
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False, future=True)

    try:
        with session_factory() as seed_session:
            old_admission = reserve_detection_request(
                seed_session,
                actor_type="user",
                actor_id=actor_id,
                idempotency_key=key,
                request_hash=request_hash,
                chars=RESERVED_CHARS,
                limit=USER_DAILY_LIMIT,
                lease_seconds=LEASE_SECONDS,
            )
            seed_session.commit()
            request_record = seed_session.get(DetectionRequestRecord, old_admission.request_id)
            assert request_record is not None
            request_record.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            seed_session.commit()

        def old_owner_settlement():
            with session_factory() as session:
                _set_thread_timeouts(session)
                barrier.wait(timeout=5)
                try:
                    lock_detection_settlement(
                        session,
                        admission=old_admission,
                        limit=USER_DAILY_LIMIT,
                    )
                except DetectionReservationLostError:
                    session.rollback()
                    return "old", "lost", old_admission.owner_token
                session.rollback()
                return "old", "locked", old_admission.owner_token

        def takeover_and_settle():
            with session_factory() as session:
                _set_thread_timeouts(session)
                barrier.wait(timeout=5)
                admission = reserve_detection_request(
                    session,
                    actor_type="user",
                    actor_id=actor_id,
                    idempotency_key=key,
                    request_hash=request_hash,
                    chars=RESERVED_CHARS,
                    limit=USER_DAILY_LIMIT,
                    lease_seconds=LEASE_SECONDS,
                )
                session.commit()
                try:
                    lock_detection_settlement(
                        session,
                        admission=admission,
                        limit=USER_DAILY_LIMIT,
                    )
                except DetectionReservationLostError:
                    session.rollback()
                    return "new", "lost", admission.owner_token
                session.rollback()
                return "new", "locked", admission.owner_token

        with ThreadPoolExecutor(max_workers=2) as executor:
            old_future = executor.submit(old_owner_settlement)
            new_future = executor.submit(takeover_and_settle)
            results = [
                old_future.result(timeout=THREAD_TIMEOUT_SECONDS),
                new_future.result(timeout=THREAD_TIMEOUT_SECONDS),
            ]

        locked = [result for result in results if result[1] == "locked"]
        assert len(locked) <= 1
        assert results[0][0:2] == ("old", "lost")
        assert results[1][0:2] == ("new", "locked")
        assert results[0][2] != results[1][2]

        records, quota = _inspect_actor(postgres_engine, actor_id)
        assert len(records) == 1
        assert sum(record.status == "processing" for record in records) == 1
        assert records[0].owner_token == results[1][2]
        assert quota is not None
        assert 0 <= quota.used <= USER_DAILY_LIMIT
        with Session(postgres_engine) as inspection:
            processing_count = inspection.scalar(
                select(func.count(DetectionRequestRecord.id)).where(
                    DetectionRequestRecord.actor_type == "user",
                    DetectionRequestRecord.actor_id == actor_id,
                    DetectionRequestRecord.status == "processing",
                )
            )
            assert processing_count == 1
    finally:
        _cleanup_actor(postgres_engine, actor_id)
