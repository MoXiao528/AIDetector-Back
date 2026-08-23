from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models.detection import Detection
from app.models.detection_request import DetectionRequest as DetectionRequestRecord
from app.models.quota_usage import QuotaUsage
from app.services.quota_service import get_effective_used_today, get_today_bounds, get_used_today


def test_get_used_today_prefers_ledger_when_present(db_session):
    actor_type = "guest"
    actor_id = "quota-ledger-test"
    start, end = get_today_bounds()

    db_session.add(
        Detection(
            actor_type=actor_type,
            actor_id=actor_id,
            chars_used=1200,
            input_text="manual history",
            editor_html="<p>manual history</p>",
            functions_used=["scan"],
            result_label="human",
            score=0.0,
            meta_json={"analysis": {"summary": {"ai": 0, "mixed": 0, "human": 100}}},
        )
    )
    db_session.add(
        QuotaUsage(
            actor_type=actor_type,
            actor_id=actor_id,
            usage_date=start.date(),
            limit=5000,
            used=300,
        )
    )
    db_session.commit()

    used_today = get_used_today(db_session, actor_type=actor_type, actor_id=actor_id, start_time=start, end_time=end)

    assert used_today == 300


def test_get_used_today_falls_back_to_detection_rows_without_ledger(db_session):
    actor_type = "guest"
    actor_id = "quota-fallback-test"
    start, end = get_today_bounds()

    db_session.add(
        Detection(
            actor_type=actor_type,
            actor_id=actor_id,
            chars_used=1200,
            input_text="legacy detection",
            editor_html="<p>legacy detection</p>",
            functions_used=["scan"],
            result_label="human",
            score=0.0,
            meta_json={"analysis": {"summary": {"ai": 0, "mixed": 0, "human": 100}}},
        )
    )
    db_session.commit()

    used_today = get_used_today(db_session, actor_type=actor_type, actor_id=actor_id, start_time=start, end_time=end)

    assert used_today == 1200


def test_effective_usage_includes_only_unexpired_processing_reservation(db_session):
    actor_type = "guest"
    actor_id = "quota-reservation-test"
    start, end = get_today_bounds()
    now = datetime.now(timezone.utc)
    db_session.add(
        QuotaUsage(
            actor_type=actor_type,
            actor_id=actor_id,
            usage_date=start.date(),
            limit=5000,
            used=300,
        )
    )
    db_session.add(
        DetectionRequestRecord(
            actor_type=actor_type,
            actor_id=actor_id,
            idempotency_key=str(uuid4()),
            request_hash="a" * 64,
            status="processing",
            usage_date=start.date(),
            reserved_chars=700,
            owner_token=str(uuid4()),
            lease_expires_at=now + timedelta(minutes=5),
        )
    )
    db_session.commit()

    assert get_effective_used_today(
        db_session,
        actor_type=actor_type,
        actor_id=actor_id,
        start_time=start,
        end_time=end,
    ) == 1000

    request_record = db_session.query(DetectionRequestRecord).filter_by(actor_id=actor_id).one()
    request_record.lease_expires_at = now - timedelta(seconds=1)
    db_session.commit()

    assert get_effective_used_today(
        db_session,
        actor_type=actor_type,
        actor_id=actor_id,
        start_time=start,
        end_time=end,
    ) == 300
