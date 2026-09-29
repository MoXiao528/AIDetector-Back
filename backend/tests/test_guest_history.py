"""Same-session history restoration and guest mutation isolation."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import guest_history
from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.session import get_db
from app.main import app
from app.models.detection import Detection
from app.models.guest_session import GuestSession
from app.models.quota_usage import QuotaUsage
from app.models.user import User
from app.services.history_service import HistoryService


@pytest.fixture
def guest_history_data(db_session, unique_email):
    now = datetime.now(timezone.utc)
    sessions = [GuestSession(id=str(uuid4()), refresh_token_hash=uuid4().hex,
                             expires_at=now + timedelta(days=1)) for _ in range(2)]
    user = User(email=unique_email, name=unique_email, password_hash="unused-test-hash")
    db_session.add_all([*sessions, user])
    db_session.flush()
    records = []
    for index, (actor_type, actor_id, user_id) in enumerate([
        *[("guest", sessions[0].id, None)] * 3,
        ("guest", sessions[1].id, None),
        ("user", str(user.id), user.id),
        ("guest", sessions[0].id, user.id),
    ]):
        records.append(Detection(
            actor_type=actor_type, actor_id=actor_id, user_id=user_id, chars_used=200,
            title=f"Record {index}", input_text=f"Stored text {index}", editor_html=f"<p>Stored text {index}</p>",
            functions_used=["scan"], score=0.8, result_label="ai",
            created_at=now + timedelta(seconds=index),
            meta_json={"analysis": {"summary": {"ai": 80, "human": 20}, "sentences": [],
                                    "ai_likely_count": 0, "highlighted_html": "<p>Stored result</p>"},
                       "evidence": {"status": "failed", "artifactVersion": "1" * 64, "featureSchemaVersion": 1,
                                    "route": None, "quality": {"level": "unavailable", "coverage": 0.0, "reasons": ["timeout"]},
                                    "signals": [], "patterns": None}},
        ))
    usage = QuotaUsage(actor_type="guest", actor_id=sessions[0].id,
                       usage_date=now.date(), limit=5000, used=600)
    db_session.add_all([*records, usage])
    db_session.commit()
    tokens = [create_access_token(subject=s.id, extra_claims={"sub_type": "guest", "sid": s.id}) for s in sessions]
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        with TestClient(app) as client:
            yield client, records, sessions, tokens, user, usage
    finally:
        app.dependency_overrides.clear()


def test_guest_history_restores_and_persists_management_without_quota_changes(guest_history_data, db_session, monkeypatch):
    client, records, _, tokens, _, usage = guest_history_data
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", "serve")
    headers = {"Authorization": f"Bearer {tokens[0]}"}
    base = "/api/v1/guest/history"
    response = client.get(base, params={"per_page": 2}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["perPage"], body["totalPages"]) == (3, 2, 2)
    assert len(body["items"]) == 2
    assert body["items"][0]["userId"] is None
    detail = client.get(f"{base}/{records[0].id}", headers=headers).json()
    assert detail["inputText"] == records[0].input_text
    assert detail["editorHtml"] == records[0].editor_html
    assert detail["analysis"]["summary"] == {"ai": 80, "human": 20}
    assert detail["evidence"] == records[0].meta_json["evidence"]

    lock_calls = []
    original_lock = guest_history._get_active_guest_session_id

    def track_lock(*args, **kwargs):
        lock_calls.append(kwargs.get("lock"))
        return original_lock(*args, **kwargs)

    monkeypatch.setattr(guest_history, "_get_active_guest_session_id", track_lock)
    updated = client.patch(f"{base}/{records[0].id}", json={"title": "Renamed", "is_pinned": True}, headers=headers)
    assert updated.status_code == 200
    assert updated.json()["isPinned"] is True
    refreshed = client.get(base, params={"q": "Renamed", "pinned": True}, headers=headers).json()
    assert refreshed["total"] == 1
    assert refreshed["items"][0]["title"] == "Renamed"
    for record in records[3:]:
        assert client.get(f"{base}/{record.id}", headers=headers).status_code == 404
        assert client.patch(f"{base}/{record.id}", json={"title": "Must not change"}, headers=headers).status_code == 404
        assert client.delete(f"{base}/{record.id}", headers=headers).status_code == 404
    batch = client.post(f"{base}/batch-delete", json={"ids": [records[0].id, records[3].id]}, headers=headers)
    assert batch.status_code == 200
    assert batch.json() == {"deletedCount": 1, "failedIds": [records[3].id]}
    assert client.delete(f"{base}/{records[1].id}", headers=headers).status_code == 204
    assert client.delete(base, headers=headers).json() == {"deletedCount": 1}
    assert client.get(base, headers=headers).json()["total"] == 0
    assert client.get(f"{base}/{records[0].id}", headers=headers).status_code == 404
    assert lock_calls and all(lock_calls)
    db_session.refresh(usage)
    assert usage.used == 600
    assert all(db_session.get(Detection, record.id) is not None for record in records[3:])


def test_guest_history_rejects_non_guest_and_consumed_sessions(guest_history_data, db_session):
    client, records, sessions, tokens, user, _ = guest_history_data
    base = "/api/v1/guest/history"
    guest_headers = {"Authorization": f"Bearer {tokens[0]}"}
    user_token = create_access_token(subject=str(user.id), extra_claims={"sub_type": "user"})
    assert client.get(base).status_code == 401
    assert client.get(base, headers={"Cookie": f"aid_access_token={user_token}"}).status_code == 401
    assert client.get(base, headers={"Authorization": f"Bearer {user_token}"}).status_code == 401
    assert client.get(base, headers={"X-API-Key": "not-a-guest-token"}).status_code == 403
    assert client.get(base, headers={**guest_headers, "X-API-Key": "mixed"}).status_code == 400
    assert client.get(base, headers={"Cookie": f"aid_access_token={user_token}", "X-API-Key": "mixed"}).status_code == 400
    assert client.get("/api/v1/history", headers=guest_headers).status_code == 401
    assert client.post(base, json={}, headers=guest_headers).status_code == 405
    # SQLite drops timezone information; expire fixture rows before the existing claim UPDATE.
    db_session.expire_all()
    claim = client.post("/api/v1/history/claim-guest", json={"guest_token": tokens[0]},
                        headers={"Authorization": f"Bearer {user_token}"})
    assert claim.status_code == 200
    for method, path, payload in [
        ("GET", base, None), ("GET", f"{base}/{records[0].id}", None),
        ("PATCH", f"{base}/{records[0].id}", {"title": "Blocked"}),
        ("DELETE", f"{base}/{records[0].id}", None),
        ("POST", f"{base}/batch-delete", {"ids": [records[0].id]}), ("DELETE", base, None),
    ]:
        assert client.request(method, path, json=payload, headers=guest_headers).status_code == 401
    sessions[1].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    assert client.get(base, headers={"Authorization": f"Bearer {tokens[1]}"}).status_code == 401
    db_session.refresh(records[0])
    assert records[0].user_id == user.id
    assert records[0].title == "Record 0"


def test_guest_history_rechecks_revocation_under_lock_before_writing(guest_history_data, db_session, monkeypatch):
    client, records, sessions, tokens, _, _ = guest_history_data
    original_resolver = guest_history._resolve_active_guest_session_id

    def revoke_after_initial_auth(db, token_data):
        guest_id = original_resolver(db, token_data)
        sessions[0].revoked_at = datetime.now(timezone.utc)
        db_session.commit()
        return guest_id

    monkeypatch.setattr(guest_history, "_resolve_active_guest_session_id", revoke_after_initial_auth)
    response = client.patch(f"/api/v1/guest/history/{records[0].id}", json={"title": "Blocked"},
                            headers={"Authorization": f"Bearer {tokens[0]}"})
    assert response.status_code == 401
    db_session.refresh(records[0])
    assert records[0].title == "Record 0"


def test_history_service_requires_an_explicit_owner(db_session):
    with pytest.raises(ValueError, match="owner"):
        HistoryService(db_session).clear_all_histories(None)
