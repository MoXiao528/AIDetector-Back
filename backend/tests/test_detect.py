import pytest

from app.api.v1.auth import register_user
from app.api.v1.detections import detect, list_detections
from app.api.v1.keys import create_api_key
from app.db.deps import get_current_user
from app.schemas.api_key import APIKeyCreateRequest
from app.schemas.auth import RegisterRequest
from app.schemas.detection import DetectionRequest


@pytest.mark.anyio
async def test_detect_with_user(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)

    response = await detect(
        payload=DetectionRequest(text="This is a sample human text.", options={"language": "en", "api_key": "secret"}),
        db=db_session,
        current_user=user,
    )
    assert response.detection_id > 0
    assert response.label in {"human", "ai"}
    assert 0 <= response.score <= 1

    listed = await list_detections(db=db_session, current_user=user, page=1, page_size=10, from_time=None, to_time=None)
    assert listed.total == 1
    assert listed.items[0].meta_json["options"]["api_key"] == "***"


@pytest.mark.anyio
async def test_detect_with_api_key(db_session, unique_email):
    user = await register_user(RegisterRequest(email=unique_email, password="StrongPass!23"), db_session)
    api_key_resp = await create_api_key(
        payload=APIKeyCreateRequest(name="Detect Key"),
        db=db_session,
        current_user=user,
    )

    current_user = get_current_user(db=db_session, token=None, api_key_header=api_key_resp.key)
    detection = await detect(
        payload=DetectionRequest(text="Short text"),
        db=db_session,
        current_user=current_user,
    )
    assert detection.detection_id > 0
    assert detection.label in {"human", "ai"}
    assert 0 <= detection.score <= 1
