from app.models.detection import Detection
from app.models.quota_usage import QuotaUsage
from app.services.quota_service import get_today_bounds, get_used_today


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
