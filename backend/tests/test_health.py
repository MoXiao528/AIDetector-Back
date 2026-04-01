import pytest
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1.health import read_health, read_readiness
from app.services.repre_guard_client import RepreGuardError, repre_guard_client


@pytest.mark.anyio
async def test_health_liveness():
    response = await read_health()
    assert response.status == "ok"


@pytest.mark.anyio
async def test_readiness_success(db_session, monkeypatch):
    async def fake_health():
        return {"status": "ok"}

    monkeypatch.setattr(repre_guard_client, "health", fake_health)

    response = await read_readiness(db_session)
    assert response.status == "ok"
    assert response.database == "ok"
    assert response.detect_service == "ok"


@pytest.mark.anyio
async def test_readiness_fails_when_detect_service_is_unavailable(db_session, monkeypatch):
    async def fake_health():
        raise RepreGuardError("detect service down")

    monkeypatch.setattr(repre_guard_client, "health", fake_health)

    with pytest.raises(HTTPException) as exc_info:
        await read_readiness(db_session)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "READINESS_CHECK_FAILED"
    assert exc_info.value.detail["detail"]["database"] == "ok"
    assert exc_info.value.detail["detail"]["detect_service"] == "error"


@pytest.mark.anyio
async def test_readiness_fails_when_database_is_unavailable(db_session, monkeypatch):
    def fake_execute(*args, **kwargs):
        raise SQLAlchemyError("db down")

    monkeypatch.setattr(db_session, "execute", fake_execute)

    with pytest.raises(HTTPException) as exc_info:
        await read_readiness(db_session)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "READINESS_CHECK_FAILED"
    assert exc_info.value.detail["detail"]["database"] == "error"
    assert exc_info.value.detail["detail"]["detect_service"] == "skipped"
