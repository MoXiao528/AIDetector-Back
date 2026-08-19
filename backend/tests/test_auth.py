import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette.requests import Request
from uuid import UUID

from app.api.v1.auth import login, logout, read_current_user, register_user
from app.core.rate_limit import auth_rate_limiter
from app.core.roles import UserRole
from app.core.security import create_access_token
from app.db.deps import get_current_actor, get_current_user
from app.db.session import get_db
from app.main import app
from app.models.guest_session import GuestSession
from app.schemas.auth import LoginRequest, RegisterRequest
from app.services.repre_guard_client import repre_guard_client

LONG_TEXT = (
    "Guest quota continuity check requires enough visible characters to pass the minimum detection threshold "
    "while still remaining deterministic for quota accounting across repeated guest sessions. "
) * 3
LONG_TEXT = LONG_TEXT.strip()


@pytest.fixture(autouse=True)
def mock_repre_guard(monkeypatch):
    auth_rate_limiter.reset()

    async def fake_detect(text: str) -> dict:
        return {
            "score": 2.8,
            "threshold": 2.4924452377944597,
            "label": "AI",
            "model_name": "Qwen/Qwen2.5-7B",
            "score_type": "raw_logit",
        }

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)
    yield


def build_request(ip: str = "127.0.0.1") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [],
            "client": (ip, 12345),
        }
    )


def _response_value(payload: dict, snake_case: str, camel_case: str) -> str:
    return str(payload.get(snake_case) or payload.get(camel_case) or "")


def _recovery_cookie(response, access_token: str) -> tuple[str, str]:
    cookies = [(name, value) for name, value in response.cookies.items() if value != access_token]
    assert cookies, "guest response must set a recovery cookie distinct from the access token"
    return cookies[0]


@pytest.mark.anyio
async def test_register_login_and_me(db_session, unique_email):
    created_user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    assert created_user.email == unique_email
    assert created_user.name == unique_email

    response = Response()
    token_resp = await login(LoginRequest(identifier=unique_email, password="StrongPass!23"), response, db_session)
    assert token_resp.token_type == "bearer"
    assert token_resp.access_token
    assert "aid_access_token=" in response.headers.get("set-cookie", "")

    authenticated_user = get_current_user(db=db_session, token=token_resp.access_token)
    me = await read_current_user(current_user=authenticated_user)
    assert me.email == unique_email
    assert getattr(me.role, "value", me.role) == "INDIVIDUAL"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/me",
        "/api/v1/keys",
        "/api/v1/admin/status",
    ],
)
async def test_numeric_guest_token_is_rejected_by_user_routes(path, db_session, unique_email):
    victim = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    victim.role = UserRole.SYS_ADMIN
    db_session.commit()
    db_session.refresh(victim)
    assert victim.id == 1

    guest_token = create_access_token(
        subject="1",
        extra_claims={"sub_type": "guest", "guest_id": "1"},
    )
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            response = client.get(path, headers={"Authorization": f"Bearer {guest_token}"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize("resolver", [get_current_user, get_current_actor])
@pytest.mark.parametrize(
    ("subject", "extra_claims"),
    [
        ("1", None),
        ("1", {"sub_type": "service"}),
        ("not-a-number", {"sub_type": "user"}),
    ],
    ids=["missing-sub-type", "unknown-sub-type", "non-numeric-user-sub"],
)
async def test_token_claims_fail_closed(resolver, subject, extra_claims, db_session, unique_email):
    victim = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    assert victim.id == 1
    token = create_access_token(subject=subject, extra_claims=extra_claims)

    with pytest.raises(HTTPException) as exc_info:
        resolver(db=db_session, token=token)

    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_login_returns_generic_invalid_credentials_error(db_session, unique_email):
    await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    with pytest.raises(HTTPException) as missing_user_error:
        await login(
            LoginRequest(identifier="missing@example.com", password="StrongPass!23"),
            Response(),
            db_session,
        )

    with pytest.raises(HTTPException) as wrong_password_error:
        await login(
            LoginRequest(identifier=unique_email, password="WrongPass!23"),
            Response(),
            db_session,
        )

    assert missing_user_error.value.status_code == 401
    assert wrong_password_error.value.status_code == 401
    assert missing_user_error.value.detail["code"] == "AUTH_INVALID_CREDENTIALS"
    assert wrong_password_error.value.detail["code"] == "AUTH_INVALID_CREDENTIALS"


@pytest.mark.anyio
async def test_login_rate_limit_blocks_excess_attempts(db_session, unique_email):
    await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    request = build_request()

    for _ in range(5):
        with pytest.raises(HTTPException) as exc_info:
            await login(
                LoginRequest(identifier=unique_email, password="WrongPass!23"),
                Response(),
                db_session,
                request=request,
            )
        assert exc_info.value.status_code == 401

    with pytest.raises(HTTPException) as rate_limit_error:
        await login(
            LoginRequest(identifier=unique_email, password="WrongPass!23"),
            Response(),
            db_session,
            request=request,
        )

    assert rate_limit_error.value.status_code == 429
    assert rate_limit_error.value.detail["code"] == "AUTH_RATE_LIMITED"


@pytest.mark.anyio
async def test_register_allows_name_with_at_symbol(db_session, unique_email):
    created_user = await register_user(
        RegisterRequest(email=unique_email, password="StrongPass!23", name="abc@def"),
        db_session,
    )
    assert created_user.name == "abc@def"


@pytest.mark.anyio
async def test_register_user_has_30000_default_credits(db_session, unique_email):
    created_user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    assert created_user.credits == 30000


def test_guest_id_without_recovery_cookie_is_rejected(db_session):
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/auth/guest",
                json={"guest_id": "known-victim-guest-id"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_guest_login_issues_server_uuid_and_http_only_recovery_cookie(db_session):
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/auth/guest")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    access_token = _response_value(payload, "access_token", "accessToken")
    guest_id = _response_value(payload, "guest_id", "guestId")
    assert access_token
    assert UUID(guest_id).version == 4
    _recovery_cookie(response, access_token)
    assert "httponly" in response.headers.get("set-cookie", "").lower()


def test_guest_bearer_without_recovery_cookie_does_not_create_session(db_session):
    guest_id = "5db45cb0-c886-4d12-88ac-d798461264a7"
    access_token = create_access_token(
        subject=guest_id,
        extra_claims={"sub_type": "guest", "sid": guest_id},
    )
    before_count = db_session.scalar(select(func.count()).select_from(GuestSession))
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/auth/guest",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    finally:
        app.dependency_overrides.clear()

    after_count = db_session.scalar(select(func.count()).select_from(GuestSession))
    assert response.status_code == 401
    assert response.json()["code"] == "GUEST_SESSION_INVALID"
    assert "aid_guest_refresh" not in response.headers.get("set-cookie", "")
    assert after_count == before_count


def test_guest_recovery_cookie_rotation_preserves_identity_quota_and_history(db_session):
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            first_response = client.post("/api/v1/auth/guest")
            assert first_response.status_code == 200
            first_payload = first_response.json()
            first_token = _response_value(first_payload, "access_token", "accessToken")
            guest_id = _response_value(first_payload, "guest_id", "guestId")
            cookie_name, first_cookie = _recovery_cookie(first_response, first_token)

            detect_response = client.post(
                "/api/v1/detect",
                json={"text": LONG_TEXT},
                headers={"Authorization": f"Bearer {first_token}"},
            )
            assert detect_response.status_code == 200
            detection_id = detect_response.json().get("detectionId") or detect_response.json().get("detection_id")

            first_quota_response = client.get(
                "/api/v1/quota",
                headers={"Authorization": f"Bearer {first_token}"},
            )
            first_history_response = client.get(
                "/api/v1/detections/",
                headers={"Authorization": f"Bearer {first_token}"},
            )
            assert first_quota_response.status_code == 200
            assert first_history_response.status_code == 200

            first_renewal = client.post("/api/v1/auth/guest")
            assert first_renewal.status_code == 200
            first_renewal_payload = first_renewal.json()
            first_renewal_token = _response_value(first_renewal_payload, "access_token", "accessToken")
            assert _response_value(first_renewal_payload, "guest_id", "guestId") == guest_id
            renewed_cookie_name, renewed_cookie = _recovery_cookie(first_renewal, first_renewal_token)
            assert renewed_cookie_name == cookie_name
            assert renewed_cookie != first_cookie

            second_renewal = client.post("/api/v1/auth/guest")
            assert second_renewal.status_code == 200
            second_renewal_payload = second_renewal.json()
            latest_token = _response_value(second_renewal_payload, "access_token", "accessToken")
            assert _response_value(second_renewal_payload, "guest_id", "guestId") == guest_id

            renewed_quota_response = client.get(
                "/api/v1/quota",
                headers={"Authorization": f"Bearer {latest_token}"},
            )
            renewed_history_response = client.get(
                "/api/v1/detections/",
                headers={"Authorization": f"Bearer {latest_token}"},
            )

            assert renewed_quota_response.status_code == 200
            assert renewed_history_response.status_code == 200
            first_quota = first_quota_response.json()
            renewed_quota = renewed_quota_response.json()
            assert renewed_quota.get("usedToday", renewed_quota.get("used_today")) == first_quota.get(
                "usedToday", first_quota.get("used_today")
            )
            assert renewed_quota["remaining"] == first_quota["remaining"]
            assert renewed_history_response.json()["total"] == 1
            renewed_item = renewed_history_response.json()["items"][0]
            assert renewed_item["id"] == detection_id

        with TestClient(app) as replay_client:
            replay_client.cookies.set(cookie_name, first_cookie)
            replay_response = replay_client.post("/api/v1/auth/guest")
    finally:
        app.dependency_overrides.clear()

    assert replay_response.status_code == 401
    assert "aid_guest_refresh" not in replay_response.headers.get("set-cookie", "")


def test_legacy_guest_token_is_rejected_by_current_actor(db_session):
    legacy_token = create_access_token(
        subject="6c6f4ad0-1176-4778-bd70-f5f2752bd4f0",
        extra_claims={
            "sub_type": "guest",
            "guest_id": "6c6f4ad0-1176-4778-bd70-f5f2752bd4f0",
        },
    )
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/detections/",
                headers={"Authorization": f"Bearer {legacy_token}"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


@pytest.mark.anyio
async def test_logout_clears_auth_cookie():
    response = await logout()
    assert response.status_code == 204
    assert "aid_access_token=" in response.headers.get("set-cookie", "")
