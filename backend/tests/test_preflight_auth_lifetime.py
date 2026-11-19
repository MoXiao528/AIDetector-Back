from datetime import datetime, timedelta, timezone
from threading import get_ident

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.api.v1 import detections
from app.core.security import create_access_token, hash_api_key
from app.db import deps
from app.db.base_class import Base
from app.db.session import get_db
from app.main import app
from app.models.api_key import APIKey, APIKeyStatus
from app.models.detection import Detection
from app.models.detection_request import DetectionRequest
from app.models.guest_session import GuestSession
from app.models.quota_usage import QuotaUsage
from app.models.revoked_access_token import RevokedAccessToken
from app.models.user import User


@pytest.fixture
def preflight_auth(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
        poolclass=QueuePool, pool_size=1, max_overflow=0,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    with sessions() as db:
        user = User(name="reader", email="preflight@example.invalid", password_hash="unused")
        db.add(user)
        db.flush()
        user_id = user.id
        db.add(GuestSession(id="auth-guest", refresh_token_hash="d" * 64, expires_at=now + timedelta(days=1)))
        key = APIKey(user_id=user_id, name="preflight", key_hash=hash_api_key("test-preflight-key"),
                     status=APIKeyStatus.ACTIVE, expires_at=now + timedelta(days=1))
        db.add(key)
        db.commit()
        key_id = key.id

    user_token = create_access_token(str(user_id), extra_claims={"sub_type": "user"})
    guest_token = create_access_token("auth-guest", extra_claims={"sub_type": "guest", "sid": "auth-guest"})
    calls = []
    connection_events = []
    event.listen(engine, "checkout", lambda *args: connection_events.append(("checkout", get_ident())))
    event.listen(engine, "checkin", lambda *args: connection_events.append(("checkin", get_ident())))

    def db_override():
        with sessions() as db:
            yield db

    def count(text, **kwargs):
        # With a single pooled connection this also proves concurrent preflights can authenticate during tokenization.
        assert engine.pool.checkedout() == 0
        assert connection_events[-1][0] == "checkin"
        calls.append(text)
        return 256

    def unexpected(*args, **kwargs):
        pytest.fail("Preflight must not invoke detection or reserve quota")

    monkeypatch.setattr(detections, "count_tokens", count)
    monkeypatch.setattr(detections.repre_guard_client, "detect", unexpected)
    monkeypatch.setattr(detections, "reserve_detection_request", unexpected)
    app.dependency_overrides[get_db] = db_override
    try:
        with TestClient(app) as client:
            yield {
                "client": client, "sessions": sessions, "engine": engine, "calls": calls,
                "events": connection_events, "user_id": user_id, "key_id": key_id,
                "user_token": user_token, "guest_token": guest_token,
            }
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


@pytest.mark.parametrize("credential", ["guest", "user", "cookie", "api_key"])
def test_real_authorization_returns_connection_before_tokenization(preflight_auth, credential):
    fixture = preflight_auth
    headers = {}
    if credential == "cookie":
        fixture["client"].cookies.set("aid_access_token", fixture["user_token"])
    elif credential == "api_key":
        headers["X-API-Key"] = "test-preflight-key"
    else:
        headers["Authorization"] = f"Bearer {fixture[f'{credential}_token']}"
    response = fixture["client"].post("/api/v1/detect/preflight", json={"text": "ready text"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert fixture["calls"] == ["ready text"]
    assert fixture["engine"].pool.checkedout() == 0
    events = fixture["events"]
    assert len(events) >= 2 and len(events) % 2 == 0
    assert all(events[i] == ("checkout", events[i + 1][1]) and events[i + 1][0] == "checkin"
               for i in range(0, len(events), 2))
    with fixture["sessions"]() as db:
        assert all(db.query(model).count() == 0 for model in (Detection, DetectionRequest, QuotaUsage))
        assert (db.get(APIKey, fixture["key_id"]).last_used_at is not None) == (credential == "api_key")


@pytest.mark.parametrize("failure", [
    "missing", "invalid_jwt", "expired_jwt", "revoked_user_token", "inactive_user",
    "revoked_guest", "expired_guest", "missing_guest", "guest_subject_mismatch",
    "revoked_api_key", "expired_api_key", "inactive_api_key", "inactive_key_owner",
    "ambiguous_bearer", "ambiguous_cookie", "missing_scope",
])
def test_rejected_credentials_release_connection_and_never_tokenize(preflight_auth, monkeypatch, failure):
    fixture = preflight_auth
    headers = {"Authorization": f"Bearer {fixture['user_token']}"}
    now = datetime.now(timezone.utc)
    with fixture["sessions"]() as db:
        if failure == "missing":
            headers = {}
        elif failure == "invalid_jwt":
            headers = {"Authorization": "Bearer invalid-token"}
        elif failure == "expired_jwt":
            token = create_access_token(str(fixture["user_id"]), expires_delta=timedelta(seconds=-1), extra_claims={"sub_type": "user"})
            headers = {"Authorization": f"Bearer {token}"}
        elif failure == "revoked_user_token":
            jti = jwt.decode(fixture["user_token"], options={"verify_signature": False})["jti"]
            db.add(RevokedAccessToken(jti=jti, expires_at=now + timedelta(hours=1)))
        elif failure == "inactive_user":
            db.get(User, fixture["user_id"]).is_active = False
        elif "guest" in failure:
            headers = {"Authorization": f"Bearer {fixture['guest_token']}"}
            if failure == "revoked_guest":
                db.get(GuestSession, "auth-guest").revoked_at = now
            elif failure == "expired_guest":
                db.get(GuestSession, "auth-guest").expires_at = now - timedelta(seconds=1)
            elif failure == "missing_guest":
                db.delete(db.get(GuestSession, "auth-guest"))
            else:
                token = create_access_token("other-guest", extra_claims={"sub_type": "guest", "sid": "auth-guest"})
                headers = {"Authorization": f"Bearer {token}"}
        else:
            headers = {"X-API-Key": "test-preflight-key"}
            if failure == "revoked_api_key":
                db.get(APIKey, fixture["key_id"]).revoked_at = now
            elif failure == "expired_api_key":
                db.get(APIKey, fixture["key_id"]).expires_at = now - timedelta(seconds=1)
            elif failure == "inactive_api_key":
                db.get(APIKey, fixture["key_id"]).status = APIKeyStatus.INACTIVE
            elif failure == "inactive_key_owner":
                db.get(User, fixture["user_id"]).is_active = False
            elif failure == "ambiguous_bearer":
                headers["Authorization"] = f"Bearer {fixture['user_token']}"
            elif failure == "ambiguous_cookie":
                fixture["client"].cookies.set("aid_access_token", fixture["user_token"])
            elif failure == "missing_scope":
                monkeypatch.setattr(deps, "API_KEY_SCOPES", ("quota:read",))
        db.commit()
    response = fixture["client"].post("/api/v1/detect/preflight", json={"text": "ready text"}, headers=headers)
    expected = 403 if failure == "missing_scope" else 400 if failure.startswith("ambiguous") else 401
    assert response.status_code == expected
    assert fixture["calls"] == []
    assert fixture["engine"].pool.checkedout() == 0
