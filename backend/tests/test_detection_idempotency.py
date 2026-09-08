import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.api.v1.auth import register_user
from app.api.v1.detections import _build_detection_request_hash, detect
from app.core.config import get_settings
from app.db.base_class import Base
from app.db.deps import ActorContext
from app.models.detection import Detection
from app.models.detection_request import DetectionRequest as DetectionRequestRecord
from app.models.quota_usage import QuotaUsage
from app.schemas.auth import RegisterRequest
from app.schemas.detection import DetectionRequest
from app.services.evidence_engine import EvidenceEngine
from app.services.quota_service import (
    DetectionAdmission,
    DetectionReservationLostError,
    USER_DAILY_LIMIT,
    lock_detection_settlement,
    reserve_detection_request,
)
from app.services.repre_guard_client import RepreGuardError, repre_guard_client


LONG_TEXT = (
    "This idempotency regression sample is deliberately long enough to pass validation and stay inside one "
    "detector segment. It gives the backend a stable logical request whose retries must never repeat model work, "
    "quota settlement, or history creation after the first request has completed successfully."
)


@pytest.fixture
def committed_db_session(tmp_path):
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(tmp_path / "idempotency.db")),
        future=True,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


async def _user_actor(db_session, email: str) -> ActorContext:
    user = await register_user(RegisterRequest(email=email, password="StrongPass!23"), db_session)
    return ActorContext(actor_type="user", actor_id=str(user.id), user=user)


def _fake_result() -> dict:
    return {
        "score": 0.72,
        "threshold": 0.5,
        "label": "AI",
        "model_name": "idempotency-test-model",
        "score_type": "probability",
    }


@pytest.mark.anyio
async def test_completed_key_replays_without_second_inference_charge_or_history(
    db_session,
    unique_email,
    monkeypatch,
):
    actor = await _user_actor(db_session, unique_email)
    key = uuid4()
    detector_calls: list[str] = []

    async def fake_detect(text: str) -> dict:
        detector_calls.append(text)
        return _fake_result()

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)

    first = await detect(
        payload=DetectionRequest(text=LONG_TEXT, functions=["scan"]),
        db=db_session,
        current_actor=actor,
        idempotency_key=key,
    )
    calls_after_first = len(detector_calls)

    replay = await detect(
        payload=DetectionRequest(text=LONG_TEXT, functions=["scan"]),
        db=db_session,
        current_actor=actor,
        idempotency_key=key,
    )

    assert calls_after_first > 0
    assert len(detector_calls) == calls_after_first
    assert replay.detection_id == first.detection_id
    assert replay.history_id == first.history_id
    assert replay.result == first.result
    assert db_session.scalar(
        select(func.count(Detection.id)).where(
            Detection.actor_type == actor.actor_type,
            Detection.actor_id == actor.actor_id,
        )
    ) == 1
    usage = db_session.scalar(
        select(QuotaUsage).where(
            QuotaUsage.actor_type == actor.actor_type,
            QuotaUsage.actor_id == actor.actor_id,
        )
    )
    assert usage is not None
    assert usage.used == len(LONG_TEXT)


@pytest.mark.anyio
async def test_same_key_with_different_payload_is_rejected_before_inference(
    db_session,
    unique_email,
    monkeypatch,
):
    actor = await _user_actor(db_session, unique_email)
    key = uuid4()
    detector_calls: list[str] = []

    async def fake_detect(text: str) -> dict:
        detector_calls.append(text)
        return _fake_result()

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)
    await detect(
        payload=DetectionRequest(text=LONG_TEXT),
        db=db_session,
        current_actor=actor,
        idempotency_key=key,
    )
    calls_after_first = len(detector_calls)

    with pytest.raises(HTTPException) as exc_info:
        await detect(
            payload=DetectionRequest(text=f"{LONG_TEXT} Changed payload."),
            db=db_session,
            current_actor=actor,
            idempotency_key=key,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert len(detector_calls) == calls_after_first


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("same_key", "expected_code"),
    [(True, "DETECTION_IN_PROGRESS"), (False, "DETECTION_ACTOR_BUSY")],
)
async def test_active_reservation_rejects_retry_before_inference(
    db_session,
    unique_email,
    monkeypatch,
    same_key,
    expected_code,
):
    actor = await _user_actor(db_session, unique_email)
    active_key = uuid4()
    payload = DetectionRequest(text=LONG_TEXT)
    now = datetime.now(timezone.utc)
    db_session.add(
        DetectionRequestRecord(
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            idempotency_key=str(active_key),
            request_hash=_build_detection_request_hash(payload, operation="detect"),
            status="processing",
            usage_date=now.date(),
            reserved_chars=len(LONG_TEXT),
            owner_token=str(uuid4()),
            lease_expires_at=now + timedelta(minutes=5),
        )
    )
    db_session.commit()

    detector_calls: list[str] = []

    async def fake_detect(text: str) -> dict:
        detector_calls.append(text)
        return _fake_result()

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)

    with pytest.raises(HTTPException) as exc_info:
        await detect(
            payload=payload,
            db=db_session,
            current_actor=actor,
            idempotency_key=active_key if same_key else uuid4(),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == expected_code
    assert detector_calls == []
    assert int(exc_info.value.headers["Retry-After"]) >= 1


@pytest.mark.anyio
async def test_timeout_keeps_unknown_request_processing_and_retry_does_not_reinfer(
    db_session,
    unique_email,
    monkeypatch,
):
    actor = await _user_actor(db_session, unique_email)
    key = uuid4()
    detector_calls = 0

    async def timed_out_detect(text: str) -> dict:
        nonlocal detector_calls
        detector_calls += 1
        raise asyncio.TimeoutError

    monkeypatch.setattr(repre_guard_client, "detect", timed_out_detect)

    with pytest.raises(HTTPException) as exc_info:
        await detect(
            payload=DetectionRequest(text=LONG_TEXT),
            db=db_session,
            current_actor=actor,
            idempotency_key=key,
        )
    assert exc_info.value.status_code == 504

    request_record = db_session.scalar(
        select(DetectionRequestRecord).where(
            DetectionRequestRecord.actor_type == actor.actor_type,
            DetectionRequestRecord.actor_id == actor.actor_id,
            DetectionRequestRecord.idempotency_key == str(key),
        )
    )
    assert request_record is not None
    assert request_record.status == "processing"

    with pytest.raises(HTTPException) as retry_exc:
        await detect(
            payload=DetectionRequest(text=LONG_TEXT),
            db=db_session,
            current_actor=actor,
            idempotency_key=key,
        )
    assert retry_exc.value.detail["code"] == "DETECTION_IN_PROGRESS"
    assert detector_calls == 1


@pytest.mark.anyio
async def test_quota_is_reserved_before_detector_call(db_session, unique_email, monkeypatch):
    actor = await _user_actor(db_session, unique_email)
    now = datetime.now(timezone.utc)
    db_session.add(
        QuotaUsage(
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            usage_date=now.date(),
            limit=USER_DAILY_LIMIT,
            used=USER_DAILY_LIMIT - len(LONG_TEXT) + 1,
        )
    )
    db_session.commit()
    detector_calls: list[str] = []

    async def fake_detect(text: str) -> dict:
        detector_calls.append(text)
        return _fake_result()

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)

    with pytest.raises(HTTPException) as exc_info:
        await detect(
            payload=DetectionRequest(text=LONG_TEXT),
            db=db_session,
            current_actor=actor,
            idempotency_key=uuid4(),
        )

    assert exc_info.value.status_code == 429
    assert detector_calls == []
    assert db_session.scalar(
        select(func.count(DetectionRequestRecord.id)).where(
            DetectionRequestRecord.actor_type == actor.actor_type,
            DetectionRequestRecord.actor_id == actor.actor_id,
        )
    ) == 0


@pytest.mark.anyio
async def test_completed_key_with_deleted_history_never_reinfers(db_session, unique_email, monkeypatch):
    actor = await _user_actor(db_session, unique_email)
    key = uuid4()
    detector_calls = 0

    async def fake_detect(text: str) -> dict:
        nonlocal detector_calls
        detector_calls += 1
        return _fake_result()

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)
    first = await detect(
        payload=DetectionRequest(text=LONG_TEXT),
        db=db_session,
        current_actor=actor,
        idempotency_key=key,
    )
    db_session.query(Detection).filter(Detection.id == first.detection_id).delete(synchronize_session=False)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await detect(
            payload=DetectionRequest(text=LONG_TEXT),
            db=db_session,
            current_actor=actor,
            idempotency_key=key,
        )

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail["code"] == "IDEMPOTENCY_RESULT_GONE"
    assert detector_calls == 1


def test_expired_lease_takeover_fences_old_owner_and_moves_reservation_to_today(committed_db_session):
    db_session = committed_db_session
    actor_type = "user"
    actor_id = "lease-takeover-user"
    key = uuid4()
    payload = DetectionRequest(text=LONG_TEXT)
    request_hash = _build_detection_request_hash(payload, operation="detect")
    now = datetime.now(timezone.utc)
    old_owner = str(uuid4())
    old_usage_date = (now - timedelta(days=1)).date()
    request_record = DetectionRequestRecord(
        actor_type=actor_type,
        actor_id=actor_id,
        idempotency_key=str(key),
        request_hash=request_hash,
        status="processing",
        usage_date=old_usage_date,
        reserved_chars=len(LONG_TEXT),
        owner_token=old_owner,
        lease_expires_at=now - timedelta(seconds=1),
    )
    db_session.add(request_record)
    db_session.commit()
    request_id = request_record.id
    old_admission = DetectionAdmission(
        state="acquired",
        request_id=request_id,
        actor_type=actor_type,
        actor_id=actor_id,
        idempotency_key=str(key),
        owner_token=old_owner,
        usage_date=old_usage_date,
        reserved_chars=len(LONG_TEXT),
    )

    new_admission = reserve_detection_request(
        db_session,
        actor_type=actor_type,
        actor_id=actor_id,
        idempotency_key=str(key),
        request_hash=request_hash,
        chars=len(LONG_TEXT),
        limit=USER_DAILY_LIMIT,
        lease_seconds=150,
    )
    db_session.commit()

    assert new_admission.request_id == request_id
    assert new_admission.owner_token != old_owner
    assert new_admission.usage_date == now.date()
    with pytest.raises(DetectionReservationLostError):
        lock_detection_settlement(db_session, admission=old_admission, limit=USER_DAILY_LIMIT)
    db_session.rollback()


@pytest.mark.anyio
async def test_reservation_commit_failure_never_calls_detector(committed_db_session, unique_email, monkeypatch):
    db_session = committed_db_session
    actor = await _user_actor(db_session, unique_email)
    original_commit = db_session.commit
    detector_calls = 0

    async def fake_detect(text: str) -> dict:
        nonlocal detector_calls
        detector_calls += 1
        return _fake_result()

    def failed_commit() -> None:
        raise RuntimeError("reservation commit failed")

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)
    monkeypatch.setattr(db_session, "commit", failed_commit)

    with pytest.raises(RuntimeError, match="reservation commit failed"):
        await detect(
            payload=DetectionRequest(text=LONG_TEXT),
            db=db_session,
            current_actor=actor,
            idempotency_key=uuid4(),
        )

    monkeypatch.setattr(db_session, "commit", original_commit)
    assert detector_calls == 0
    assert db_session.scalar(
        select(func.count(DetectionRequestRecord.id)).where(
            DetectionRequestRecord.actor_type == actor.actor_type,
            DetectionRequestRecord.actor_id == actor.actor_id,
        )
    ) == 0


def _failed_evidence_request(monkeypatch):
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", "serve")
    engine = EvidenceEngine(mode="serve")
    calls = []
    original = engine.run

    async def run(text, **kwargs):
        calls.append(text)
        return await original(text, **kwargs)

    monkeypatch.setattr(engine, "run", run)
    return Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(evidence_engine=engine))}), calls


@pytest.mark.anyio
@pytest.mark.parametrize("with_evidence", [False, True])
async def test_final_commit_failure_rolls_back_and_lease_retry_recovers(
    committed_db_session,
    unique_email,
    monkeypatch,
    with_evidence,
):
    request, evidence_calls = _failed_evidence_request(monkeypatch) if with_evidence else (None, [])
    db_session = committed_db_session
    actor = await _user_actor(db_session, unique_email)
    key = uuid4()
    original_commit = db_session.commit
    commit_calls = 0
    detector_calls = 0

    async def fake_detect(text: str) -> dict:
        nonlocal detector_calls
        detector_calls += 1
        return _fake_result()

    def fail_second_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise RuntimeError("final commit failed")
        original_commit()

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)
    monkeypatch.setattr(db_session, "commit", fail_second_commit)

    with pytest.raises(RuntimeError, match="final commit failed"):
        await detect(
            payload=DetectionRequest(text=LONG_TEXT),
            db=db_session,
            current_actor=actor,
            idempotency_key=key,
            request=request,
        )

    monkeypatch.setattr(db_session, "commit", original_commit)
    request_record = db_session.scalar(
        select(DetectionRequestRecord).where(
            DetectionRequestRecord.actor_type == actor.actor_type,
            DetectionRequestRecord.actor_id == actor.actor_id,
            DetectionRequestRecord.idempotency_key == str(key),
        )
    )
    usage = db_session.scalar(
        select(QuotaUsage).where(
            QuotaUsage.actor_type == actor.actor_type,
            QuotaUsage.actor_id == actor.actor_id,
        )
    )
    assert request_record is not None
    assert request_record.status == "processing"
    assert usage is not None and usage.used == 0
    assert db_session.scalar(select(func.count(Detection.id))) == 0

    request_record.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    recovered = await detect(
        payload=DetectionRequest(text=LONG_TEXT),
        db=db_session,
        current_actor=actor,
        idempotency_key=key,
        request=request,
    )

    assert recovered.detection_id > 0
    assert detector_calls == 2
    assert db_session.scalar(select(func.count(Detection.id))) == 1
    assert db_session.scalar(select(QuotaUsage.used)) == len(LONG_TEXT)
    stored = db_session.get(Detection, recovered.detection_id)
    if with_evidence:
        assert evidence_calls == [LONG_TEXT, LONG_TEXT]
        assert recovered.evidence.model_dump(mode="json") == stored.meta_json["evidence"]
        assert stored.meta_json["artifactVersion"] is None
    else:
        assert "evidence" not in stored.meta_json


@pytest.mark.anyio
@pytest.mark.parametrize("with_evidence", [False, True])
async def test_commit_succeeded_but_response_failed_replays_without_reinference(
    committed_db_session,
    unique_email,
    monkeypatch,
    with_evidence,
):
    request, evidence_calls = _failed_evidence_request(monkeypatch) if with_evidence else (None, [])
    db_session = committed_db_session
    actor = await _user_actor(db_session, unique_email)
    key = uuid4()
    original_commit = db_session.commit
    commit_calls = 0
    detector_calls = 0

    async def fake_detect(text: str) -> dict:
        nonlocal detector_calls
        detector_calls += 1
        return _fake_result()

    def commit_then_fail_on_settlement() -> None:
        nonlocal commit_calls
        commit_calls += 1
        original_commit()
        if commit_calls == 2:
            raise RuntimeError("connection lost after commit")

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)
    monkeypatch.setattr(db_session, "commit", commit_then_fail_on_settlement)

    with pytest.raises(RuntimeError, match="connection lost after commit"):
        await detect(
            payload=DetectionRequest(text=LONG_TEXT),
            db=db_session,
            current_actor=actor,
            idempotency_key=key,
            request=request,
        )

    monkeypatch.setattr(db_session, "commit", original_commit)
    replay = await detect(
        payload=DetectionRequest(text=LONG_TEXT),
        db=db_session,
        current_actor=actor,
        idempotency_key=key,
        request=request,
    )

    assert replay.detection_id > 0
    assert detector_calls == 1
    assert db_session.scalar(select(func.count(Detection.id))) == 1
    assert db_session.scalar(select(QuotaUsage.used)) == len(LONG_TEXT)
    stored = db_session.get(Detection, replay.detection_id)
    if with_evidence:
        assert evidence_calls == [LONG_TEXT]
        assert replay.evidence.model_dump(mode="json") == stored.meta_json["evidence"]
        assert stored.meta_json["artifactVersion"] is None
    else:
        assert "evidence" not in stored.meta_json


@pytest.mark.anyio
async def test_multisegment_partial_failure_keeps_reservation_unknown(
    db_session,
    unique_email,
    monkeypatch,
):
    actor = await _user_actor(db_session, unique_email)
    key = uuid4()
    first_paragraph = f"First segment. {LONG_TEXT}"
    second_paragraph = f"Second segment. {LONG_TEXT}"
    both_started = asyncio.Event()
    detector_calls: list[str] = []

    async def partially_failed_detect(text: str) -> dict:
        detector_calls.append(text)
        if len(detector_calls) == 2:
            both_started.set()
        await both_started.wait()
        if text.startswith("First segment"):
            raise RepreGuardError(
                "Detector queue rejected one segment",
                status_code=503,
                code="DETECT_QUEUE_FULL",
            )
        return _fake_result()

    monkeypatch.setattr(repre_guard_client, "detect", partially_failed_detect)

    with pytest.raises(HTTPException) as exc_info:
        await detect(
            payload=DetectionRequest(text=f"{first_paragraph}\n{second_paragraph}"),
            db=db_session,
            current_actor=actor,
            idempotency_key=key,
        )

    assert exc_info.value.status_code == 503
    assert len(detector_calls) == 2
    request_record = db_session.scalar(
        select(DetectionRequestRecord).where(DetectionRequestRecord.idempotency_key == str(key))
    )
    assert request_record is not None
    assert request_record.status == "processing"


@pytest.mark.anyio
async def test_client_cancellation_keeps_reservation_unknown(db_session, unique_email, monkeypatch):
    actor = await _user_actor(db_session, unique_email)
    key = uuid4()

    async def cancelled_detect(text: str) -> dict:
        raise asyncio.CancelledError

    monkeypatch.setattr(repre_guard_client, "detect", cancelled_detect)

    with pytest.raises(asyncio.CancelledError):
        await detect(
            payload=DetectionRequest(text=LONG_TEXT),
            db=db_session,
            current_actor=actor,
            idempotency_key=key,
        )

    request_record = db_session.scalar(
        select(DetectionRequestRecord).where(DetectionRequestRecord.idempotency_key == str(key))
    )
    assert request_record is not None
    assert request_record.status == "processing"
