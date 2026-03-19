import pytest
from fastapi import HTTPException

from app.api.v1.admin import (
    adjust_admin_user_credits,
    admin_status,
    delete_admin_detection,
    get_admin_detection,
    get_admin_overview,
    get_admin_user,
    list_admin_detections,
    list_admin_users,
    update_admin_user,
)
from app.api.v1.auth import register_user
from app.core.roles import UserRole
from app.schemas.admin import AdminOverviewPreset, AdminUserCreditsAdjustRequest, AdminUserUpdateRequest
from app.schemas.auth import RegisterRequest
from app.services.detection_service import DetectionService


def _build_analysis_dict():
    return {
        "summary": {"ai": 90, "mixed": 5, "human": 5},
        "sentences": [
            {
                "id": "sent-1",
                "text": "AI generated sentence.",
                "raw": "AI generated sentence.",
                "type": "ai",
                "probability": 0.9,
                "score": 90,
                "reason": "Structured wording",
                "suggestion": "Use more natural phrasing",
            }
        ],
        "translation": "",
        "polish": "",
        "citations": [],
        "ai_likely_count": 1,
        "highlighted_html": "<span>AI generated sentence.</span>",
    }


async def _create_user(db_session, email: str, role: UserRole = UserRole.INDIVIDUAL):
    user = await register_user(RegisterRequest(email=email, password="StrongPass!23"), db_session)
    user.role = role
    db_session.commit()
    db_session.refresh(user)
    return user


def _create_detection(db_session, user, *, label: str = "ai", score: float = 0.91):
    service = DetectionService(db_session)
    return service.create_detection(
        user_id=user.id,
        text="This is a generated test text.",
        label=label,
        score=score,
        functions_used=["scan"],
        actor_type="user",
        actor_id=str(user.id),
        analysis=_build_analysis_dict(),
        commit=True,
    )


@pytest.mark.anyio
async def test_admin_status_and_overview(db_session, unique_email):
    admin = await _create_user(db_session, unique_email, role=UserRole.SYS_ADMIN)
    normal = await _create_user(db_session, f"member-{unique_email}")
    _create_detection(db_session, normal)

    status_response = await admin_status(_=admin)
    overview = await get_admin_overview(db=db_session, _=admin, preset=AdminOverviewPreset.WEEK)

    assert status_response.message == "admin ok"
    assert overview.period.preset == AdminOverviewPreset.WEEK
    assert overview.period.granularity == "day"
    assert overview.summary.total_users == 2
    assert overview.summary.active_users == 2
    assert overview.summary.new_users == 2
    assert overview.summary.detections == 1
    assert len(overview.series) == 7
    assert sum(item.new_users for item in overview.series) == 2
    assert sum(item.detections for item in overview.series) == 1
    assert overview.recent_users[0].id in {admin.id, normal.id}
    assert len(overview.recent_detections) == 1


@pytest.mark.anyio
async def test_list_and_update_admin_users(db_session, unique_email):
    admin = await _create_user(db_session, unique_email, role=UserRole.SYS_ADMIN)
    member = await _create_user(db_session, f"member-{unique_email}")

    listed = await list_admin_users(
        db=db_session,
        _=admin,
        page=1,
        page_size=20,
        search="member-",
        system_role=None,
        is_active=True,
        plan_tier=None,
        sort="createdAt",
        order="desc",
    )
    assert listed.total == 1
    assert listed.items[0].email == member.email

    updated = await update_admin_user(
        user_id=member.id,
        payload=AdminUserUpdateRequest(system_role=UserRole.TEAM_ADMIN, plan_tier="team", is_active=False),
        db=db_session,
        _=admin,
    )
    assert updated.system_role == UserRole.TEAM_ADMIN
    assert updated.plan_tier == "team"
    assert updated.is_active is False

    detail = await get_admin_user(user_id=member.id, db=db_session, _=admin)
    assert detail.profile is not None


@pytest.mark.anyio
async def test_adjust_admin_user_credits(db_session, unique_email):
    admin = await _create_user(db_session, unique_email, role=UserRole.SYS_ADMIN)
    member = await _create_user(db_session, f"member-{unique_email}")

    response = await adjust_admin_user_credits(
        user_id=member.id,
        payload=AdminUserCreditsAdjustRequest(delta=5000, reason="Grant"),
        db=db_session,
        _=admin,
    )
    assert response.user_id == member.id
    assert response.credits_total == 35000
    assert response.credits_remaining == 35000

    with pytest.raises(HTTPException) as exc:
        await adjust_admin_user_credits(
            user_id=member.id,
            payload=AdminUserCreditsAdjustRequest(delta=-40000, reason="Too much"),
            db=db_session,
            _=admin,
        )
    assert exc.value.status_code == 422


@pytest.mark.anyio
async def test_list_get_and_delete_admin_detections(db_session, unique_email):
    admin = await _create_user(db_session, unique_email, role=UserRole.SYS_ADMIN)
    member = await _create_user(db_session, f"member-{unique_email}")
    detection = _create_detection(db_session, member)

    listed = await list_admin_detections(
        db=db_session,
        _=admin,
        page=1,
        page_size=20,
        search="generated",
        user_id=member.id,
        actor_type="user",
        label="ai",
        function_name="scan",
        date_from=None,
        date_to=None,
        sort="createdAt",
        order="desc",
    )
    assert listed.total == 1
    assert listed.items[0].id == detection.id
    assert listed.items[0].user_email == member.email

    detail = await get_admin_detection(detection_id=detection.id, db=db_session, _=admin)
    assert detail.input_text == "This is a generated test text."
    assert detail.analysis is not None
    assert detail.analysis.summary.ai == 90

    await delete_admin_detection(detection_id=detection.id, db=db_session, _=admin)
    after_delete = await list_admin_detections(
        db=db_session,
        _=admin,
        page=1,
        page_size=20,
        search=None,
        user_id=member.id,
        actor_type="user",
        label=None,
        function_name=None,
        date_from=None,
        date_to=None,
        sort="createdAt",
        order="desc",
    )
    assert after_delete.total == 0
