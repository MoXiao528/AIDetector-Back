from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.db.deps import ActorContext, get_current_actor
from app.db.session import get_db
from app.main import app
from app.models.guest_session import GuestSession
from app.services.repre_guard_client import repre_guard_client


LONG_TEXT = (
    "This route-level detection sample is intentionally long enough to satisfy the non-whitespace minimum. "
    "It exercises the FastAPI route stack, dependency overrides, response serialization, and compatibility endpoints. "
) * 2


def _idempotency_headers() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid4())}


def _install_route_overrides(db_session):
    db_session.add(
        GuestSession(
            id="route-guest",
            refresh_token_hash="b" * 64,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db_session.flush()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_actor] = lambda: ActorContext(actor_type="guest", actor_id="route-guest")


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({"repre_guard": "invalid"}, id="string"),
        pytest.param({"repre_guard": []}, id="array"),
        pytest.param({"repre_guard": None}, id="null"),
        pytest.param({"repre_guard": {"nested": {"unexpected": True}}}, id="object"),
        pytest.param({"REPRE_GUARD": {}}, id="case-alias"),
        pytest.param({" repre_guard ": {}}, id="whitespace-alias"),
    ],
)
def test_detect_route_rejects_reserved_repre_guard_before_inference(db_session, monkeypatch, options):
    detector_calls = []

    async def fake_detect(text: str) -> dict:
        detector_calls.append(text)
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
            response = client.post(
                "/api/v1/detect",
                json={"text": LONG_TEXT, "options": options},
                headers=_idempotency_headers(),
            )

        assert response.status_code == 422
        assert response.json()["message"] == "Validation Error"
        assert detector_calls == []
    finally:
        app.dependency_overrides.clear()


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
            response = client.post(
                "/api/v1/detect",
                json={"text": LONG_TEXT, "functions": ["scan"], "options": {"language": "en"}},
                headers=_idempotency_headers(),
            )
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
            response = client.post(
                "/api/scan",
                json={"text": LONG_TEXT, "functions": ["scan"]},
                headers=_idempotency_headers(),
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["summary"] == "AI 0% | Human 100%"
            assert payload["sentences"]
            assert payload["sentences"][0]["isAi"] is False
    finally:
        app.dependency_overrides.clear()


def test_detect_route_long_text_uses_business_error(db_session):
    _install_route_overrides(db_session)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/detect",
                json={"text": "x" * 20001},
                headers=_idempotency_headers(),
            )
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
            response = client.post("/api/v1/detect", json={}, headers=_idempotency_headers())

        assert response.status_code == 422
        payload = response.json()
        assert payload["code"] == 422
        assert payload["message"] == "Validation Error"
        assert isinstance(payload["detail"], list)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5300"])
def test_cors_preflight_allows_local_vite_origin(origin):
    with TestClient(app) as client:
        response = client.options(
            "/api/v1/detect",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Idempotency-Key",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "idempotency-key" in response.headers["access-control-allow-headers"].lower()


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/detect",
        "/api/v1/scan/detect",
        "/api/v1/scan",
        "/api/scan/detect",
        "/api/scan",
    ],
)
@pytest.mark.parametrize("header_value", [None, "not-a-uuid"])
def test_all_detection_routes_require_uuid_idempotency_key(db_session, path, header_value):
    _install_route_overrides(db_session)
    headers = {} if header_value is None else {"Idempotency-Key": header_value}

    try:
        with TestClient(app) as client:
            response = client.post(path, json={"text": LONG_TEXT, "functions": ["scan"]}, headers=headers)

        assert response.status_code == 422
        assert response.json()["message"] == "Validation Error"
        assert any(item["loc"][-1] == "Idempotency-Key" for item in response.json()["detail"])
    finally:
        app.dependency_overrides.clear()


def test_app_shutdown_closes_shared_repre_guard_client(monkeypatch):
    closed = False

    async def fake_aclose() -> None:
        nonlocal closed
        closed = True

    monkeypatch.setattr(repre_guard_client, "aclose", fake_aclose)

    with TestClient(app):
        pass

    assert closed is True
