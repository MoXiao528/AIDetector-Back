"""Tests for history API endpoints."""

import pytest
from fastapi import HTTPException

from app.api.v1.auth import register_user
from app.api.v1.history import (
    batch_delete_histories,
    claim_guest_history,
    clear_all_histories,
    create_history,
    delete_history,
    get_history,
    list_histories,
    update_history,
)
from app.core.security import create_access_token
from app.models.detection import Detection
from app.schemas.auth import RegisterRequest
from app.schemas.history import (
    Analysis,
    BatchDeleteRequest,
    ClaimGuestHistoryRequest,
    HistoryRecordCreate,
    HistoryRecordUpdate,
    Sentence,
    Summary,
)


@pytest.mark.anyio
async def test_create_history(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    payload = HistoryRecordCreate(
        title="Scan Record · 2026-02-11 13:15:21",
        functions=["scan"],
        input_text="The quick brown fox jumps over the lazy dog.",
        editor_html="<p>The quick brown fox jumps over the lazy dog.</p>",
        analysis=Analysis(
            summary=Summary(ai=45, mixed=25, human=30),
            sentences=[
                Sentence(
                    id="sent-1",
                    text="The quick brown fox jumps over the lazy dog.",
                    raw="The quick brown fox jumps over the lazy dog.",
                    type="ai",
                    probability=0.85,
                    score=85,
                    reason="Highly structured phrasing",
                    suggestion="Use a more natural rewrite",
                )
            ],
            translation="",
            polish="",
            citations=[],
            ai_likely_count=1,
            highlighted_html='<span class="bg-amber-100">The quick brown fox jumps over the lazy dog.</span>',
        ),
    )

    response = await create_history(payload=payload, db=db_session, current_user=user)

    assert response.id > 0
    assert response.user_id == user.id
    assert response.title == "Scan Record · 2026-02-11 13:15:21"
    assert response.functions == ["scan"]
    assert response.input_text == "The quick brown fox jumps over the lazy dog."
    assert response.analysis is not None
    assert response.analysis.summary.ai == 45


@pytest.mark.anyio
async def test_list_histories(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    for index in range(3):
        payload = HistoryRecordCreate(
            title=f"Record {index + 1}",
            functions=["scan"],
            input_text=f"Text {index + 1}",
            editor_html=f"<p>Text {index + 1}</p>",
            analysis=Analysis(
                summary=Summary(ai=30, mixed=20, human=50),
                sentences=[],
                translation="",
                polish="",
                citations=[],
                ai_likely_count=0,
                highlighted_html="",
            ),
        )
        await create_history(payload=payload, db=db_session, current_user=user)

    response = await list_histories(
        db=db_session,
        current_user=user,
        page=1,
        per_page=10,
        sort="created_at",
        order="desc",
    )

    assert response.total == 3
    assert len(response.items) == 3
    assert response.page == 1
    assert response.per_page == 10
    assert response.total_pages == 1


@pytest.mark.anyio
async def test_list_histories_pagination(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    for index in range(5):
        payload = HistoryRecordCreate(
            title=f"Record {index + 1}",
            functions=["scan"],
            input_text=f"Text {index + 1}",
            editor_html=f"<p>Text {index + 1}</p>",
            analysis=Analysis(
                summary=Summary(ai=20, mixed=20, human=60),
                sentences=[],
                translation="",
                polish="",
                citations=[],
                ai_likely_count=0,
                highlighted_html="",
            ),
        )
        await create_history(payload=payload, db=db_session, current_user=user)

    page1 = await list_histories(db=db_session, current_user=user, page=1, per_page=2)
    page2 = await list_histories(db=db_session, current_user=user, page=2, per_page=2)
    page3 = await list_histories(db=db_session, current_user=user, page=3, per_page=2)

    assert page1.total == 5
    assert len(page1.items) == 2
    assert page1.total_pages == 3
    assert len(page2.items) == 2
    assert len(page3.items) == 1


@pytest.mark.anyio
async def test_get_history(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    payload = HistoryRecordCreate(
        title="Test Record",
        functions=["scan", "polish"],
        input_text="Test text",
        editor_html="<p>Test text</p>",
        analysis=Analysis(
            summary=Summary(ai=10, mixed=20, human=70),
            sentences=[],
            translation="",
            polish="",
            citations=[],
            ai_likely_count=0,
            highlighted_html="",
        ),
    )
    created = await create_history(payload=payload, db=db_session, current_user=user)

    response = await get_history(history_id=created.id, db=db_session, current_user=user)

    assert response.id == created.id
    assert response.title == "Test Record"
    assert response.functions == ["scan", "polish"]


@pytest.mark.anyio
async def test_get_history_not_found(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    with pytest.raises(HTTPException) as exc_info:
        await get_history(history_id=99999, db=db_session, current_user=user)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "HISTORY_NOT_FOUND"


@pytest.mark.anyio
async def test_update_history(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    payload = HistoryRecordCreate(
        title="Old Title",
        functions=["scan"],
        input_text="Test",
        editor_html="<p>Test</p>",
        analysis=Analysis(
            summary=Summary(ai=15, mixed=15, human=70),
            sentences=[],
            translation="",
            polish="",
            citations=[],
            ai_likely_count=0,
            highlighted_html="",
        ),
    )
    created = await create_history(payload=payload, db=db_session, current_user=user)

    response = await update_history(
        history_id=created.id,
        payload=HistoryRecordUpdate(title="New Title"),
        db=db_session,
        current_user=user,
    )

    assert response.id == created.id
    assert response.title == "New Title"


@pytest.mark.anyio
async def test_delete_history(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    payload = HistoryRecordCreate(
        title="To Delete",
        functions=["scan"],
        input_text="Test",
        editor_html="<p>Test</p>",
        analysis=Analysis(
            summary=Summary(ai=15, mixed=15, human=70),
            sentences=[],
            translation="",
            polish="",
            citations=[],
            ai_likely_count=0,
            highlighted_html="",
        ),
    )
    created = await create_history(payload=payload, db=db_session, current_user=user)

    await delete_history(history_id=created.id, db=db_session, current_user=user)

    with pytest.raises(HTTPException) as exc_info:
        await get_history(history_id=created.id, db=db_session, current_user=user)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_batch_delete(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    ids = []
    for index in range(3):
        payload = HistoryRecordCreate(
            title=f"Record {index + 1}",
            functions=["scan"],
            input_text=f"Text {index + 1}",
            editor_html=f"<p>Text {index + 1}</p>",
            analysis=Analysis(
                summary=Summary(ai=30, mixed=20, human=50),
                sentences=[],
                translation="",
                polish="",
                citations=[],
                ai_likely_count=0,
                highlighted_html="",
            ),
        )
        created = await create_history(payload=payload, db=db_session, current_user=user)
        ids.append(created.id)

    response = await batch_delete_histories(
        payload=BatchDeleteRequest(ids=ids[:2]),
        db=db_session,
        current_user=user,
    )

    assert response.deleted_count == 2
    assert response.failed_ids == []

    list_response = await list_histories(
        db=db_session,
        current_user=user,
        page=1,
        per_page=20,
        sort="created_at",
        order="desc",
    )
    assert list_response.total == 1


@pytest.mark.anyio
async def test_clear_all(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    for index in range(5):
        payload = HistoryRecordCreate(
            title=f"Record {index + 1}",
            functions=["scan"],
            input_text=f"Text {index + 1}",
            editor_html=f"<p>Text {index + 1}</p>",
            analysis=Analysis(
                summary=Summary(ai=20, mixed=20, human=60),
                sentences=[],
                translation="",
                polish="",
                citations=[],
                ai_likely_count=0,
                highlighted_html="",
            ),
        )
        await create_history(payload=payload, db=db_session, current_user=user)

    response = await clear_all_histories(db=db_session, current_user=user)
    assert response.deleted_count == 5

    list_response = await list_histories(
        db=db_session,
        current_user=user,
        page=1,
        per_page=20,
        sort="created_at",
        order="desc",
    )
    assert list_response.total == 0


@pytest.mark.anyio
async def test_permission_control(db_session, unique_email):
    user_a = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    user_b = await register_user(RegisterRequest(email=f"other-{unique_email}", password="StrongPass!23"), db_session)

    payload = HistoryRecordCreate(
        title="User A Record",
        functions=["scan"],
        input_text="User A text",
        editor_html="<p>User A text</p>",
        analysis=Analysis(
            summary=Summary(ai=20, mixed=20, human=60),
            sentences=[],
            translation="",
            polish="",
            citations=[],
            ai_likely_count=0,
            highlighted_html="",
        ),
    )
    record_a = await create_history(payload=payload, db=db_session, current_user=user_a)

    with pytest.raises(HTTPException) as exc_info:
        await get_history(history_id=record_a.id, db=db_session, current_user=user_b)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_limit_enforcement(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    for index in range(102):
        payload = HistoryRecordCreate(
            title=f"Record {index + 1}",
            functions=["scan"],
            input_text=f"Text {index + 1}",
            editor_html=f"<p>Text {index + 1}</p>",
            analysis=Analysis(
                summary=Summary(ai=20, mixed=20, human=60),
                sentences=[],
                translation="",
                polish="",
                citations=[],
                ai_likely_count=0,
                highlighted_html="",
            ),
        )
        await create_history(payload=payload, db=db_session, current_user=user)

    list_response = await list_histories(
        db=db_session,
        current_user=user,
        page=1,
        per_page=100,
        sort="created_at",
        order="desc",
    )
    assert list_response.total == 100
    assert len(list_response.items) == 100


@pytest.mark.anyio
async def test_claim_guest_history_assigns_guest_records_to_user(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    guest_id = "guest-claim-test"
    guest_token = create_access_token(
        subject=guest_id,
        extra_claims={"sub_type": "guest", "guest_id": guest_id},
    )
    detection = Detection(
        user_id=None,
        actor_type="guest",
        actor_id=guest_id,
        chars_used=12,
        title="Guest Record",
        input_text="Guest history",
        editor_html="<p>Guest history</p>",
        functions_used=["scan"],
        result_label="ai",
        score=0.82,
        meta_json={
            "analysis": {
                "summary": {"ai": 82, "mixed": 10, "human": 8},
                "sentences": [],
                "translation": "",
                "polish": "",
                "citations": [],
                "ai_likely_count": 0,
                "highlighted_html": "",
            }
        },
    )
    db_session.add(detection)
    db_session.commit()

    response = await claim_guest_history(
        payload=ClaimGuestHistoryRequest(guest_token=guest_token),
        db=db_session,
        current_user=user,
    )

    assert response.claimed_count == 1

    histories = await list_histories(
        db=db_session,
        current_user=user,
        page=1,
        per_page=20,
        sort="created_at",
        order="desc",
    )
    assert histories.total == 1
    assert histories.items[0].title == "Guest Record"


@pytest.mark.anyio
async def test_create_history_rejects_blank_input_text(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    payload = HistoryRecordCreate(
        title="Blank Record",
        functions=["scan"],
        input_text=" ",
        editor_html="<p><br></p>",
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_history(payload=payload, db=db_session, current_user=user)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "INVALID_HISTORY_DATA"


@pytest.mark.anyio
async def test_list_histories_filters_blank_records(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    valid = Detection(
        user_id=user.id,
        actor_type="user",
        actor_id=str(user.id),
        chars_used=8,
        title="Valid Record",
        input_text="Real text",
        editor_html="<p>Real text</p>",
        functions_used=["scan"],
        result_label="ai",
        score=0.77,
        meta_json={
            "analysis": {
                "summary": {"ai": 77, "mixed": 13, "human": 10},
                "sentences": [],
                "translation": "",
                "polish": "",
                "citations": [],
                "ai_likely_count": 0,
                "highlighted_html": "",
            }
        },
    )
    blank = Detection(
        user_id=user.id,
        actor_type="user",
        actor_id=str(user.id),
        chars_used=0,
        title="Blank Record",
        input_text="",
        editor_html="<p><br></p>",
        functions_used=["scan"],
        result_label="human",
        score=0.0,
        meta_json={
            "analysis": {
                "summary": {"ai": 0, "mixed": 0, "human": 100},
                "sentences": [],
                "translation": "",
                "polish": "",
                "citations": [],
                "ai_likely_count": 0,
                "highlighted_html": "",
            }
        },
    )
    db_session.add_all([valid, blank])
    db_session.commit()

    response = await list_histories(
        db=db_session,
        current_user=user,
        page=1,
        per_page=20,
        sort="created_at",
        order="desc",
    )

    assert response.total == 1
    assert len(response.items) == 1
    assert response.items[0].title == "Valid Record"
