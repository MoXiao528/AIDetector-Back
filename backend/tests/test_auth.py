from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette.requests import Request

from app.api.v1.auth import login, read_current_user, register_user
from app.core.config import get_settings
from app.core.rate_limit import auth_rate_limiter
from app.core.roles import UserRole
from app.core.security import create_access_token
from app.db.deps import get_current_actor, get_current_user
from app.db.session import get_db
from app.main import app
from app.models.detection import Detection
from app.models.guest_session import GuestSession
from app.schemas.auth import LoginRequest, RegisterRequest
from app.services.repre_guard_client import repre_guard_client

LONG_TEXT = (
    "Guest quota continuity check requires enough visible characters to pass the minimum detection threshold "
    "while still remaining deterministic for quota accounting across repeated guest sessions. "
) * 3
LONG_TEXT = LONG_TEXT.strip()
REQUIRED_ACCESS_TOKEN_CLAIMS = {"iss", "aud", "iat", "jti", "exp"}


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


def _issue_guest_session(client: TestClient) -> tuple[str, str, str, str]:
    response = client.post("/api/v1/auth/guest")
    assert response.status_code == 200
    payload = response.json()
    access_token = _response_value(payload, "access_token", "accessToken")
    guest_id = _response_value(payload, "guest_id", "guestId")
    cookie_name, refresh_cookie = _recovery_cookie(response, access_token)
    assert access_token
    assert guest_id
    return guest_id, access_token, cookie_name, refresh_cookie


def _issue_user_tokens(client: TestClient, email: str, *, count: int = 1) -> list[str]:
    register_response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPass!23"},
    )
    assert register_response.status_code == 201, register_response.text

    tokens = []
    for _ in range(count):
        login_response = client.post(
            "/api/v1/auth/login",
            json={"identifier": email, "password": "StrongPass!23"},
        )
        assert login_response.status_code == 200, login_response.text
        token = _response_value(login_response.json(), "access_token", "accessToken")
        assert token
        tokens.append(token)

    client.cookies.clear()
    return tokens


def _decode_unverified(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


def _resign_claims(claims: dict) -> str:
    return jwt.encode(claims, get_settings().secret_key, algorithm="HS256")


def _add_detection(
    db_session,
    *,
    actor_type: str,
    actor_id: str,
    user_id: int | None = None,
    input_text: str = "preview-secret-input",
) -> None:
    db_session.add(
        Detection(
            user_id=user_id,
            actor_type=actor_type,
            actor_id=actor_id,
            chars_used=len(input_text),
            title="Preview secret title",
            input_text=input_text,
            editor_html=f"<p>{input_text}</p>",
            functions_used=["scan"],
            result_label="human",
            score=0.1,
        )
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

    authenticated_user = get_current_user(db=db_session, token=token_resp.access_token)
    me = await read_current_user(current_user=authenticated_user)
    assert me.email == unique_email
    assert getattr(me.role, "value", me.role) == "INDIVIDUAL"


def test_login_access_token_has_required_claims_and_is_accepted(db_session, unique_email):
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            (access_token,) = _issue_user_tokens(client, unique_email)
            claims = _decode_unverified(access_token)
            me_response = client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    finally:
        app.dependency_overrides.clear()

    assert REQUIRED_ACCESS_TOKEN_CLAIMS <= claims.keys()
    assert isinstance(claims["iss"], str) and claims["iss"]
    assert isinstance(claims["aud"], str) and claims["aud"]
    assert isinstance(claims["iat"], int)
    assert isinstance(claims["jti"], str) and claims["jti"]
    assert isinstance(claims["exp"], int)
    assert claims["iat"] < claims["exp"]
    assert me_response.status_code == 200


@pytest.mark.parametrize(
    "invalid_claim",
    [
        "missing-iss",
        "missing-aud",
        "missing-iat",
        "missing-jti",
        "missing-exp",
        "wrong-iss",
        "wrong-aud",
        "malformed-jti",
        "future-iat",
        "expired-exp",
    ],
)
def test_invalid_access_token_claims_are_rejected(invalid_claim, db_session, unique_email):
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            (access_token,) = _issue_user_tokens(client, unique_email)
            claims = _decode_unverified(access_token)
            missing_claims = REQUIRED_ACCESS_TOKEN_CLAIMS - claims.keys()
            assert not missing_claims, f"login token missing required claims: {sorted(missing_claims)}"

            if invalid_claim.startswith("missing-"):
                claims.pop(invalid_claim.removeprefix("missing-"))
            elif invalid_claim == "wrong-iss":
                claims["iss"] = f"{claims['iss']}-wrong"
            elif invalid_claim == "wrong-aud":
                claims["aud"] = f"{claims['aud']}-wrong"
            elif invalid_claim == "malformed-jti":
                claims["jti"] = "not-a-uuid"
            elif invalid_claim == "future-iat":
                claims["iat"] = int(datetime.now(timezone.utc).timestamp()) + 300
            else:
                claims["exp"] = int(datetime.now(timezone.utc).timestamp()) - 1

            invalid_token = _resign_claims(claims)
            response = client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {invalid_token}"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


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


def test_guest_session_preview_without_credentials_is_inactive_and_minimal(db_session):
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/auth/guest")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"active": False, "historyCount": 0}


@pytest.mark.anyio
async def test_guest_session_preview_cookie_counts_only_unclaimed_records_for_that_guest(
    db_session,
    unique_email,
):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            guest_id, _, _, _ = _issue_guest_session(client)
            _add_detection(
                db_session,
                actor_type="guest",
                actor_id=guest_id,
                input_text="target-guest-secret",
            )
            _add_detection(
                db_session,
                actor_type="guest",
                actor_id="another-guest-id",
                input_text="other-guest-secret",
            )
            _add_detection(
                db_session,
                actor_type="guest",
                actor_id=guest_id,
                user_id=user.id,
                input_text="already-owned-secret",
            )
            _add_detection(
                db_session,
                actor_type="user",
                actor_id=guest_id,
                input_text="wrong-actor-secret",
            )
            db_session.commit()

            response = client.get("/api/v1/auth/guest")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"active": True, "historyCount": 1}
    assert "secret" not in response.text.lower()
    assert "items" not in response.json()


def test_guest_session_preview_accepts_active_bearer_without_refresh_cookie(db_session):
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as issuing_client:
            guest_id, access_token, _, _ = _issue_guest_session(issuing_client)
            _add_detection(
                db_session,
                actor_type="guest",
                actor_id=guest_id,
            )
            db_session.commit()

        with TestClient(app) as bearer_client:
            response = bearer_client.get(
                "/api/v1/auth/guest",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"active": True, "historyCount": 1}


@pytest.mark.parametrize(
    "wrong_bearer",
    [
        "not-a-valid-jwt",
        create_access_token(subject="1", extra_claims={"sub_type": "user"}),
    ],
    ids=["invalid-bearer", "user-bearer"],
)
def test_guest_session_preview_falls_back_to_valid_cookie_when_bearer_is_not_guest(
    wrong_bearer,
    db_session,
):
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            guest_id, _, _, _ = _issue_guest_session(client)
            _add_detection(
                db_session,
                actor_type="guest",
                actor_id=guest_id,
            )
            db_session.commit()

            response = client.get(
                "/api/v1/auth/guest",
                headers={"Authorization": f"Bearer {wrong_bearer}"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"active": True, "historyCount": 1}


@pytest.mark.parametrize("terminal_state", ["revoked", "expired"])
def test_guest_session_preview_terminal_session_is_inactive(
    terminal_state,
    db_session,
):
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            guest_id, access_token, _, _ = _issue_guest_session(client)
            _add_detection(
                db_session,
                actor_type="guest",
                actor_id=guest_id,
            )
            guest_session = db_session.get(GuestSession, guest_id)
            assert guest_session is not None
            if terminal_state == "revoked":
                guest_session.revoked_at = datetime.now(timezone.utc)
            else:
                guest_session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db_session.commit()

            response = client.get(
                "/api/v1/auth/guest",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"active": False, "historyCount": 0}


def test_discard_guest_session_revokes_bearer_clears_refresh_cookie_and_is_idempotent(db_session):
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            guest_id, access_token, cookie_name, refresh_cookie = _issue_guest_session(client)

            discard_response = client.delete(
                "/api/v1/auth/guest",
                headers={"Authorization": f"Bearer {access_token}"},
            )

            client.cookies.set(
                cookie_name,
                refresh_cookie,
                path="/api/v1/auth/guest",
            )
            repeated_discard_response = client.delete(
                "/api/v1/auth/guest",
                headers={"Authorization": f"Bearer {access_token}"},
            )

            old_bearer_response = client.get(
                "/api/v1/quota",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        with TestClient(app) as replay_client:
            replay_client.cookies.set(
                cookie_name,
                refresh_cookie,
                path="/api/v1/auth/guest",
            )
            old_refresh_response = replay_client.post("/api/v1/auth/guest")
    finally:
        app.dependency_overrides.clear()

    assert discard_response.status_code == 204
    set_cookie = discard_response.headers.get("set-cookie", "").lower()
    assert "aid_guest_refresh=" in set_cookie
    assert "max-age=0" in set_cookie
    assert "path=/api/v1/auth/guest" in set_cookie

    db_session.expire_all()
    discarded_session = db_session.get(GuestSession, guest_id)
    assert discarded_session is not None
    assert discarded_session.revoked_at is not None

    assert repeated_discard_response.status_code == 204
    repeated_set_cookie = repeated_discard_response.headers.get("set-cookie", "").lower()
    assert "aid_guest_refresh=" in repeated_set_cookie
    assert "max-age=0" in repeated_set_cookie
    assert "path=/api/v1/auth/guest" in repeated_set_cookie
    assert old_bearer_response.status_code == 401
    assert old_refresh_response.status_code == 401
    assert old_refresh_response.json()["code"] == "GUEST_SESSION_INVALID"


def test_discard_guest_session_without_bearer_only_clears_recovery_cookie(db_session):
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            guest_id, _, cookie_name, _ = _issue_guest_session(client)
            discard_response = client.delete("/api/v1/auth/guest")
            assert client.cookies.get(cookie_name) is None
    finally:
        app.dependency_overrides.clear()

    assert discard_response.status_code == 204
    db_session.expire_all()
    discarded_session = db_session.get(GuestSession, guest_id)
    assert discarded_session is not None
    assert discarded_session.revoked_at is None


def test_discard_guest_session_without_credentials_is_idempotent(db_session):
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            response = client.delete("/api/v1/auth/guest")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    set_cookie = response.headers.get("set-cookie", "").lower()
    assert "aid_guest_refresh=" in set_cookie
    assert "max-age=0" in set_cookie
    assert "path=/api/v1/auth/guest" in set_cookie


def test_guest_migration_runtime_openapi_does_not_require_bearer_credentials():
    guest_operations = app.openapi()["paths"]["/api/v1/auth/guest"]

    for method in ("get", "delete"):
        operation = guest_operations[method]
        assert "security" not in operation
        authorization = next(
            parameter
            for parameter in operation["parameters"]
            if parameter["name"] == "Authorization"
        )
        assert authorization["in"] == "header"
        assert authorization["required"] is False


@pytest.mark.parametrize(
    "invalid_credential_kind",
    ["expired", "malformed", "user"],
    ids=["expired-guest-bearer", "malformed-bearer", "user-bearer"],
)
def test_discard_guest_session_rejects_invalid_bearer_without_clearing_recovery_cookie(
    invalid_credential_kind,
    db_session,
):
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            guest_id, access_token, cookie_name, refresh_cookie = _issue_guest_session(client)

            if invalid_credential_kind == "expired":
                invalid_token = create_access_token(
                    subject=guest_id,
                    expires_delta=timedelta(seconds=-1),
                    extra_claims={"sub_type": "guest", "sid": guest_id},
                )
            elif invalid_credential_kind == "malformed":
                invalid_token = "not-a-valid-jwt"
            else:
                invalid_token = create_access_token(
                    subject="1",
                    extra_claims={"sub_type": "user"},
                )

            discard_response = client.delete(
                "/api/v1/auth/guest",
                headers={"Authorization": f"Bearer {invalid_token}"},
            )
            retained_refresh_cookie = client.cookies.get(cookie_name)
            old_bearer_response = client.get(
                "/api/v1/quota",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    finally:
        app.dependency_overrides.clear()

    assert discard_response.status_code == 401
    assert discard_response.json()["code"] == "GUEST_SESSION_INVALID"
    assert "aid_guest_refresh=" not in discard_response.headers.get("set-cookie", "").lower()
    assert retained_refresh_cookie == refresh_cookie
    assert old_bearer_response.status_code == 200

    db_session.expire_all()
    guest_session = db_session.get(GuestSession, guest_id)
    assert guest_session is not None
    assert guest_session.revoked_at is None


def test_discard_guest_session_uses_valid_guest_bearer_as_revocation_identity(db_session):
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as victim_client:
            victim_guest_id, victim_access_token, _, _ = _issue_guest_session(victim_client)
            with TestClient(app) as other_guest_client:
                other_guest_id, other_access_token, _, _ = _issue_guest_session(other_guest_client)

            discard_response = victim_client.delete(
                "/api/v1/auth/guest",
                headers={"Authorization": f"Bearer {other_access_token}"},
            )
            victim_bearer_response = victim_client.get(
                "/api/v1/quota",
                headers={"Authorization": f"Bearer {victim_access_token}"},
            )
    finally:
        app.dependency_overrides.clear()

    assert discard_response.status_code == 204
    assert victim_bearer_response.status_code == 200

    db_session.expire_all()
    victim_guest_session = db_session.get(GuestSession, victim_guest_id)
    assert victim_guest_session is not None
    assert victim_guest_session.revoked_at is None
    other_guest_session = db_session.get(GuestSession, other_guest_id)
    assert other_guest_session is not None
    assert other_guest_session.revoked_at is not None


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
                headers={
                    "Authorization": f"Bearer {first_token}",
                    "Idempotency-Key": str(uuid4()),
                },
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


@pytest.mark.parametrize("credential_transport", ["bearer", "cookie"])
def test_logout_revokes_only_current_access_token(credential_transport, db_session, unique_email):
    app.dependency_overrides[get_db] = lambda: db_session
    protected_paths = ("/api/v1/auth/me", "/api/v1/detections/")

    try:
        with TestClient(app) as client:
            token_a, token_b = _issue_user_tokens(client, unique_email, count=2)
            assert _decode_unverified(token_a)["jti"] != _decode_unverified(token_b)["jti"]
            headers_a = {"Authorization": f"Bearer {token_a}"}
            headers_b = {"Authorization": f"Bearer {token_b}"}

            before_logout = {
                path: (
                    client.get(path, headers=headers_a).status_code,
                    client.get(path, headers=headers_b).status_code,
                )
                for path in protected_paths
            }
            if credential_transport == "cookie":
                client.cookies.set("aid_access_token", token_a, domain="testserver.local", path="/")
                logout_response = client.post("/api/v1/auth/logout")
            else:
                logout_response = client.post("/api/v1/auth/logout", headers=headers_a)
            cookie_after_logout = client.cookies.get("aid_access_token")
            db_session.expunge_all()
            repeated_logout_response = client.post("/api/v1/auth/logout", headers=headers_a)
            after_logout = {
                path: (
                    client.get(path, headers=headers_a).status_code,
                    client.get(path, headers=headers_b).status_code,
                )
                for path in protected_paths
            }
    finally:
        app.dependency_overrides.clear()

    assert before_logout == {path: (200, 200) for path in protected_paths}
    assert logout_response.status_code == 204
    set_cookie = logout_response.headers.get("set-cookie", "").lower()
    assert "aid_access_token=" in set_cookie
    assert "max-age=0" in set_cookie
    assert cookie_after_logout is None
    assert repeated_logout_response.status_code == 204
    assert after_logout == {path: (401, 200) for path in protected_paths}


@pytest.mark.parametrize(
    ("authorization", "expected_status", "clears_cookie"),
    [
        (None, 204, True),
        ("Bearer not-a-valid-jwt", 401, False),
        ("Basic Zm9vOmJhcg==", 401, False),
        ("Bearer ", 401, False),
    ],
    ids=["already-absent", "invalid-jwt", "wrong-scheme", "empty-bearer"],
)
def test_logout_handles_absent_and_invalid_credentials(
    authorization,
    expected_status,
    clears_cookie,
    db_session,
):
    app.dependency_overrides[get_db] = lambda: db_session
    headers = {"Authorization": authorization} if authorization is not None else {}

    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/auth/logout", headers=headers)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == expected_status
    assert ("aid_access_token=" in response.headers.get("set-cookie", "")) is clears_cookie


def test_logout_rejects_expired_and_guest_access_tokens(db_session):
    expired_user_token = create_access_token(
        subject="1",
        expires_delta=timedelta(seconds=-1),
        extra_claims={"sub_type": "user"},
    )
    guest_id = "5db45cb0-c886-4d12-88ac-d798461264a7"
    guest_token = create_access_token(
        subject=guest_id,
        extra_claims={"sub_type": "guest", "sid": guest_id},
    )
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        with TestClient(app) as client:
            responses = [
                client.post(
                    "/api/v1/auth/logout",
                    headers={"Authorization": f"Bearer {token}"},
                )
                for token in (expired_user_token, guest_token)
            ]
    finally:
        app.dependency_overrides.clear()

    assert [response.status_code for response in responses] == [401, 401]
    assert all("aid_access_token=" not in response.headers.get("set-cookie", "") for response in responses)
