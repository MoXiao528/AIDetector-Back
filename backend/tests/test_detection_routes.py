from fastapi.testclient import TestClient
import pytest

from app.db.deps import ActorContext, get_current_actor
from app.db.session import get_db
from app.main import app
from app.services.repre_guard_client import repre_guard_client


LONG_TEXT = (
    "This route-level detection sample is intentionally long enough to satisfy the non-whitespace minimum. "
    "It exercises the FastAPI route stack, dependency overrides, response serialization, and compatibility endpoints. "
) * 2


def _install_route_overrides(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_actor] = lambda: ActorContext(actor_type="guest", actor_id="route-guest")


def test_detect_route_returns_detection_payload(db_session, monkeypatch):
    async def fake_detect(text: str) -> dict:
        return {
            "score": 0.003,
            "threshold": 0.0028,
            "label": "AI",
            "model_name": "route-model",
            "score_type": "probability",
        }

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)
    _install_route_overrides(db_session)

    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/detect", json={"text": LONG_TEXT, "functions": ["scan"]})
            assert response.status_code == 200
            payload = response.json()
            assert payload["label"] == "ai"
            assert payload["score"] == pytest.approx(0.003)
            assert payload["result"]["summary"]["ai"] == 100
    finally:
        app.dependency_overrides.clear()


def test_scan_root_compat_route_is_available(db_session, monkeypatch):
    async def fake_detect(text: str) -> dict:
        return {
            "score": 0.002,
            "threshold": 0.0028,
            "label": "HUMAN",
            "model_name": "route-model",
            "score_type": "probability",
        }

    monkeypatch.setattr(repre_guard_client, "detect", fake_detect)
    _install_route_overrides(db_session)

    try:
        with TestClient(app) as client:
            response = client.post("/api/scan", json={"text": LONG_TEXT, "functions": ["scan"]})
            assert response.status_code == 200
            payload = response.json()
            assert payload["summary"] == "AI 0% | Mixed 100% | Human 0%"
            assert payload["sentences"]
    finally:
        app.dependency_overrides.clear()


def test_detect_route_long_text_uses_business_error(db_session):
    _install_route_overrides(db_session)

    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/detect", json={"text": "x" * 20001})
            assert response.status_code == 422
            payload = response.json()
            assert payload["code"] == "TEXT_TOO_LONG"
            assert payload["detail"]["maximum"] == 20000
    finally:
        app.dependency_overrides.clear()


def test_validation_handler_shape_for_missing_text(db_session):
    _install_route_overrides(db_session)

    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/detect", json={})

        assert response.status_code == 422
        payload = response.json()
        assert payload["code"] == 422
        assert payload["message"] == "Validation Error"
        assert isinstance(payload["detail"], list)
    finally:
        app.dependency_overrides.clear()


def test_cors_preflight_allows_local_vite_origin():
    with TestClient(app) as client:
        response = client.options(
            "/api/v1/detect",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
