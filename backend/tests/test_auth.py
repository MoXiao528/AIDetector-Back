import pytest

from app.api.v1.auth import login, read_current_user, register_user
from app.schemas.auth import LoginRequest, RegisterRequest


@pytest.mark.anyio
async def test_register_login_and_me(db_session, unique_email):
    created_user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    assert created_user.email == unique_email
    assert created_user.name == unique_email

    token_resp = await login(LoginRequest(identifier=unique_email, password="StrongPass!23"), db_session)
    assert token_resp.token_type == "bearer"
    assert token_resp.access_token

    me = await read_current_user(current_user=created_user)
    assert me.email == unique_email
    assert getattr(me.role, "value", me.role) == "INDIVIDUAL"


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
