import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api.v1.auth import guest_login, login, logout, read_current_user, register_user
from app.core.rate_limit import auth_rate_limiter
from app.api.v1.detections import detect
from app.api.v1.quota import get_quota
from app.db.deps import get_current_actor
from app.schemas.auth import GuestTokenRequest, LoginRequest, RegisterRequest
from app.schemas.detection import DetectionRequest
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

    me = await read_current_user(current_user=created_user)
    assert me.email == unique_email
    assert getattr(me.role, "value", me.role) == "INDIVIDUAL"


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


@pytest.mark.anyio
async def test_guest_token_reuse_preserves_guest_quota(db_session):
    first_guest = await guest_login()
    first_actor = get_current_actor(db=db_session, token=first_guest.access_token)

    await detect(payload=DetectionRequest(text=LONG_TEXT), db=db_session, current_actor=first_actor)
    first_quota = await get_quota(db=db_session, current_actor=first_actor)

    renewed_guest = await guest_login(GuestTokenRequest(guest_id=first_guest.guest_id))
    renewed_actor = get_current_actor(db=db_session, token=renewed_guest.access_token)
    renewed_quota = await get_quota(db=db_session, current_actor=renewed_actor)

    assert first_guest.guest_id
    assert renewed_guest.guest_id == first_guest.guest_id
    assert renewed_actor.actor_type == "guest"
    assert renewed_actor.actor_id == first_actor.actor_id == first_guest.guest_id
    assert first_quota.used_today > 0
    assert renewed_quota.used_today == first_quota.used_today
    assert renewed_quota.remaining == first_quota.remaining


@pytest.mark.anyio
async def test_logout_clears_auth_cookie():
    response = await logout()
    assert response.status_code == 204
    assert "aid_access_token=" in response.headers.get("set-cookie", "")
