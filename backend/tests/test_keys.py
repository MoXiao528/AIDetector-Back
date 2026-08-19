from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.v1.auth import register_user
from app.core.roles import UserRole
from app.core.security import create_access_token
from app.db.session import get_db
from app.main import app
from app.models.api_key import APIKey
from app.schemas.auth import RegisterRequest
from app.services.repre_guard_client import repre_guard_client


DETECT_TEXT = (
    "This API-key authorization test uses enough text to exercise the real detection route while keeping "
    "the downstream detector isolated at the external-service boundary. "
) * 2


@pytest.fixture()
def route_client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def _create_admin_credentials(db_session, route_client: TestClient, email: str):
    user = await register_user(
        payload=RegisterRequest(email=email, password="StrongPass!23"),
        db=db_session,
    )
    user.role = UserRole.SYS_ADMIN
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    access_token = create_access_token(
        subject=str(user.id),
        extra_claims={"sub_type": "user"},
    )
    user_headers = {"Authorization": f"Bearer {access_token}"}
    create_response = route_client.post(
        "/api/v1/keys",
        json={"name": "Authorization boundary key"},
        headers=user_headers,
    )
    assert create_response.status_code == 201, create_response.text

    created = create_response.json()
    api_key_headers = {"X-API-Key": created["key"]}
    return user_headers, api_key_headers, created


@pytest.mark.anyio
async def test_api_key_metadata_usage_and_revocation(db_session, unique_email, route_client):
    user_headers, api_key_headers, created = await _create_admin_credentials(db_session, route_client, unique_email)
    expected_scopes = ["detect:write", "quota:read"]
    expires_at = datetime.fromisoformat(created["expiresAt"].replace("Z", "+00:00"))
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    assert created["scopes"] == expected_scopes
    assert expires_at > datetime.now(timezone.utc)
    assert created["lastUsedAt"] is None
    assert created["revokedAt"] is None

    listed_response = route_client.get("/api/v1/keys", headers=user_headers)
    assert listed_response.status_code == 200
    listed = next(item for item in listed_response.json() if item["id"] == created["id"])
    assert listed["scopes"] == expected_scopes
    assert listed["expiresAt"] == created["expiresAt"]
    assert listed["revokedAt"] is None

    use_response = route_client.get("/api/v1/quota", headers=api_key_headers)
    assert use_response.status_code == 200, use_response.text
    used = next(
        item for item in route_client.get("/api/v1/keys", headers=user_headers).json() if item["id"] == created["id"]
    )
    assert used["lastUsedAt"] is not None

    revoke_response = route_client.delete(f"/api/v1/keys/{created['id']}", headers=user_headers)
    assert revoke_response.status_code == 204
    revoked = next(
        item for item in route_client.get("/api/v1/keys", headers=user_headers).json() if item["id"] == created["id"]
    )
    assert revoked["status"] == "inactive"
    assert revoked["revokedAt"] is not None

    rejected = route_client.get("/api/v1/quota", headers=api_key_headers)
    assert rejected.status_code == 401
    assert rejected.json()["code"] == "INVALID_API_KEY"


@pytest.mark.anyio
async def test_expired_api_key_is_rejected_without_recording_usage(db_session, unique_email, route_client):
    user_headers, api_key_headers, created = await _create_admin_credentials(db_session, route_client, unique_email)
    api_key = db_session.get(APIKey, created["id"])
    api_key.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.add(api_key)
    db_session.commit()

    before = next(
        item for item in route_client.get("/api/v1/keys", headers=user_headers).json() if item["id"] == created["id"]
    )
    response = route_client.get("/api/v1/quota", headers=api_key_headers)
    after = next(
        item for item in route_client.get("/api/v1/keys", headers=user_headers).json() if item["id"] == created["id"]
    )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_API_KEY"
    assert after["lastUsedAt"] == before["lastUsedAt"]


@pytest.mark.anyio
async def test_api_key_limit_and_revocation_releases_a_slot(db_session, unique_email, route_client):
    user_headers, _, first = await _create_admin_credentials(db_session, route_client, unique_email)

    for index in range(2, 6):
        response = route_client.post(
            "/api/v1/keys",
            json={"name": f"Key {index}"},
            headers=user_headers,
        )
        assert response.status_code == 201, response.text

    limit_response = route_client.post(
        "/api/v1/keys",
        json={"name": "Key 6"},
        headers=user_headers,
    )
    listed_at_limit = route_client.get("/api/v1/keys", headers=user_headers)

    assert limit_response.status_code == 409
    assert limit_response.json()["code"] == "API_KEY_LIMIT_REACHED"
    assert len(listed_at_limit.json()) == 5

    revoke_response = route_client.delete(f"/api/v1/keys/{first['id']}", headers=user_headers)
    replacement_response = route_client.post(
        "/api/v1/keys",
        json={"name": "Replacement after revocation"},
        headers=user_headers,
    )
    after_replacement = route_client.get("/api/v1/keys", headers=user_headers).json()

    assert revoke_response.status_code == 204
    assert replacement_response.status_code == 201, replacement_response.text
    assert sum(item["status"] == "active" and item["revokedAt"] is None for item in after_replacement) == 5


@pytest.mark.anyio
async def test_api_key_cannot_mint_a_replacement_key(db_session, unique_email, route_client):
    user_headers, api_key_headers, _ = await _create_admin_credentials(db_session, route_client, unique_email)
    before = route_client.get("/api/v1/keys", headers=user_headers)

    response = route_client.post(
        "/api/v1/keys",
        json={"name": "Replacement key"},
        headers=api_key_headers,
    )
    after = route_client.get("/api/v1/keys", headers=user_headers)

    assert response.status_code == 403
    assert [item["id"] for item in after.json()] == [item["id"] for item in before.json()]


@pytest.mark.anyio
async def test_api_key_cannot_update_profile(db_session, unique_email, route_client):
    user_headers, api_key_headers, _ = await _create_admin_credentials(db_session, route_client, unique_email)
    before = route_client.get("/api/v1/auth/me", headers=user_headers)

    response = route_client.patch(
        "/api/v1/auth/me/profile",
        json={"firstName": "Compromised"},
        headers=api_key_headers,
    )
    after = route_client.get("/api/v1/auth/me", headers=user_headers)

    assert response.status_code == 403
    assert after.json()["profile"] == before.json()["profile"]


@pytest.mark.anyio
async def test_api_key_cannot_delete_owned_history(db_session, unique_email, route_client):
    user_headers, api_key_headers, _ = await _create_admin_credentials(db_session, route_client, unique_email)
    created = route_client.post(
        "/api/v1/history",
        json={
            "title": "Owned history",
            "functions": ["scan"],
            "inputText": "History that must survive an API-key deletion attempt.",
            "editorHtml": "<p>History that must survive an API-key deletion attempt.</p>",
        },
        headers=user_headers,
    )
    assert created.status_code == 201, created.text
    history_id = created.json()["id"]

    response = route_client.delete(f"/api/v1/history/{history_id}", headers=api_key_headers)
    after = route_client.get(f"/api/v1/history/{history_id}", headers=user_headers)

    assert response.status_code == 403
    assert after.status_code == 200


@pytest.mark.anyio
async def test_api_key_cannot_create_team(db_session, unique_email, route_client):
    user_headers, api_key_headers, _ = await _create_admin_credentials(db_session, route_client, unique_email)
    payload = {"name": f"Boundary team {unique_email}"}

    response = route_client.post("/api/v1/teams", json=payload, headers=api_key_headers)
    session_control = route_client.post("/api/v1/teams", json=payload, headers=user_headers)

    assert response.status_code == 403
    assert session_control.status_code == 201


@pytest.mark.anyio
async def test_sysadmin_api_key_cannot_access_admin_routes(db_session, unique_email, route_client):
    _, api_key_headers, _ = await _create_admin_credentials(db_session, route_client, unique_email)

    response = route_client.get("/api/v1/admin/status", headers=api_key_headers)

    assert response.status_code == 403


@pytest.mark.anyio
async def test_api_key_cannot_read_detection_history(db_session, unique_email, route_client):
    _, api_key_headers, _ = await _create_admin_credentials(db_session, route_client, unique_email)

    for path in ("/api/v1/detections/", "/api/v1/scan/history", "/api/scan/history"):
        response = route_client.get(path, headers=api_key_headers)
        assert response.status_code == 403, path


@pytest.mark.anyio
async def test_mixed_session_and_api_key_credentials_fail_closed(db_session, unique_email, route_client):
    user_headers, _, _ = await _create_admin_credentials(db_session, route_client, unique_email)
    mixed_headers = {**user_headers, "X-API-Key": "revoked-or-invalid-key"}

    response = route_client.get("/api/v1/detections/", headers=mixed_headers)

    assert response.status_code == 400
    assert response.json()["code"] == "AMBIGUOUS_CREDENTIALS"


@pytest.mark.anyio
async def test_api_key_keeps_detect_and_quota_access(db_session, unique_email, route_client, monkeypatch):
    _, api_key_headers, _ = await _create_admin_credentials(db_session, route_client, unique_email)

    async def fake_detect(text: str) -> dict:
        return {
            "score": 0.003,
            "threshold": 0.0028,
            "label": "AI",
            "model_name": "route-model",
            "score_type": "probability",
        }

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)
    detect_paths = (
        "/api/v1/detect",
        "/api/v1/scan/detect",
        "/api/v1/scan",
        "/api/scan/detect",
        "/api/scan",
    )
    detect_responses = [
        route_client.post(
            path,
            json={"text": DETECT_TEXT, "functions": ["scan"]},
            headers=api_key_headers,
        )
        for path in detect_paths
    ]
    quota_response = route_client.get("/api/v1/quota", headers=api_key_headers)
    self_test_response = route_client.get("/api/v1/keys/self-test", headers=api_key_headers)

    assert all(response.status_code == 200 for response in detect_responses), [
        (path, response.status_code, response.text) for path, response in zip(detect_paths, detect_responses)
    ]
    assert quota_response.status_code == 200, quota_response.text
    assert self_test_response.status_code == 200, self_test_response.text
    assert self_test_response.json() == {"message": "API key is valid"}
