from datetime import datetime, timedelta, timezone
from threading import get_ident
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import detections
from app.core.security import create_access_token
from app.db.deps import ActorContext, authorize_preflight, get_current_actor
from app.db.session import get_db
from app.main import app
from app.models.detection import Detection
from app.models.detection_request import DetectionRequest
from app.models.guest_session import GuestSession
from app.models.quota_usage import QuotaUsage
from app.services import token_chunker
from app.services.repre_guard_client import repre_guard_client


class RecordingTokenizer:
    def __init__(self):
        self.calls = []

    def encode(self, text, *, add_special_tokens=True):
        self.calls.append((text, add_special_tokens, get_ident()))
        body = list(range(len(text.split())))
        return [-1, *body, -2] if add_special_tokens else body

    def num_special_tokens_to_add(self, pair=False):
        return 2


@pytest.fixture
def tokenizer(monkeypatch):
    tokenizer = RecordingTokenizer()
    monkeypatch.setattr(token_chunker, "get_tokenizer", lambda model_name=None: tokenizer)
    return tokenizer


@pytest.fixture
def client(db_session, tokenizer):
    db_session.add(
        GuestSession(
            id="preflight-guest",
            refresh_token_hash="c" * 64,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db_session.flush()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_actor] = lambda: ActorContext(
        actor_type="guest", actor_id="preflight-guest"
    )
    app.dependency_overrides[authorize_preflight] = lambda: None
    try:
        with TestClient(app) as client:
            assert tokenizer.calls[0][:2] == ("warmup", True)
            tokenizer.calls.clear()
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("content_tokens,ready", [(253, False), (254, True), (255, True)])
def test_preflight_exact_boundary_counts_special_tokens_once(client, tokenizer, content_tokens, ready):
    text = " ".join(["x"] * content_tokens)
    response = client.post("/api/v1/detect/preflight", json={"text": text})

    assert response.status_code == 200
    assert response.json() == {
        "characterCount": content_tokens,
        "tokenCount": content_tokens + 2,
        "minimumTokens": 256,
        "ready": ready,
        "estimatedAdditionalCharacters": 0 if ready else 1,
    }
    assert [(text, special) for text, special, _ in tokenizer.calls] == [(text, True)]


@pytest.mark.parametrize("text", ["", " \r\n\t\u3000"])
def test_preflight_empty_input_skips_tokenizer(client, tokenizer, text):
    response = client.post("/api/v1/detect/preflight", json={"text": text})
    assert response.status_code == 200
    assert response.json() == {
        "characterCount": 0,
        "tokenCount": 0,
        "minimumTokens": 256,
        "ready": False,
        "estimatedAdditionalCharacters": None,
    }
    assert tokenizer.calls == []


def test_preflight_estimate_excludes_special_tokens_and_uses_unicode_characters(client, tokenizer):
    response = client.post("/api/v1/detect/preflight", json={"text": "\r\n  甲🧪 \r\n \t \r乙  \t"})
    assert response.json() == {
        "characterCount": 3,
        "tokenCount": 4,
        "minimumTokens": 256,
        "ready": False,
        "estimatedAdditionalCharacters": 378,
    }
    assert tokenizer.calls[0][0] == "甲🧪 \n乙"


@pytest.mark.parametrize(
    "endpoint", ["/api/v1/detect", "/api/v1/scan", "/api/v1/scan/detect", "/api/scan", "/api/scan/detect"]
)
def test_submission_reuses_preflight_normalization_and_gate(client, tokenizer, monkeypatch, endpoint):
    def unexpected(*args, **kwargs):
        pytest.fail("Short input must not reserve quota or invoke the detector")

    monkeypatch.setattr(detections, "reserve_detection_request", unexpected)
    monkeypatch.setattr(repre_guard_client, "detect", unexpected)
    text = "\r\n  " + " ".join(["word"] * 253) + "  \r\n\t\r\n"
    preview = client.post("/api/v1/detect/preflight", json={"text": text}).json()
    response = client.post(endpoint, json={"text": text}, headers={"Idempotency-Key": str(uuid4())})

    assert response.status_code == 422
    assert response.json()["code"] == "TEXT_TOO_SHORT"
    assert response.json()["detail"] == {**preview, "unit": "tokens"}
    assert len(tokenizer.calls) == 2
    assert tokenizer.calls[0][:2] == tokenizer.calls[1][:2]


def test_preflight_has_no_quota_history_or_detector_side_effects(client, db_session, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Preflight must not reserve quota or invoke the detector")

    monkeypatch.setattr(detections, "reserve_detection_request", unexpected)
    monkeypatch.setattr(repre_guard_client, "detect", unexpected)
    models = (Detection, DetectionRequest, QuotaUsage)
    counts = [db_session.query(model).count() for model in models]

    for text in ("short", " ".join(["word"] * 254)):
        assert client.post("/api/v1/detect/preflight", json={"text": text}).status_code == 200

    assert [db_session.query(model).count() for model in models] == counts


def test_preflight_raw_maximum_checks_before_tokenizing(client, tokenizer):
    accepted = client.post("/api/v1/detect/preflight", json={"text": "🧪" * 20000})
    assert accepted.status_code == 200
    assert accepted.json()["characterCount"] == 20000
    tokenizer.calls.clear()

    rejected = client.post("/api/v1/detect/preflight", json={"text": "🧪" * 20000 + " "})
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "TEXT_TOO_LONG"
    assert rejected.json()["detail"] == {"maximum": 20000, "current": 20001}
    assert tokenizer.calls == []


def test_preflight_requires_authentication_and_accepts_existing_guest_token(client, tokenizer):
    del app.dependency_overrides[authorize_preflight]
    assert client.post("/api/v1/detect/preflight", json={"text": "sample"}).status_code == 401
    assert tokenizer.calls == []
    token = create_access_token("preflight-guest", extra_claims={"sub_type": "guest", "sid": "preflight-guest"})
    response = client.post(
        "/api/v1/detect/preflight", json={"text": "sample"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200


def test_preflight_requires_detect_scope(client, monkeypatch):
    del app.dependency_overrides[authorize_preflight]
    monkeypatch.setattr("app.db.deps.get_current_actor", lambda *args: ActorContext(
        actor_type="user", actor_id="123", auth_method="api_key", scopes=frozenset({"quota:read"})
    ))
    assert client.post("/api/v1/detect/preflight", json={"text": "sample"}).status_code == 403


def test_preflight_tokenizer_runs_outside_event_loop(client, tokenizer):
    loop_threads = []

    async def actor():
        loop_threads.append(get_ident())
        return ActorContext(actor_type="guest", actor_id="preflight-guest")

    app.dependency_overrides[authorize_preflight] = actor
    assert client.post("/api/v1/detect/preflight", json={"text": "sample"}).status_code == 200
    assert tokenizer.calls[0][2] != loop_threads[0]


@pytest.mark.parametrize("dense_unicode", [False, True])
def test_ready_input_reaches_detector_including_below_old_character_minimum(client, tokenizer, monkeypatch, dense_unicode):
    sent = []
    if dense_unicode:
        def encode(text, *, add_special_tokens=True):
            body = list(range(len(text) * 13))
            return [-1, *body, -2] if add_special_tokens else body

        monkeypatch.setattr(tokenizer, "encode", encode)
        text = "🧪" * 20
    else:
        text = " ".join(["word"] * 254)

    async def detect(text):
        sent.append(text)
        return {"score": 0.1, "threshold": 0.5, "label": "HUMAN", "model_name": "test", "score_type": "probability"}

    monkeypatch.setattr(repre_guard_client, "detect", detect)
    preview = client.post("/api/v1/detect/preflight", json={"text": text})
    assert preview.json()["ready"] is True
    response = client.post("/api/v1/detect", json={"text": text}, headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 200
    assert sent == [text]


def test_tokenizer_startup_failure_is_explicit(monkeypatch, caplog):
    def unavailable(model_name=None):
        raise RuntimeError("tokenizer unavailable")

    monkeypatch.setattr(token_chunker, "get_tokenizer", unavailable)
    with pytest.raises(RuntimeError, match="tokenizer unavailable"):
        with TestClient(app):
            pass
    assert "Detection tokenizer initialization failed" in caplog.text
