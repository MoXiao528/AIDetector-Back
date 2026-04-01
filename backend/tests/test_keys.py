import pytest

from app.api.v1.auth import register_user
from app.api.v1.keys import api_key_self_test, create_api_key, list_api_keys
from app.schemas.auth import RegisterRequest
from app.schemas.api_key import APIKeyCreateRequest, APIKeyStatus


@pytest.mark.anyio
async def test_api_key_happy_path(db_session, unique_email):
    user = await register_user(payload=RegisterRequest(email=unique_email, password="StrongPass!23"), db=db_session)

    created = await create_api_key(
        payload=APIKeyCreateRequest(name="CI Key"),
        db=db_session,
        current_user=user,
    )
    assert created.name == "CI Key"
    assert created.status == APIKeyStatus.ACTIVE
    assert created.key

    keys = await list_api_keys(current_user=user, db=db_session)
    assert len(keys) == 1
    assert keys[0].id == created.id

    self_test = await api_key_self_test(current_user=user)
    assert unique_email in self_test.message
