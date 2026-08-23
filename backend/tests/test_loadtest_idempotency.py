from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any
from uuid import UUID

import httpx
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
LOADTEST_DIR = REPO_ROOT / "scripts" / "loadtest"


def _load_script(name: str) -> ModuleType:
    path = LOADTEST_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"loadtest_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _assert_uuid(value: str) -> None:
    parsed = UUID(value)
    assert str(parsed) == value


def test_runner_runtime_context_generates_unique_idempotency_uuid() -> None:
    runner = _load_script("run_loadtest")

    first = runner.build_runtime_context({}, request_index=0, round_index=0, concurrency=2)
    second = runner.build_runtime_context({}, request_index=1, round_index=0, concurrency=2)

    _assert_uuid(first["idempotency_key"])
    _assert_uuid(second["idempotency_key"])
    assert first["idempotency_key"] != second["idempotency_key"]


def test_runner_dataset_cannot_override_runtime_idempotency_uuid() -> None:
    runner = _load_script("run_loadtest")

    context = runner.build_runtime_context(
        {"idempotency_key": "dataset-controlled"},
        request_index=0,
        round_index=0,
        concurrency=1,
    )

    _assert_uuid(context["idempotency_key"])
    assert context["idempotency_key"] != "dataset-controlled"


@pytest.mark.anyio
async def test_detect_100_users_sends_unique_idempotency_uuid() -> None:
    detect_module = _load_script("detect_100_users")
    captured_headers: list[dict[str, str]] = []

    class StubClient:
        async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> httpx.Response:
            captured_headers.append(headers)
            return httpx.Response(200, json={"ok": True}, request=httpx.Request("POST", url))

    token = detect_module.TokenInfo(value="token", source="test", actor_type="user")
    client = StubClient()
    await detect_module.detect_once(
        client=client,
        base_url="http://testserver",
        path="/api/v1/detect",
        token=token,
        text="x" * 200,
        request_id=1,
        round_index=0,
    )
    await detect_module.detect_once(
        client=client,
        base_url="http://testserver",
        path="/api/v1/detect",
        token=token,
        text="y" * 200,
        request_id=2,
        round_index=0,
    )

    keys = [headers["Idempotency-Key"] for headers in captured_headers]
    for key in keys:
        _assert_uuid(key)
    assert len(set(keys)) == 2


def test_bootstrap_detection_requests_send_unique_idempotency_uuid() -> None:
    bootstrap = _load_script("bootstrap_online")
    captured_headers: list[dict[str, str]] = []

    class StubSession:
        def request(self, method: str, path: str, **kwargs: Any) -> dict[str, int]:
            captured_headers.append(dict(kwargs.get("headers") or {}))
            sequence = len(captured_headers)
            return {"detection_id": sequence, "history_id": sequence}

    records = bootstrap.create_detections(StubSession(), "member-token", 2)

    assert len(records) == 2
    keys = [headers["Idempotency-Key"] for headers in captured_headers]
    for key in keys:
        _assert_uuid(key)
    assert len(set(keys)) == 2


def test_example_detect_scenarios_render_runtime_idempotency_key() -> None:
    config = json.loads((LOADTEST_DIR / "scenarios.example.json").read_text(encoding="utf-8"))
    detect_scenarios = [scenario for scenario in config["scenarios"] if scenario["name"] in {"detect_member", "detect_guest_pool"}]

    assert len(detect_scenarios) == 2
    for scenario in detect_scenarios:
        assert scenario["headers"]["Idempotency-Key"] == "{{idempotency_key}}"
