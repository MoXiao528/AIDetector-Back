import pytest

from app.api.v1.auth import register_user
from app.api.v1.detections import detect, list_detections
from app.db.deps import ActorContext
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
        current_user=user,
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
