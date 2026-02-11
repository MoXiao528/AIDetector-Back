import pytest

from app.api.v1.auth import register_user
from fastapi import HTTPException

from app.api.v1.detections import detect, list_detections
from app.api.v1.keys import create_api_key
from app.db.deps import ActorContext, get_current_actor
from app.schemas.api_key import APIKeyCreateRequest
from app.schemas.auth import RegisterRequest
from app.schemas.detection import DetectionRequest
from app.services.repre_guard_client import repre_guard_client

@pytest.fixture(autouse=True)
def mock_repre_guard(monkeypatch):
    """
    在测试中自动 mock 掉 RepreGuard 微服务调用，
    避免真实发 HTTP 请求导致 [Errno -2] Name or service not known。
    """

    async def fake_detect(text: str) -> dict:
        # 这里返回一个“假的 RepreGuard 检测结果”
        # 字段结构要和你真实微服务保持一致
        return {
            "score": 2.8,                       # raw_score
            "threshold": 2.4924452377944597,
            "label": "AI",                      # 或 "HUMAN"
            "model_name": "Qwen/Qwen2.5-7B",
        }

    # 把单例实例上的 detect 方法替换掉
    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)
    yield

@pytest.mark.anyio
async def test_detect_with_user(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    actor = ActorContext(actor_type="user", actor_id=str(user.id), user=user)

    response = await detect(
        payload=DetectionRequest(text="This is a sample human text.", options={"language": "en", "api_key": "secret"}),
        db=db_session,
        current_actor=actor,
    )
    assert response.detection_id > 0
    assert response.label in {"human", "ai"}
    assert 0 <= response.score <= 1

    listed = await list_detections(
        db=db_session,
        current_actor=actor,
        page=1,
        page_size=10,
        from_time=None,
        to_time=None,
    )
    assert listed.total == 1
    assert listed.items[0].meta_json["options"]["api_key"] == "***"


@pytest.mark.anyio
async def test_detect_with_guest(db_session):
    actor = ActorContext(actor_type="guest", actor_id="guest-test")
    detection = await detect(payload=DetectionRequest(text="Short text"), db=db_session, current_actor=actor)
    assert detection.detection_id > 0
    assert detection.label in {"human", "ai"}
    assert 0 <= detection.score <= 1


@pytest.mark.anyio
async def test_detect_with_api_key_actor(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    api_key_resp = await create_api_key(
        payload=APIKeyCreateRequest(name="Detect Key"),
        db=db_session,
        current_user=user,
    )

    actor = get_current_actor(db=db_session, token=None, api_key_header=api_key_resp.key)
    detection = await detect(payload=DetectionRequest(text="Short text"), db=db_session, current_actor=actor)
    assert detection.detection_id > 0
    assert detection.label in {"human", "ai"}


@pytest.mark.anyio
async def test_invalid_bearer_does_not_fallback_to_api_key(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    api_key_resp = await create_api_key(
        payload=APIKeyCreateRequest(name="Detect Key"),
        db=db_session,
        current_user=user,
    )

    with pytest.raises(HTTPException) as exc_info:
        get_current_actor(db=db_session, token="invalid-token", api_key_header=api_key_resp.key)
    assert exc_info.value.status_code == 401


@pytest.mark.anyio
async def test_detect_saves_history(db_session, unique_email):
    """
    Validation Rule:
    1. /detect saves a complete record (check meta_json["analysis"]).
    2. Response includes history_id.
    """
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    actor = ActorContext(actor_type="user", actor_id=str(user.id), user=user)

    # Call /detect
    response = await detect(
        payload=DetectionRequest(text="This is a test sentence for history saving.", functions=["scan", "polish"]),
        db=db_session,
        current_actor=actor,
    )

    # Check response (Standard 1 & 2)
    assert response.history_id is not None
    assert response.history_id > 0
    assert response.detection_id == response.history_id

    # Check DB structure
    from app.services.detection_service import DetectionService
    service = DetectionService(db_session)
    record = list(service.list_detections(
        actor_type="user", 
        actor_id=str(user.id), 
        page=1, 
        page_size=1, 
        from_time=None, 
        to_time=None
    )[0])[0]

    # Check flat analysis structure (Standard 2)
    # Expected: record.meta_json = { "options": ..., "analysis": { "summary": ..., "sentences": ... }, ... }
    assert "analysis" in record.meta_json
    analysis = record.meta_json["analysis"]
    assert "summary" in analysis
    assert "sentences" in analysis
    assert len(analysis["sentences"]) > 0
    assert analysis["sentences"][0]["text"] == "This is a test sentence for history saving"
    
    # Check NO double nesting
    assert "analysis" not in analysis

