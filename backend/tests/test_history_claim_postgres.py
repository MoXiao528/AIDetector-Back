"""PostgreSQL-only concurrency regressions for guest history claims."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from threading import Barrier, Event
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import URL, create_engine, delete, func, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1 import detections as detections_api
from app.api.v1.detections import detect_router
from app.api.v1.history import router as history_router
from app.core.config import Settings
from app.core.roles import UserRole
from app.core.security import create_access_token
from app.db.session import get_db
from app.models.detection import Detection
from app.models.detection_request import DetectionRequest
from app.models.guest_session import GuestSession
from app.models.quota_usage import QuotaUsage
from app.models.user import User


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_CLAIM_TESTS") != "1",
    reason="set RUN_POSTGRES_CLAIM_TESTS=1 to run destructive-isolated PostgreSQL claim tests",
)


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


def _make_app(session_factory: sessionmaker[Session]) -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(detect_router, prefix="/api/v1")
    test_app.include_router(history_router, prefix="/api/v1")

    def _postgres_session():
        with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_db] = _postgres_session
    return test_app


def _seed_claim_case(postgres_engine, *, user_count: int) -> dict[str, object]:
    suffix = uuid4().hex
    guest_id = str(uuid4())
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False, future=True)
    with session_factory() as session:
        users = [
            User(
                email=f"back03-pg-{index}-{suffix}@example.com",
                name=f"back03-pg-{index}-{suffix}",
                password_hash="not-used-by-claim-tests",
                role=UserRole.INDIVIDUAL,
                plan_tier="personal-free",
                credits_total=30000,
                credits_used=0,
                onboarding_completed=False,
                is_active=True,
            )
            for index in range(user_count)
        ]
        session.add_all(users)
        session.flush()
        session.add(
            GuestSession(
                id=guest_id,
                refresh_token_hash=uuid4().hex + uuid4().hex,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        detection = Detection(
            user_id=None,
            actor_type="guest",
            actor_id=guest_id,
            chars_used=15,
            title=f"BACK-03 PostgreSQL {suffix}",
            input_text="PostgreSQL claim race",
            editor_html="<p>PostgreSQL claim race</p>",
            functions_used=["scan"],
            result_label="ai",
            score=0.82,
            meta_json={
                "analysis": {
                    "summary": {"ai": 82, "mixed": 10, "human": 8},
                    "sentences": [],
                }
            },
        )
        session.add(detection)
        session.commit()
        return {
            "guest_id": guest_id,
            "guest_token": create_access_token(
                subject=guest_id,
                extra_claims={"sub_type": "guest", "sid": guest_id},
            ),
            "user_ids": [user.id for user in users],
            "user_tokens": [
                create_access_token(subject=str(user.id), extra_claims={"sub_type": "user"})
                for user in users
            ],
            "detection_id": detection.id,
        }


def _cleanup_claim_case(postgres_engine, case: dict[str, object]) -> None:
    user_ids = list(case["user_ids"])
    guest_id = str(case["guest_id"])
    detection_id = int(case["detection_id"])
    with Session(postgres_engine) as session:
        session.rollback()
        session.execute(
            delete(DetectionRequest).where(
                DetectionRequest.actor_type == "guest",
                DetectionRequest.actor_id == guest_id,
            )
        )
        session.execute(
            delete(Detection).where(
                (Detection.id == detection_id)
                | (Detection.actor_id == guest_id)
                | (Detection.user_id.in_(user_ids))
            )
        )
        session.execute(delete(QuotaUsage).where(QuotaUsage.actor_id == guest_id))
        session.execute(delete(GuestSession).where(GuestSession.id == guest_id))
        session.execute(delete(User).where(User.id.in_(user_ids)))
        session.commit()
        assert session.get(Detection, detection_id) is None
        assert session.get(GuestSession, guest_id) is None
        assert all(session.get(User, user_id) is None for user_id in user_ids)


def test_postgres_claim_waits_for_locked_guest_detection_and_migrates_its_record(
    postgres_engine,
    monkeypatch,
):
    case = _seed_claim_case(postgres_engine, user_count=1)
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False, future=True)
    detection_app = _make_app(session_factory)
    claim_app = FastAPI()
    claim_app.include_router(history_router, prefix="/api/v1")
    final_guest_lock_acquired = Event()
    release_detection = Event()
    claim_pid_ready = Event()
    detect_backend_pid: dict[str, int] = {}
    claim_backend_pid: dict[str, int] = {}
    guest_lock_calls = 0
    get_active_guest_session_id = detections_api._get_active_guest_session_id

    async def _successful_detect(segments):
        return [
            {
                "score": 0.9,
                "threshold": 0.5,
                "label": "AI",
                "model_name": "back03-race-test",
                "score_type": "probability",
            }
            for _ in segments
        ]

    def _hold_after_final_guest_lock(db, session_id, *, lock=False):
        nonlocal guest_lock_calls
        active_session_id = get_active_guest_session_id(db, session_id, lock=lock)
        if lock:
            guest_lock_calls += 1
            if guest_lock_calls == 2:
                detect_backend_pid["value"] = db.scalar(select(func.pg_backend_pid()))
                final_guest_lock_acquired.set()
                assert release_detection.wait(timeout=5)
        return active_session_id

    def _claim_session():
        with session_factory() as session:
            claim_backend_pid["value"] = session.scalar(select(func.pg_backend_pid()))
            claim_pid_ready.set()
            yield session

    monkeypatch.setattr(detections_api, "_detect_segments_with_limit", _successful_detect)
    monkeypatch.setattr(detections_api, "_get_active_guest_session_id", _hold_after_final_guest_lock)
    claim_app.dependency_overrides[get_db] = _claim_session

    def _run_guest_detection():
        with TestClient(detection_app) as client:
            return client.post(
                "/api/v1/detect",
                json={
                    "text": (
                        "This PostgreSQL race test holds the guest session lock until the completed "
                        "detection and its quota usage are committed in the same transaction. "
                    )
                    * 3
                },
                headers={
                    "Authorization": f"Bearer {case['guest_token']}",
                    "Idempotency-Key": str(uuid4()),
                },
            )

    def _run_claim():
        with TestClient(claim_app) as client:
            return client.post(
                "/api/v1/history/claim-guest",
                json={"guest_token": case["guest_token"]},
                headers={"Authorization": f"Bearer {case['user_tokens'][0]}"},
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            detection_future = executor.submit(_run_guest_detection)
            assert final_guest_lock_acquired.wait(timeout=5)
            claim_future = executor.submit(_run_claim)
            assert claim_pid_ready.wait(timeout=5)

            blockers: list[int] = []
            deadline = monotonic() + 5
            with Session(postgres_engine) as inspection:
                while monotonic() < deadline:
                    blockers = inspection.scalar(
                        select(func.pg_blocking_pids(claim_backend_pid["value"]))
                    )
                    if detect_backend_pid["value"] in blockers:
                        break
                    release_detection.wait(timeout=0.01)
            assert detect_backend_pid["value"] in blockers
            assert not claim_future.done()

            release_detection.set()
            detection_response = detection_future.result(timeout=10)
            claim_response = claim_future.result(timeout=10)

        assert detection_response.status_code == 200
        assert claim_response.status_code == 200
        assert claim_response.json().get("claimedCount", claim_response.json().get("claimed_count")) == 2

        with Session(postgres_engine) as inspection:
            records = list(
                inspection.scalars(
                    select(Detection)
                    .where(Detection.user_id == case["user_ids"][0])
                    .order_by(Detection.id)
                ).all()
            )
            assert len(records) == 2
            assert {
                (record.user_id, record.actor_type, record.actor_id)
                for record in records
            } == {(case["user_ids"][0], "user", str(case["user_ids"][0]))}
    finally:
        release_detection.set()
        _cleanup_claim_case(postgres_engine, case)


def test_postgres_concurrent_claim_has_exactly_one_winner(postgres_engine):
    case = _seed_claim_case(postgres_engine, user_count=2)
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False, future=True)
    apps = [_make_app(session_factory), _make_app(session_factory)]
    barrier = Barrier(2)

    def _claim(index: int):
        with TestClient(apps[index]) as client:
            barrier.wait(timeout=5)
            response = client.post(
                "/api/v1/history/claim-guest",
                json={"guest_token": case["guest_token"]},
                headers={"Authorization": f"Bearer {case['user_tokens'][index]}"},
            )
            return index, response.status_code, response.json()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(_claim, range(2)))

        assert sorted(result[1] for result in results) == [200, 400]
        winner_index, _, winner_payload = next(result for result in results if result[1] == 200)
        assert winner_payload.get("claimedCount", winner_payload.get("claimed_count")) == 1

        with Session(postgres_engine) as inspection:
            detection = inspection.get(Detection, case["detection_id"])
            guest_session = inspection.get(GuestSession, case["guest_id"])
            assert detection is not None
            assert (
                detection.user_id,
                detection.actor_type,
                detection.actor_id,
            ) == (
                case["user_ids"][winner_index],
                "user",
                str(case["user_ids"][winner_index]),
            )
            assert guest_session is not None
            assert guest_session.revoked_at is not None
    finally:
        _cleanup_claim_case(postgres_engine, case)


def test_postgres_claim_prevents_inflight_guest_detection_from_writing_after_revocation(
    postgres_engine,
    monkeypatch,
):
    case = _seed_claim_case(postgres_engine, user_count=1)
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False, future=True)
    test_app = _make_app(session_factory)
    inference_started = Event()
    release_inference = Event()

    async def _delayed_detect(segments):
        inference_started.set()
        assert release_inference.wait(timeout=5)
        return [
            {
                "score": 0.9,
                "threshold": 0.5,
                "label": "AI",
                "model_name": "back03-race-test",
                "score_type": "probability",
            }
            for _ in segments
        ]

    monkeypatch.setattr(detections_api, "_detect_segments_with_limit", _delayed_detect)

    def _run_guest_detection():
        with TestClient(test_app) as client:
            return client.post(
                "/api/v1/detect",
                json={
                    "text": (
                        "This PostgreSQL race test keeps a guest detection in model inference while another "
                        "account atomically claims the same guest history and revokes its session. "
                    )
                    * 3
                },
                headers={
                    "Authorization": f"Bearer {case['guest_token']}",
                    "Idempotency-Key": str(uuid4()),
                },
            )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            detection_future = executor.submit(_run_guest_detection)
            assert inference_started.wait(timeout=5)

            with TestClient(test_app) as client:
                claim_response = client.post(
                    "/api/v1/history/claim-guest",
                    json={"guest_token": case["guest_token"]},
                    headers={"Authorization": f"Bearer {case['user_tokens'][0]}"},
                )
            assert claim_response.status_code == 200
            assert claim_response.json().get("claimedCount", claim_response.json().get("claimed_count")) == 1

            release_inference.set()
            detection_response = detection_future.result(timeout=10)

        assert detection_response.status_code == 401
        with Session(postgres_engine) as inspection:
            quota_usage = inspection.scalar(
                select(QuotaUsage).where(
                    QuotaUsage.actor_type == "guest",
                    QuotaUsage.actor_id == case["guest_id"],
                )
            )
            records = list(
                inspection.scalars(
                    select(Detection).where(
                        or_(
                            Detection.id == case["detection_id"],
                            Detection.actor_id == case["guest_id"],
                            Detection.user_id.in_(case["user_ids"]),
                        )
                    )
                ).all()
            )
            assert [(record.user_id, record.actor_type, record.actor_id) for record in records] == [
                (case["user_ids"][0], "user", str(case["user_ids"][0]))
            ]
            assert quota_usage is not None
            assert quota_usage.used == 15
    finally:
        release_inference.set()
        _cleanup_claim_case(postgres_engine, case)


def test_postgres_claim_rolls_back_after_flush_when_commit_fails(postgres_engine, monkeypatch):
    case = _seed_claim_case(postgres_engine, user_count=1)
    failing_session = Session(postgres_engine, expire_on_commit=False, future=True)
    test_app = FastAPI()
    test_app.include_router(history_router, prefix="/api/v1")

    def _failing_session_dependency():
        yield failing_session

    test_app.dependency_overrides[get_db] = _failing_session_dependency

    def _fail_commit_after_flush():
        failing_session.flush()
        raise OperationalError("COMMIT", {}, RuntimeError("injected commit failure after flush"))

    monkeypatch.setattr(failing_session, "commit", _fail_commit_after_flush)

    try:
        with TestClient(test_app, raise_server_exceptions=False) as client:
            response = client.post(
                "/api/v1/history/claim-guest",
                json={"guest_token": case["guest_token"]},
                headers={"Authorization": f"Bearer {case['user_tokens'][0]}"},
            )

        transaction_was_left_open = failing_session.in_transaction()
        internal_detection = failing_session.get(Detection, case["detection_id"])
        internal_guest_session = failing_session.get(GuestSession, case["guest_id"])
        internal_state = (
            internal_detection.user_id,
            internal_detection.actor_type,
            internal_detection.actor_id,
            internal_guest_session.revoked_at,
        )
        with Session(postgres_engine) as inspection:
            external_detection = inspection.get(Detection, case["detection_id"])
            external_guest_session = inspection.get(GuestSession, case["guest_id"])
            external_state = (
                external_detection.user_id,
                external_detection.actor_type,
                external_detection.actor_id,
                external_guest_session.revoked_at,
            )

        expected_state = (None, "guest", case["guest_id"], None)
        assert response.status_code == 500
        assert (transaction_was_left_open, internal_state, external_state) == (
            False,
            expected_state,
            expected_state,
        )
    finally:
        failing_session.rollback()
        failing_session.close()
        _cleanup_claim_case(postgres_engine, case)
