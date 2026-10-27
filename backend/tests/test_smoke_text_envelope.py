import json
from types import SimpleNamespace

import pytest

import app.core.config as config_module
import app.services.evidence_engine as engine_module
import app.services.repre_guard_client as client_module
import smoke_text_envelope as smoke
from test_evidence_engine import ROUTER_SHA, response


@pytest.fixture
def probe(monkeypatch):
    state = SimpleNamespace(
        main={"score": 0.25, "threshold": 0.5, "label": "HUMAN", "score_type": "probability", "model_name": "test"},
        evidence=json.dumps(response()).encode(),
        exception=None,
        close_exception=None,
        calls=[],
        closed=False,
        options=None,
        engine_options=None,
    )
    engine = engine_module.EvidenceEngine(mode="off")
    engine.status = "ready"
    engine.router_artifact_sha256 = ROUTER_SHA
    engine.timeout_seconds = 1
    state.engine = engine

    def build_engine(**kwargs):
        state.engine_options = kwargs
        return engine

    class Client:
        def __init__(self, **kwargs):
            state.options = kwargs

        async def detect(self, text):
            state.calls.append(("detect", text))
            if state.exception:
                raise state.exception
            return state.main

        async def route_evidence(self, text):
            state.calls.append(("evidence", text))
            return state.evidence

        async def aclose(self):
            state.closed = True
            if state.close_exception:
                raise state.close_exception

    monkeypatch.setattr(config_module, "get_settings", lambda: SimpleNamespace(
        detect_evidence_mode="off",
        detect_evidence_bundle_path="private-bundle-path",
        detect_evidence_bundle_sha256="2" * 64,
        detect_evidence_timeout_seconds="12",
        repre_guard_service_token=SimpleNamespace(get_secret_value=lambda: "private-token"),
    ))
    monkeypatch.setattr(engine_module, "EvidenceEngine", build_engine)
    monkeypatch.setattr(client_module, "RepreGuardClient", Client)
    return state


@pytest.mark.anyio
async def test_success_uses_explicit_envelope_and_validates_existing_contract(probe):
    assert await smoke.check("https://private.example/aidetect.php") is None
    assert probe.options == {
        "detect_url": "https://private.example/aidetect.php",
        "service_token": "private-token",
        "text_envelope_enabled": True,
    }
    assert probe.engine_options == {
        "mode": "serve", "bundle_path": "private-bundle-path",
        "bundle_sha256": "2" * 64, "timeout_seconds": "12",
    }
    assert probe.calls == [("detect", smoke.MAIN_TEXT), ("evidence", smoke.EVIDENCE_TEXT)]
    assert probe.closed


@pytest.mark.anyio
@pytest.mark.parametrize("changes", [
    {"score_type": "raw_logit"}, {"score": float("nan")}, {"score": float("inf")},
    {"score": -0.1}, {"score": 1.1}, {"score": True}, {"threshold": 1.1},
    {"label": "AI"}, {"model_name": " "}, {"model_name": None},
])
async def test_bad_probability_fails_before_evidence_and_closes(probe, changes):
    probe.main.update(changes)
    assert await smoke.check("https://private.example/detect") == "MAIN_CHECK_FAILED"
    assert [action for action, _ in probe.calls] == ["detect"]
    assert probe.closed


@pytest.mark.anyio
async def test_probability_threshold_equality_uses_ai_label(probe):
    probe.main.update(score=0.5, label="AI")
    assert await smoke.check("https://private.example/detect") is None


@pytest.mark.anyio
@pytest.mark.parametrize("payload", [
    response("failed", reason="busy"),
    response(sha="f" * 64),
    {key: value for key, value in response().items() if key != "schemaVersion"},
    {**response(), "extra": "private"},
    {**response(), "schemaVersion": True},
])
async def test_evidence_requires_five_fields_sha_and_routed_status(probe, payload):
    probe.evidence = json.dumps(payload).encode()
    assert await smoke.check("https://private.example/detect") == "EVIDENCE_CHECK_FAILED"
    assert probe.closed


@pytest.mark.anyio
@pytest.mark.parametrize("encoded", [
    b'{"private":', b'{"status":"routed","status":"failed"}',
    b'{"private":NaN}', b'{"private":"\xff"}',
])
async def test_evidence_uses_strict_json_parser(probe, encoded):
    probe.evidence = encoded
    assert await smoke.check("https://private.example/detect") == "EVIDENCE_CHECK_FAILED"
    assert probe.closed


@pytest.mark.anyio
async def test_unavailable_bundle_prevents_network(probe):
    probe.engine.status = "failed"
    assert await smoke.check("https://private.example/detect") == "CONFIG_CHECK_FAILED"
    assert probe.options is None
    assert probe.calls == []


@pytest.mark.anyio
async def test_blank_explicit_url_cannot_fall_back_to_configured_endpoint(probe):
    assert await smoke.check("  ") == "CONFIG_CHECK_FAILED"
    assert probe.options is None
    assert probe.calls == []


def test_cli_exception_output_is_sanitized_and_client_is_closed(probe, capsys):
    probe.exception = RuntimeError("private-token private-bundle-path https://private.example original text")
    assert smoke.main(["--url", "https://private.example/detect"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "FAIL MAIN_CHECK_FAILED\n"
    assert captured.err == ""
    assert probe.closed


def test_cli_success_is_generic(probe, capsys):
    assert smoke.main(["--url", "https://private.example/detect"]) == 0
    assert capsys.readouterr().out == "PASS TEXT_ENVELOPE\n"


def test_cli_close_failure_is_sanitized(probe, capsys):
    probe.close_exception = RuntimeError("private-token private path")
    assert smoke.main(["--url", "https://private.example/detect"]) == 1
    assert capsys.readouterr().out == "FAIL CLIENT_CLOSE_FAILED\n"
    assert probe.closed


def test_cli_invalid_arguments_do_not_echo_values(capsys):
    assert smoke.main(["--unknown", "private-token"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "FAIL INVALID_ARGUMENTS\n"
    assert captured.err == ""
