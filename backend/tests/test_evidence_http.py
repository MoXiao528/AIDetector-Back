"""EV4-02 HTTP, deadline, admission and settlement regression checks."""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import time
from threading import Event, Thread
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import func, select

from app.api.v1 import detections as detection_api
from app.core.config import get_settings
from app.db.deps import get_current_actor, get_current_user
from app.db.session import get_db
from app.main import app
from app.models.detection import Detection
from app.models.detection_request import DetectionRequest as RequestRecord
from app.models.quota_usage import QuotaUsage
from app.schemas.detection import DetectionRequest
from app.schemas.evidence import EvidenceResult
from app.services.evidence_engine import EvidenceEngine
from app.services.repre_guard_client import RepreGuardClient, repre_guard_client

from test_detection_idempotency import LONG_TEXT, _fake_result, _user_actor
import test_detection_idempotency as idempotency_tests
import test_evidence_engine as engine_tests
from test_evidence_engine import response, write_bundle
from test_repre_guard_client import SERVICE_TOKEN

artifacts = engine_tests.artifacts
ready_engine = engine_tests.ready_engine
committed_db_session = idempotency_tests.committed_db_session


@pytest.fixture(autouse=True)
def optional_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "detect_evidence_mode", "off")
    monkeypatch.setattr(settings, "detect_evidence_bundle_path", "")
    monkeypatch.setattr(settings, "detect_evidence_bundle_sha256", "")
    monkeypatch.setattr(settings, "detect_evidence_timeout_seconds", "12")


@asynccontextmanager
async def mock_client(handler, endpoint="https://router.example/prefix/detect"):
    client = RepreGuardClient(detect_url=endpoint, service_token=SERVICE_TOKEN)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client._client = transport
        yield client


@pytest.mark.parametrize("timeout", ["invalid", "NaN", "inf", "0", "-1", "60.01", ""])
def test_invalid_timeout_disables_only_evidence(monkeypatch, timeout):
    settings = get_settings()
    monkeypatch.setattr(settings, "detect_evidence_mode", "serve")
    monkeypatch.setattr(settings, "detect_evidence_timeout_seconds", timeout)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert app.state.evidence_engine.reason == "invalid_evidence_config"
        assert app.state.evidence_engine.timeout_seconds == 0


@pytest.mark.anyio
async def test_bad_bundle_does_not_call_router(tmp_path, artifacts):
    path, _ = write_bundle(tmp_path, artifacts)
    engine = EvidenceEngine(mode="serve", bundle_path=str(path), bundle_sha256="f" * 64)

    def forbidden(request):
        pytest.fail("Bad Bundle must not contact Router")

    async with mock_client(forbidden) as client:
        result = await engine.run(LONG_TEXT, main_label="AI", client=client)
    assert result["quality"]["reasons"] == ["invalid_evidence_bundle"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("https://router.example/detect", "https://router.example/evidence/route"),
        (
            "https://router.example/proxy/v1/detect/",
            "https://router.example/proxy/v1/evidence/route",
        ),
        ("https://router.example/detect.php", None),
        ("https://router.example/detect?key=private", None),
        ("https://router.example/detect#fragment", None),
        ("https://user:password@router.example/detect", None),
    ],
)
async def test_effective_endpoint_reuses_token_and_never_guesses(
    ready_engine, endpoint, expected
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response("failed", reason="model_failure"))

    async with mock_client(handler, endpoint) as client:
        result = await ready_engine.run(LONG_TEXT, main_label="AI", client=client)
    assert len(calls) == (1 if expected else 0)
    if expected:
        assert str(calls[0].url) == expected
        assert calls[0].headers["X-RepreGuard-Token"] == SERVICE_TOKEN
        assert json.loads(calls[0].content) == {"text": LONG_TEXT}
    assert result["quality"]["reasons"] == [
        "model_failure" if expected else "model_unavailable"
    ]


class BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"{" * (64 * 1024)
        yield b"}" * (64 * 1024 + 1)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case,reason",
    [
        ("disconnect", "model_unavailable"),
        ("unexpected_transport", "model_failure"),
        ("read_timeout", "timeout"),
        ("non_200", "model_unavailable"),
        ("redirect", "model_unavailable"),
        ("html", "invalid_router_response"),
        ("duplicate_key", "invalid_router_response"),
        ("nan", "invalid_router_response"),
        ("wrong_sha", "invalid_router_response"),
        ("extra_field", "invalid_router_response"),
        ("oversize_header", "invalid_router_response"),
        ("oversize_stream", "invalid_router_response"),
        ("unsupported", "unsupported_language"),
        ("busy", "busy"),
        ("timeout", "timeout"),
        ("model_unavailable", "model_unavailable"),
        ("model_failure", "model_failure"),
        ("language_undetermined", "language_undetermined"),
    ],
)
async def test_http_and_contract_failures_are_isolated(
    ready_engine, monkeypatch, caplog, case, reason
):
    calls = []

    def forbidden_features(*args, **kwargs):
        raise AssertionError("Failure must not load feature dependencies")

    monkeypatch.setattr(
        "app.services.evidence_features.extract_document_features", forbidden_features
    )

    def handler(request):
        calls.append(request)
        if case == "disconnect":
            raise httpx.ReadError("private document and token", request=request)
        if case == "unexpected_transport":
            raise RuntimeError("private document and token")
        if case == "read_timeout":
            raise httpx.ReadTimeout("private document and token", request=request)
        if case in {"non_200", "redirect"}:
            return httpx.Response(
                503 if case == "non_200" else 307,
                content=b"private document and token",
                headers={"location": "https://other.example"},
            )
        if case == "html":
            return httpx.Response(200, content=b"<html>private document</html>")
        if case == "duplicate_key":
            body = json.dumps(response()).replace(
                '"schemaVersion": 1', '"schemaVersion": 2, "schemaVersion": 1'
            )
            return httpx.Response(200, content=body)
        if case == "nan":
            return httpx.Response(
                200,
                content=json.dumps(response()).replace(
                    '"language": 0.8', '"language": NaN'
                ),
            )
        if case == "oversize_header":
            return httpx.Response(
                200, content=b"{}", headers={"content-length": str(128 * 1024 + 1)}
            )
        if case == "oversize_stream":
            return httpx.Response(200, stream=BrokenStream())
        payload = (
            response("unsupported", reason=reason)
            if case == "unsupported"
            else response("failed", reason=reason)
        )
        if case == "wrong_sha":
            payload = response(sha="f" * 64)
        if case == "extra_field":
            payload = {**response(), "text": "private document"}
        return httpx.Response(200, json=payload)

    with caplog.at_level(logging.INFO, logger="app.services.evidence_engine"):
        async with mock_client(handler) as client:
            result = await ready_engine.run(LONG_TEXT, main_label="AI", client=client)
    assert len(calls) == 1
    assert result["status"] == ("unsupported" if case == "unsupported" else "failed")
    assert result["quality"]["reasons"] == [reason]
    EvidenceResult.model_validate(result)
    assert "private document" not in caplog.text
    assert LONG_TEXT not in caplog.text
    assert "elapsed_ms=" in caplog.text


@pytest.mark.anyio
async def test_trickling_http_is_cancelled_at_total_deadline(ready_engine):
    closed = Event()

    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b" "
                await asyncio.sleep(0.01)

        async def aclose(self):
            closed.set()

    ready_engine.timeout_seconds = 0.05
    async with mock_client(
        lambda request: httpx.Response(200, stream=Trickle())
    ) as client:
        result = await ready_engine.run(LONG_TEXT, main_label="AI", client=client)
        await asyncio.wait_for(ready_engine.drain(), 1)
    assert result["quality"]["reasons"] == ["timeout"]
    assert closed.is_set()
    assert ready_engine._task is None


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [False, True])
async def test_timed_out_or_cancelled_thread_keeps_slot_until_finished(
    ready_engine, monkeypatch, cancel
):
    started, release = Event(), Event()
    calls = []

    def blocked_compare(text, payload, *, main_label):
        started.set()
        assert release.wait(3)
        return ready_engine._empty_result("comparison_failed")

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response())

    monkeypatch.setattr(ready_engine, "analyze", blocked_compare)
    ready_engine.timeout_seconds = 0.1
    async with mock_client(handler) as client:
        caller = asyncio.create_task(
            ready_engine.run(LONG_TEXT, main_label="AI", client=client)
        )
        try:
            assert await asyncio.to_thread(started.wait, 2)
            if cancel:
                caller.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await caller
            else:
                assert (await caller)["quality"]["reasons"] == ["timeout"]
            occupied = ready_engine._task
            for _ in range(3):
                busy = await ready_engine.run(LONG_TEXT, main_label="AI", client=client)
                assert busy["quality"]["reasons"] == ["busy"]
                assert ready_engine._task is occupied
            assert len(calls) == 1
            draining = asyncio.create_task(ready_engine.drain())
            await asyncio.sleep(0)
            assert not draining.done()
        finally:
            release.set()
            await asyncio.wait_for(ready_engine.drain(), 2)
        await draining
        assert ready_engine._task is None
        assert (await ready_engine.run(LONG_TEXT, main_label="AI", client=client))[
            "quality"
        ]["reasons"] == ["comparison_failed"]
        assert len(calls) == 2


@pytest.mark.anyio
async def test_http_time_is_part_of_comparison_budget(ready_engine, monkeypatch):
    compared = Event()

    async def handler(request):
        await asyncio.sleep(0.08)
        return httpx.Response(200, json=response())

    def compare(text, payload, *, main_label):
        compared.set()
        time.sleep(0.08)
        raise RuntimeError("private comparison failure")

    monkeypatch.setattr(ready_engine, "analyze", compare)
    ready_engine.timeout_seconds = 0.12
    async with mock_client(handler) as client:
        result = await ready_engine.run(LONG_TEXT, main_label="AI", client=client)
        assert compared.is_set()
        assert result["quality"]["reasons"] == ["timeout"]
        task = ready_engine._task
        assert task is not None
        await asyncio.wait_for(ready_engine.drain(), 1)
        assert task.result()["quality"]["reasons"] == ["comparison_failed"]


@contextmanager
def loopback_router(calls):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["content-length"]))
            calls.append(
                (self.path, self.headers["X-RepreGuard-Token"], json.loads(body))
            )
            encoded = json.dumps(response()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/prefix/detect"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "mode,path,timeout",
    [
        ("serve", "/api/v1/detect", "12"),
        ("shadow", "/api/v1/detect", "12"),
        ("off", "/api/v1/detect", "invalid"),
        ("serve", "/api/v1/detect", "invalid"),
        *[
            ("serve", path, "12")
            for path in [
                "/api/v1/scan/detect",
                "/api/v1/scan",
                "/api/scan/detect",
                "/api/scan",
            ]
        ],
    ],
)
def test_whole_text_over_real_http_modes_lease_and_replay(
    db_session, unique_email, tmp_path, artifacts, monkeypatch, mode, path, timeout
):
    bundle, sha = write_bundle(tmp_path, artifacts)
    settings = get_settings()
    monkeypatch.setattr(settings, "detect_evidence_mode", mode)
    monkeypatch.setattr(settings, "detect_evidence_bundle_path", str(bundle))
    monkeypatch.setattr(settings, "detect_evidence_bundle_sha256", sha)
    monkeypatch.setattr(settings, "detect_evidence_timeout_seconds", timeout)
    monkeypatch.setattr(settings, "detect_request_timeout", 120)
    monkeypatch.setattr(settings, "detect_service_timeout", 60)
    main_calls, router_calls, leases = [], [], []
    original_reserve = detection_api.reserve_detection_request

    def reserve(*args, **kwargs):
        leases.append(kwargs["lease_seconds"])
        return original_reserve(*args, **kwargs)

    async def main_detect(text):
        main_calls.append(text)
        return _fake_result()

    monkeypatch.setattr(detection_api, "reserve_detection_request", reserve)
    monkeypatch.setattr(repre_guard_client, "detect", main_detect)
    monkeypatch.setattr(repre_guard_client, "service_token", SERVICE_TOKEN)
    actor = asyncio.run(_user_actor(db_session, unique_email))
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_actor] = lambda: actor
    app.dependency_overrides[get_current_user] = lambda: actor.user
    text = ("  😀\r\n" + (LONG_TEXT + "\r\n\r\n") * 70)[:20000]
    key = {"Idempotency-Key": str(uuid4())}
    try:
        with loopback_router(router_calls) as endpoint:
            monkeypatch.setattr(repre_guard_client, "detect_url", endpoint)
            with TestClient(app) as client:
                first = client.post(path, json={"text": text}, headers=key)
                assert first.status_code == 200
                count = len(main_calls)
                assert count > 1
                second = client.post(path, json={"text": text}, headers=key)
                assert second.status_code == 200
                assert len(main_calls) == count
                data = first.json()
                if path == "/api/v1/detect":
                    assert (
                        data["score"],
                        data["rawScore"],
                        data["threshold"],
                    ) == pytest.approx((0.72, 0.72, 0.5))
                    assert data["label"] == "ai"
                    if mode == "serve":
                        assert data["evidence"]["status"] == (
                            "failed" if timeout == "invalid" else "partial"
                        )
                        EvidenceResult.model_validate(data["evidence"])
                    else:
                        assert "evidence" not in data
                assert second.json().get("evidence") == data.get("evidence")
                stored = db_session.scalar(
                    select(Detection).where(Detection.actor_id == actor.actor_id)
                )
                for history_path in ("/api/v1/history", f"/api/v1/history/{stored.id}"):
                    history = client.get(history_path)
                    assert history.status_code == 200
                    record = (
                        history.json()["items"][0]
                        if history_path.endswith("history")
                        else history.json()
                    )
                    assert record.get("evidence") == (
                        stored.meta_json.get("evidence") if mode == "serve" else None
                    )
                assert len(main_calls) == count
        enabled = mode != "off" and timeout != "invalid"
        assert leases == [162 if enabled else 150] * 2
        assert router_calls == (
            [
                (
                    "/prefix/evidence/route",
                    repre_guard_client.service_token,
                    {"text": text},
                )
            ]
            if enabled
            else []
        )
        stored = db_session.scalar(
            select(Detection).where(Detection.actor_id == actor.actor_id)
        )
        assert stored.input_text == text
        if mode == "off":
            assert (
                "evidence" not in stored.meta_json
                and "artifactVersion" not in stored.meta_json
            )
        else:
            EvidenceResult.model_validate(stored.meta_json["evidence"])
            assert (
                stored.meta_json["artifactVersion"]
                == stored.meta_json["evidence"]["artifactVersion"]
            )
        assert (
            db_session.scalar(
                select(func.count(Detection.id)).where(
                    Detection.actor_id == actor.actor_id
                )
            )
            == 1
        )
        assert db_session.scalar(
            select(QuotaUsage.used).where(QuotaUsage.actor_id == actor.actor_id)
        ) == len(text)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "outcome", ["timeout", "cancel", "stale_owner", "main_failure"]
)
async def test_evidence_preserves_settlement_and_cancellation(
    committed_db_session, unique_email, ready_engine, monkeypatch, outcome
):
    db_session = committed_db_session
    actor = await _user_actor(db_session, unique_email)
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", "serve")
    ready_engine.timeout_seconds = 0.05
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=SimpleNamespace(evidence_engine=ready_engine)),
        }
    )
    key = uuid4()
    started, release = asyncio.Event(), asyncio.Event()
    router_calls, main_calls = [], []

    async def main_detect(text):
        main_calls.append(text)
        if outcome == "main_failure":
            raise asyncio.TimeoutError
        return _fake_result()

    async def route(text):
        router_calls.append(text)
        started.set()
        if outcome in {"timeout", "cancel"}:
            await release.wait()
        if outcome == "stale_owner":
            record = db_session.scalar(
                select(RequestRecord).where(RequestRecord.idempotency_key == str(key))
            )
            record.owner_token = str(uuid4())
            db_session.commit()
        return json.dumps(response("failed", reason="model_failure")).encode()

    monkeypatch.setattr(repre_guard_client, "detect", main_detect)
    monkeypatch.setattr(repre_guard_client, "route_evidence", route)
    kwargs = dict(
        payload=DetectionRequest(text=LONG_TEXT),
        db=db_session,
        current_actor=actor,
        idempotency_key=key,
        request=request,
    )
    caller = asyncio.create_task(detection_api.detect(**kwargs))
    try:
        if outcome == "cancel":
            await asyncio.wait_for(started.wait(), 2)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
        elif outcome in {"stale_owner", "main_failure"}:
            with pytest.raises(HTTPException) as exc:
                await caller
            assert exc.value.status_code == (409 if outcome == "stale_owner" else 504)
        else:
            result = await caller
            assert result.evidence.quality.reasons == ["timeout"]
            assert (result.score, result.raw_score, result.threshold, result.label) == (
                0.72,
                0.72,
                0.5,
                "ai",
            )
            replay = await detection_api.detect(**kwargs)
            assert (
                replay.evidence == result.evidence
                and replay.detection_id == result.detection_id
            )
    finally:
        release.set()
        await asyncio.wait_for(ready_engine.drain(), 2)
    assert router_calls == ([] if outcome == "main_failure" else [LONG_TEXT])
    assert len(main_calls) == 1
    assert db_session.scalar(
        select(func.count(Detection.id)).where(Detection.actor_id == actor.actor_id)
    ) == (1 if outcome == "timeout" else 0)
    assert db_session.scalar(
        select(QuotaUsage.used).where(QuotaUsage.actor_id == actor.actor_id)
    ) == (len(LONG_TEXT) if outcome == "timeout" else 0)
    record = db_session.scalar(
        select(RequestRecord).where(RequestRecord.idempotency_key == str(key))
    )
    assert record.status == ("completed" if outcome == "timeout" else "processing")
