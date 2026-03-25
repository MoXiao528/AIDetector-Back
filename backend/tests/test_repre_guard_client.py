import httpx
import pytest

import app.services.repre_guard_client as client_module
from app.services.repre_guard_client import HEALTH_PROBE_TEXT, RepreGuardClient


def _detect_payload():
    return {
        "score": -1.0313416951862366,
        "threshold": 2.4924452377944597,
        "label": "HUMAN",
        "model_name": "Qwen/Qwen2.5-0.5B",
    }


def _build_async_client(monkeypatch, calls, responder):
    class DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            self.timeout = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            calls.append(("GET", url, None))
            return responder("GET", url, None)

        async def post(self, url, json):
            calls.append(("POST", url, json))
            return responder("POST", url, json)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", DummyAsyncClient)


@pytest.mark.anyio
async def test_detect_uses_explicit_detect_url(monkeypatch):
    calls = []

    def responder(method, url, payload):
        return httpx.Response(200, json=_detect_payload(), request=httpx.Request(method, url, json=payload))

    _build_async_client(monkeypatch, calls, responder)

    client = RepreGuardClient(
        base_url="https://detect.internal.example.com",
        detect_url="https://umcat.cis.um.edu.mo/api/aidetect.php",
    )
    data = await client.detect("test payload")

    assert data["model_name"] == "Qwen/Qwen2.5-0.5B"
    assert calls == [
        ("POST", "https://umcat.cis.um.edu.mo/api/aidetect.php", {"text": "test payload"}),
    ]


@pytest.mark.anyio
async def test_detect_uses_legacy_base_url_suffix(monkeypatch):
    calls = []

    def responder(method, url, payload):
        return httpx.Response(200, json=_detect_payload(), request=httpx.Request(method, url, json=payload))

    _build_async_client(monkeypatch, calls, responder)

    client = RepreGuardClient(base_url="https://detect.internal.example.com")
    await client.detect("legacy payload")

    assert calls == [
        ("POST", "https://detect.internal.example.com/detect", {"text": "legacy payload"}),
    ]


@pytest.mark.anyio
async def test_detect_accepts_direct_detect_url_from_legacy_setting(monkeypatch):
    calls = []

    def responder(method, url, payload):
        return httpx.Response(200, json=_detect_payload(), request=httpx.Request(method, url, json=payload))

    _build_async_client(monkeypatch, calls, responder)

    client = RepreGuardClient(base_url="https://umcat.cis.um.edu.mo/api/aidetect.php")
    await client.detect("compat payload")

    assert calls == [
        ("POST", "https://umcat.cis.um.edu.mo/api/aidetect.php", {"text": "compat payload"}),
    ]


@pytest.mark.anyio
async def test_health_uses_detect_probe_when_only_detect_endpoint_is_available(monkeypatch):
    calls = []

    def responder(method, url, payload):
        return httpx.Response(200, json=_detect_payload(), request=httpx.Request(method, url, json=payload))

    _build_async_client(monkeypatch, calls, responder)

    client = RepreGuardClient(detect_url="https://umcat.cis.um.edu.mo/api/aidetect.php")
    data = await client.health()

    assert data == {"status": "ok", "mode": "detect_probe"}
    assert calls == [
        ("POST", "https://umcat.cis.um.edu.mo/api/aidetect.php", {"text": HEALTH_PROBE_TEXT}),
    ]


@pytest.mark.anyio
async def test_health_uses_explicit_health_url(monkeypatch):
    calls = []

    def responder(method, url, payload):
        return httpx.Response(200, json={"status": "ok"}, request=httpx.Request(method, url))

    _build_async_client(monkeypatch, calls, responder)

    client = RepreGuardClient(
        detect_url="https://umcat.cis.um.edu.mo/api/aidetect.php",
        health_url="https://umcat.cis.um.edu.mo/api/health.php",
    )
    data = await client.health()

    assert data == {"status": "ok"}
    assert calls == [
        ("GET", "https://umcat.cis.um.edu.mo/api/health.php", None),
    ]
