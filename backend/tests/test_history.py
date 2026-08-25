"""Tests for history API endpoints."""

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v1.auth import register_user
from app.api.v1.history import (
    batch_delete_histories,
    clear_all_histories,
    create_history,
    delete_history,
    get_history,
    list_histories,
    update_history,
)
from app.core.security import create_access_token
from app.db.session import get_db
from app.main import app
from app.models.detection import Detection
from app.models.guest_session import GuestSession
from app.schemas.auth import RegisterRequest
from app.schemas.history import (
    Analysis,
    BatchDeleteRequest,
    HistoryRecordCreate,
    HistoryRecordUpdate,
    Sentence,
    Summary,
)


def test_legacy_mixed_analysis_projects_to_binary_without_mutating_source():
    legacy = {
        "summary": {"ai": 55, "mixed": 20, "human": 25},
        "sentences": [
            {
                "id": "sent-legacy",
                "text": "Legacy borderline sentence.",
                "raw": "Legacy borderline sentence.",
                "type": "mixed",
                "probability": 0.5,
                "score": 50,
                "reason": "Legacy reason",
                "suggestion": "Legacy suggestion",
            }
        ],
        "translation": "",
        "polish": "",
        "citations": [],
        "ai_likely_count": 1,
        "highlighted_html": "<p>Legacy borderline sentence.</p>",
    }

    analysis = Analysis.model_validate(legacy)
    public_payload = analysis.model_dump(mode="json")

    assert analysis.summary.ai == 55
    assert analysis.summary.human == 45
    assert analysis.sentences[0].type == "human"
    assert analysis.ai_likely_count == 0
    assert "mixed" not in json.dumps(public_payload).casefold()
    assert legacy["summary"]["mixed"] == 20
    assert legacy["sentences"][0]["type"] == "mixed"


@pytest.mark.anyio
async def test_create_history(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    payload = HistoryRecordCreate(
        title="Scan Record · 2026-02-11 13:15:21",
        functions=["scan"],
        input_text="The quick brown fox jumps over the lazy dog.",
        editor_html="<p>The quick brown fox jumps over the lazy dog.</p>",
        analysis=Analysis(
            summary=Summary(ai=45, human=55),
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
    assert response.is_pinned is False
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
                summary=Summary(ai=30, human=70),
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
async def test_list_histories_searches_title_and_input(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    for title, text in [
        ("Project Alpha", "ordinary text"),
        ("Project Beta", "contains needle phrase"),
        ("Needle Title", "different body"),
    ]:
        payload = HistoryRecordCreate(
            title=title,
            functions=["scan"],
            input_text=text,
            editor_html=f"<p>{text}</p>",
            analysis=Analysis(
                summary=Summary(ai=30, human=70),
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
        q="needle",
    )

    assert response.total == 2
    assert {item.title for item in response.items} == {"Project Beta", "Needle Title"}

    empty = await list_histories(
        db=db_session,
        current_user=user,
        page=1,
        per_page=10,
        sort="created_at",
        order="desc",
        q="missing",
    )
    assert empty.total == 0


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
                summary=Summary(ai=20, human=80),
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
            summary=Summary(ai=10, human=90),
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
            summary=Summary(ai=15, human=85),
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
async def test_update_history_pin_state_and_list_pinned_first(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    first = await create_history(
        payload=HistoryRecordCreate(
            title="First",
            functions=["scan"],
            input_text="First text",
            editor_html="<p>First text</p>",
            analysis=Analysis(
                summary=Summary(ai=15, human=85),
                sentences=[],
                translation="",
                polish="",
                citations=[],
                ai_likely_count=0,
                highlighted_html="",
            ),
        ),
        db=db_session,
        current_user=user,
    )
    second = await create_history(
        payload=HistoryRecordCreate(
            title="Second",
            functions=["scan"],
            input_text="Second text",
            editor_html="<p>Second text</p>",
            analysis=Analysis(
                summary=Summary(ai=15, human=85),
                sentences=[],
                translation="",
                polish="",
                citations=[],
                ai_likely_count=0,
                highlighted_html="",
            ),
        ),
        db=db_session,
        current_user=user,
    )

    response = await update_history(
        history_id=first.id,
        payload=HistoryRecordUpdate(is_pinned=True),
        db=db_session,
        current_user=user,
    )

    assert response.is_pinned is True

    listed = await list_histories(
        db=db_session,
        current_user=user,
        page=1,
        per_page=10,
        sort="created_at",
        order="desc",
    )
    assert listed.items[0].id == first.id
    assert listed.items[0].is_pinned is True
    assert {item.id for item in listed.items} == {first.id, second.id}

    pinned_only = await list_histories(
        db=db_session,
        current_user=user,
        page=1,
        per_page=10,
        sort="created_at",
        order="desc",
        pinned=True,
    )
    assert pinned_only.total == 1
    assert pinned_only.items[0].id == first.id


def test_history_update_accepts_snake_and_camel_pinned_payloads():
    snake_payload = HistoryRecordUpdate.model_validate({"is_pinned": True})
    camel_payload = HistoryRecordUpdate.model_validate({"isPinned": False})

    assert snake_payload.is_pinned is True
    assert camel_payload.is_pinned is False


@pytest.mark.anyio
async def test_delete_history(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    payload = HistoryRecordCreate(
        title="To Delete",
        functions=["scan"],
        input_text="Test",
        editor_html="<p>Test</p>",
        analysis=Analysis(
            summary=Summary(ai=15, human=85),
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
                summary=Summary(ai=30, human=70),
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
                summary=Summary(ai=20, human=80),
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
            summary=Summary(ai=20, human=80),
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
                summary=Summary(ai=20, human=80),
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
async def test_limit_enforcement_preserves_pinned_history(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    pinned_record = None
    for index in range(100):
        created = await create_history(
            payload=HistoryRecordCreate(
                title=f"Record {index + 1}",
                functions=["scan"],
                input_text=f"Text {index + 1}",
                editor_html=f"<p>Text {index + 1}</p>",
                analysis=Analysis(
                    summary=Summary(ai=20, human=80),
                    sentences=[],
                    translation="",
                    polish="",
                    citations=[],
                    ai_likely_count=0,
                    highlighted_html="",
                ),
            ),
            db=db_session,
            current_user=user,
        )
        if index == 0:
            pinned_record = created

    assert pinned_record is not None
    await update_history(
        history_id=pinned_record.id,
        payload=HistoryRecordUpdate(is_pinned=True),
        db=db_session,
        current_user=user,
    )

    for index in range(2):
        await create_history(
            payload=HistoryRecordCreate(
                title=f"New Record {index + 1}",
                functions=["scan"],
                input_text=f"New text {index + 1}",
                editor_html=f"<p>New text {index + 1}</p>",
                analysis=Analysis(
                    summary=Summary(ai=20, human=80),
                    sentences=[],
                    translation="",
                    polish="",
                    citations=[],
                    ai_likely_count=0,
                    highlighted_html="",
                ),
            ),
            db=db_session,
            current_user=user,
        )

    list_response = await list_histories(
        db=db_session,
        current_user=user,
        page=1,
        per_page=100,
        sort="created_at",
        order="desc",
    )
    assert list_response.total == 100
    assert any(item.id == pinned_record.id for item in list_response.items)


@pytest.mark.anyio
async def test_claim_guest_history_assigns_guest_records_to_user(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    user_token = create_access_token(
        subject=str(user.id),
        extra_claims={"sub_type": "user"},
    )
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            guest_response = client.post("/api/v1/auth/guest")
            assert guest_response.status_code == 200
            guest_payload = guest_response.json()
            guest_id = str(guest_payload.get("guest_id") or guest_payload.get("guestId") or "")
            guest_token = str(guest_payload.get("access_token") or guest_payload.get("accessToken") or "")
            assert guest_id
            assert guest_token

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

            guest_history_response = client.get(
                "/api/v1/detections/",
                headers={"Authorization": f"Bearer {guest_token}"},
            )
            assert guest_history_response.status_code == 200
            assert guest_history_response.json()["total"] == 1

            claim_response = client.post(
                "/api/v1/history/claim-guest",
                json={"guest_token": guest_token},
                headers={"Authorization": f"Bearer {user_token}"},
            )
            assert claim_response.status_code == 200
            claim_payload = claim_response.json()
            assert claim_payload.get("claimedCount", claim_payload.get("claimed_count")) == 1

            db_session.refresh(detection)
            guest_session = db_session.get(GuestSession, guest_id)
            assert detection.user_id == user.id
            assert detection.actor_type == "user"
            assert detection.actor_id == str(user.id)
            assert guest_session is not None
            assert guest_session.revoked_at is not None

            for path in ("/api/v1/detections/", "/api/v1/scan/history", "/api/scan/history"):
                old_guest_response = client.get(
                    path,
                    headers={"Authorization": f"Bearer {guest_token}"},
                )
                assert old_guest_response.status_code == 401

            repeated_claim_response = client.post(
                "/api/v1/history/claim-guest",
                json={"guest_token": guest_token},
                headers={"Authorization": f"Bearer {user_token}"},
            )
            assert repeated_claim_response.status_code == 400
            assert repeated_claim_response.json()["code"] == "INVALID_GUEST_TOKEN"
    finally:
        app.dependency_overrides.clear()

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
async def test_guest_detection_history_hides_records_already_owned_by_a_user(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            guest_response = client.post("/api/v1/auth/guest")
            assert guest_response.status_code == 200
            guest_payload = guest_response.json()
            guest_id = str(guest_payload.get("guest_id") or guest_payload.get("guestId") or "")
            guest_token = str(guest_payload.get("access_token") or guest_payload.get("accessToken") or "")

            db_session.add(
                Detection(
                    user_id=user.id,
                    actor_type="guest",
                    actor_id=guest_id,
                    chars_used=12,
                    title="Already Claimed",
                    input_text="Already claimed guest history",
                    editor_html="<p>Already claimed guest history</p>",
                    functions_used=["scan"],
                    result_label="ai",
                    score=0.82,
                    meta_json=None,
                )
            )
            db_session.commit()

            response = client.get(
                "/api/v1/detections/",
                headers={"Authorization": f"Bearer {guest_token}"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.anyio
async def test_legacy_guest_token_cannot_claim_history(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    user_token = create_access_token(
        subject=str(user.id),
        extra_claims={"sub_type": "user"},
    )
    legacy_guest_id = "09a2aaad-ddf6-410d-85ed-424a5c212c7f"
    legacy_guest_token = create_access_token(
        subject=legacy_guest_id,
        extra_claims={
            "sub_type": "guest",
            "guest_id": legacy_guest_id,
        },
    )
    detection = Detection(
        user_id=None,
        actor_type="guest",
        actor_id=legacy_guest_id,
        chars_used=12,
        title="Legacy Guest Record",
        input_text="Legacy guest history",
        editor_html="<p>Legacy guest history</p>",
        functions_used=["scan"],
        result_label="ai",
        score=0.82,
        meta_json=None,
    )
    db_session.add(detection)
    db_session.commit()
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/history/claim-guest",
                json={"guest_token": legacy_guest_token},
                headers={"Authorization": f"Bearer {user_token}"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_GUEST_TOKEN"
    db_session.refresh(detection)
    assert detection.user_id is None


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
