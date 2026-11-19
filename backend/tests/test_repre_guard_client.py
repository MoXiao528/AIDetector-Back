import asyncio
import json

import httpx
import pytest

import app.services.repre_guard_client as client_module
from app.services.repre_guard_client import (
    HEALTH_PROBE_TEXT,
    MAX_DETECT_REQUEST_BYTES,
    RepreGuardClient,
    RepreGuardError,
)

SERVICE_TOKEN = "test-repre-guard-service-token-1234567890"
SERVICE_HEADERS = {"X-RepreGuard-Token": SERVICE_TOKEN}


def _detect_payload():
    return {
        "score": -1.642964,
        "threshold": 0.0,
        "label": "HUMAN",
        "model_name": "openai-community/roberta-base-openai-detector",
        "score_type": "raw_logit",
    }


def _build_async_client(monkeypatch, calls, responder):
    async_client = httpx.AsyncClient

    def handler(request):
        payload = json.loads(request.content) if request.content else None
        headers = {"X-RepreGuard-Token": request.headers["X-RepreGuard-Token"]}
        calls.append((request.method, str(request.url), payload, headers))
        return responder(request.method, str(request.url), payload)

    monkeypatch.setattr(
        client_module.httpx,
        "AsyncClient",
        lambda **kwargs: async_client(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.anyio
async def test_detect_uses_explicit_detect_url(monkeypatch):
    calls = []

    def responder(method, url, payload):
        return httpx.Response(
            200,
            json=_detect_payload(),
            headers={"content-type": "text/event-stream;charset=UTF-8"},
            request=httpx.Request(method, url, json=payload),
        )

    _build_async_client(monkeypatch, calls, responder)

    client = RepreGuardClient(
        base_url="https://detect.internal.example.com",
        detect_url="https://umcat.cis.um.edu.mo/api/aidetect.php",
        service_token=SERVICE_TOKEN,
    )
    data = await client.detect("test payload")

    assert data["model_name"] == "openai-community/roberta-base-openai-detector"
    assert calls == [
        ("POST", "https://umcat.cis.um.edu.mo/api/aidetect.php", {"text": "test payload"}, SERVICE_HEADERS),
    ]


@pytest.mark.anyio
async def test_detect_uses_legacy_base_url_suffix(monkeypatch):
    calls = []

    def responder(method, url, payload):
        return httpx.Response(200, json=_detect_payload(), request=httpx.Request(method, url, json=payload))

    _build_async_client(monkeypatch, calls, responder)

    client = RepreGuardClient(base_url="https://detect.internal.example.com", service_token=SERVICE_TOKEN)
    await client.detect("legacy payload")

    assert calls == [
        ("POST", "https://detect.internal.example.com/detect", {"text": "legacy payload"}, SERVICE_HEADERS),
    ]


@pytest.mark.anyio
async def test_detect_accepts_direct_detect_url_from_legacy_setting(monkeypatch):
    calls = []

    def responder(method, url, payload):
        return httpx.Response(200, json=_detect_payload(), request=httpx.Request(method, url, json=payload))

    _build_async_client(monkeypatch, calls, responder)

    client = RepreGuardClient(
        base_url="https://umcat.cis.um.edu.mo/api/aidetect.php",
        service_token=SERVICE_TOKEN,
    )
    await client.detect("compat payload")

    assert calls == [
        ("POST", "https://umcat.cis.um.edu.mo/api/aidetect.php", {"text": "compat payload"}, SERVICE_HEADERS),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "options,expected_url",
    [
        ({"base_url": "http://host.docker.internal:9000"}, "http://host.docker.internal:9000/detect"),
        ({"base_url": "https://umcat.cis.um.edu.mo/api/aidetect.php"}, "https://umcat.cis.um.edu.mo/api/aidetect.php"),
        (
            {
                "base_url": "https://unused.example",
                "detect_url": "https://umcat.cis.um.edu.mo/api/aidetect.php",
            },
            "https://umcat.cis.um.edu.mo/api/aidetect.php",
        ),
    ],
)
async def test_evidence_uses_effective_detect_url_and_preserves_original_text(
    monkeypatch, options, expected_url
):
    calls = []
    body = b'{"schemaVersion":1,"status":"failed"}'

    def responder(method, url, payload):
        return httpx.Response(200, content=body, request=httpx.Request(method, url, json=payload))

    _build_async_client(monkeypatch, calls, responder)
    client = RepreGuardClient(**options, service_token=SERVICE_TOKEN)
    text = "  original\r\nfull text 😀  "
    assert await client.route_evidence(text) == body
    assert calls == [
        ("POST", expected_url, {"text": text, "action": "evidence"}, SERVICE_HEADERS),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("url_setting", ["base_url", "detect_url"])
@pytest.mark.parametrize(
    "endpoint",
    [
        "https://gateway.example/api/AIDETECT.PHP",
        "https://gateway.example/Proxy/v1/AiDetect.PhP/",
        "https://gateway.example/Proxy/v1/DeTeCt/",
    ],
)
async def test_case_insensitive_endpoint_suffix_preserves_request_url(
    monkeypatch, url_setting, endpoint
):
    calls = []
    evidence_body = b'{"schemaVersion":1,"status":"failed","reason":"model_unavailable"}'

    def responder(method, url, payload):
        request = httpx.Request(method, url, json=payload)
        if payload and payload.get("action") == "evidence":
            return httpx.Response(200, content=evidence_body, request=request)
        return httpx.Response(200, json=_detect_payload(), request=request)

    _build_async_client(monkeypatch, calls, responder)
    client = RepreGuardClient(**{url_setting: endpoint}, service_token=SERVICE_TOKEN)
    text = "  original\r\nfull text 😀  "
    expected_url = endpoint.rstrip("/")
    assert await client.detect(text) == _detect_payload()
    assert calls[0][1] == expected_url
    assert await client.health() == {"status": "ok", "mode": "detect_probe"}
    assert await client.route_evidence(text) == evidence_body
    assert calls == [
        ("POST", expected_url, {"text": text}, SERVICE_HEADERS),
        ("POST", expected_url, {"text": HEALTH_PROBE_TEXT}, SERVICE_HEADERS),
        ("POST", expected_url, {"text": text, "action": "evidence"}, SERVICE_HEADERS),
    ]


@pytest.mark.anyio
async def test_health_uses_detect_probe_when_only_detect_endpoint_is_available(monkeypatch):
    calls = []

    def responder(method, url, payload):
        return httpx.Response(200, json=_detect_payload(), request=httpx.Request(method, url, json=payload))

    _build_async_client(monkeypatch, calls, responder)

    client = RepreGuardClient(
        detect_url="https://umcat.cis.um.edu.mo/api/aidetect.php",
        service_token=SERVICE_TOKEN,
    )
    data = await client.health()

    assert data == {"status": "ok", "mode": "detect_probe"}
    assert calls == [
        ("POST", "https://umcat.cis.um.edu.mo/api/aidetect.php", {"text": HEALTH_PROBE_TEXT}, SERVICE_HEADERS),
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
        service_token=SERVICE_TOKEN,
    )
    data = await client.health()

    assert data == {"status": "ok"}
    assert calls == [
        ("GET", "https://umcat.cis.um.edu.mo/api/health.php", None, SERVICE_HEADERS),
    ]


@pytest.mark.anyio
async def test_detect_rejects_missing_score_type(monkeypatch):
    calls = []

    def responder(method, url, payload):
        data = _detect_payload()
        data.pop("score_type")
        return httpx.Response(200, json=data, request=httpx.Request(method, url, json=payload))

    _build_async_client(monkeypatch, calls, responder)

    client = RepreGuardClient(base_url="https://detect.internal.example.com", service_token=SERVICE_TOKEN)
    with pytest.raises(RepreGuardError) as exc_info:
        await client.detect("bad payload")

    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "INVALID_DETECT_RESPONSE"


@pytest.mark.anyio
async def test_detect_rejects_bad_numeric_payload(monkeypatch):
    calls = []

    def responder(method, url, payload):
        data = _detect_payload()
        data["score"] = "abc"
        return httpx.Response(200, json=data, request=httpx.Request(method, url, json=payload))

    _build_async_client(monkeypatch, calls, responder)

    client = RepreGuardClient(base_url="https://detect.internal.example.com", service_token=SERVICE_TOKEN)
    with pytest.raises(RepreGuardError) as exc_info:
        await client.detect("bad payload")

    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "INVALID_DETECT_RESPONSE"


@pytest.mark.anyio
async def test_detect_rejects_probability_out_of_range(monkeypatch):
    calls = []

    def responder(method, url, payload):
        data = _detect_payload()
        data["score"] = 1.2
        data["score_type"] = "probability"
        return httpx.Response(200, json=data, request=httpx.Request(method, url, json=payload))

    _build_async_client(monkeypatch, calls, responder)

    client = RepreGuardClient(base_url="https://detect.internal.example.com", service_token=SERVICE_TOKEN)
    with pytest.raises(RepreGuardError) as exc_info:
        await client.detect("bad payload")

    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "INVALID_DETECT_RESPONSE"


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_detect_maps_auth_failure_to_stable_generic_error(monkeypatch, status_code):
    calls = []

    def responder(method, url, payload):
        return httpx.Response(
            status_code,
            json={"detail": {"code": "INTERNAL_SECRET", "message": "token mismatch: secret-value"}},
            request=httpx.Request(method, url, json=payload),
        )

    _build_async_client(monkeypatch, calls, responder)

    client = RepreGuardClient(base_url="https://detect.internal.example.com", service_token=SERVICE_TOKEN)
    with pytest.raises(RepreGuardError) as exc_info:
        await client.detect("payload")

    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "DETECT_SERVICE_AUTH_FAILED"
    assert exc_info.value.message == "detect service authentication failed"
    assert exc_info.value.detail is None


@pytest.mark.anyio
async def test_detect_rejects_streamed_response_over_128_kib(monkeypatch):
    calls = []

    def responder(method, url, payload):
        return httpx.Response(
            200,
            content=b"x" * (131072 + 1),
            headers={"content-length": "1", "content-type": "application/json"},
            request=httpx.Request(method, url, json=payload),
        )

    _build_async_client(monkeypatch, calls, responder)

    client = RepreGuardClient(base_url="https://detect.internal.example.com", service_token=SERVICE_TOKEN)
    with pytest.raises(RepreGuardError) as exc_info:
        await client.detect("payload")

    assert exc_info.value.status_code == 502
    assert exc_info.value.code == "DETECT_SERVICE_RESPONSE_TOO_LARGE"


@pytest.mark.anyio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("action", ["detect", "evidence"])
@pytest.mark.parametrize(
    "text",
    [
        "中",
        '  中文\r\n\t"引号"\\路径  ',
        '{"action":"evidence","text":"普通 JSON 正文"}',
        "AIDT:1:evidence\n正文",
        "中文" * 10_000,
        "😀" * 20_000,
        "\x00" * 20_000,
    ],
    ids=["one-char", "unicode-whitespace", "plain-json", "prefix-body", "max-cjk", "max-emoji", "max-controls"],
)
async def test_payload_modes_preserve_original_text(enabled, action, text):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=_detect_payload())

    client = RepreGuardClient(
        detect_url="https://gateway.example/aidetect.php",
        service_token=SERVICE_TOKEN,
        text_envelope_enabled=enabled,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client._client = transport
        await (client.detect(text) if action == "detect" else client.route_evidence(text))
    expected = {"text": f"AIDT:1:{action}\n{text}"} if enabled else {"text": text}
    if not enabled and action == "evidence":
        expected["action"] = "evidence"
    assert len(calls) == 1
    assert json.loads(calls[0].content) == expected
    assert len(calls[0].content) <= MAX_DETECT_REQUEST_BYTES
    assert calls[0].headers["X-RepreGuard-Token"] == SERVICE_TOKEN


@pytest.mark.anyio
async def test_envelope_covers_readiness_probe():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_detect_payload())

    client = RepreGuardClient(
        detect_url="https://gateway.example/aidetect.php",
        service_token=SERVICE_TOKEN,
        text_envelope_enabled=True,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client._client = transport
        assert await client.health() == {"status": "ok", "mode": "detect_probe"}
    assert calls == [{"text": f"AIDT:1:detect\n{HEALTH_PROBE_TEXT}"}]


@pytest.mark.anyio
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("action", ["detect", "evidence"])
@pytest.mark.parametrize(
    "text",
    [None, 12, True, [], {}, "", " \r\n\t", "中" * 20_001, "private\ud800"],
    ids=["null", "number", "bool", "list", "object", "empty", "blank", "over-limit", "surrogate"],
)
async def test_invalid_text_never_reaches_transport(enabled, action, text):
    def forbidden(request):
        pytest.fail("Invalid text must be rejected before sending")

    client = RepreGuardClient(
        detect_url="https://gateway.example/aidetect.php",
        service_token=SERVICE_TOKEN,
        text_envelope_enabled=enabled,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as transport:
        client._client = transport
        with pytest.raises(RepreGuardError) as caught:
            await (client.detect(text) if action == "detect" else client.route_evidence(text))
    assert caught.value.code == "INVALID_DETECT_REQUEST"
    assert caught.value.detail is None
    assert "private" not in str(caught.value)
    assert SERVICE_TOKEN not in str(caught.value)


@pytest.mark.anyio
@pytest.mark.parametrize("extra_bytes", [0, 1])
async def test_actual_serialized_request_limit_and_same_request(monkeypatch, extra_bytes):
    sent, built = [], []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json=_detect_payload())

    client = RepreGuardClient(service_token=SERVICE_TOKEN)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=17) as transport:
        client._client = transport
        build_request = transport.build_request

        def observe_build(*args, **kwargs):
            request = build_request(*args, **kwargs)
            built.append(request)
            return request

        monkeypatch.setattr(transport, "build_request", observe_build)
        payload = {"text": "a" * (MAX_DETECT_REQUEST_BYTES - 11 + extra_bytes)}
        if extra_bytes:
            with pytest.raises(RepreGuardError) as caught:
                await client._request("POST", "https://gateway.example/detect", json_payload=payload)
            assert caught.value.code == "DETECT_SERVICE_REQUEST_TOO_LARGE"
            assert not sent
        else:
            await client._request("POST", "https://gateway.example/detect", json_payload=payload)
            assert sent == built and sent[0] is built[0]
            assert sent[0].extensions["timeout"]["read"] == 17
        assert len(built[0].content) == MAX_DETECT_REQUEST_BYTES + extra_bytes


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["oversize", "cancelled"])
async def test_stream_is_closed_on_failure(failure):
    class ResponseStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            if failure == "cancelled":
                raise asyncio.CancelledError
            yield b"x" * (128 * 1024 + 1)

        async def aclose(self):
            self.closed = True

    stream = ResponseStream()
    client = RepreGuardClient(service_token=SERVICE_TOKEN)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    ) as transport:
        client._client = transport
        with pytest.raises(asyncio.CancelledError if failure == "cancelled" else RepreGuardError):
            await client.detect("text")
    assert stream.closed


@pytest.mark.anyio
async def test_requests_never_follow_redirects():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(307, headers={"location": "https://other.example/detect"})

    client = RepreGuardClient(service_token=SERVICE_TOKEN)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as transport:
        client._client = transport
        with pytest.raises(RepreGuardError):
            await client.detect("text")
    assert len(calls) == 1
