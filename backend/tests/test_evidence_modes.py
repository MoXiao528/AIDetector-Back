"""EV4-03 mode/output checks; Router responses and stored snapshots are fixtures."""

import copy
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from fastapi import Request
import pytest
from sqlalchemy import func, select
import yaml

from app.api.v1 import detections as detection_api
from app.api.v1.admin import get_admin_detection
from app.api.v1.history import (
    _detection_to_history_response,
    create_history,
    get_history,
    list_histories,
)
from app.core.config import get_settings
from app.db.deps import get_current_user
from app.db.session import get_db
from app.main import app
from app.models.detection import Detection
from app.models.guest_session import GuestSession
from app.models.quota_usage import QuotaUsage
from app.schemas.detection import DetectionItem, DetectionRequest, DetectionResponse
from app.schemas.evidence import EvidenceResult, project_public_evidence
from app.schemas.history import HistoryRecordCreate, project_public_meta_json
from app.services.evidence_engine import EvidenceEngine
from app.services.history_service import HistoryService
from app.services.repre_guard_client import repre_guard_client

from test_detection_idempotency import LONG_TEXT, _fake_result, _user_actor
from test_detection_routes import _install_route_overrides
import test_evidence_engine as engine_tests
from test_evidence_engine import response, write_bundle


artifacts = engine_tests.artifacts
comparison_input = engine_tests.comparison_input
ready_engine = engine_tests.ready_engine

MODES = ["off", "shadow", "serve", "invalid-mode"]
DETECT_PATHS = [
    "/api/v1/detect",
    "/api/v1/scan/detect",
    "/api/v1/scan",
    "/api/scan/detect",
    "/api/scan",
]


@pytest.fixture(autouse=True)
def optional_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "detect_evidence_mode", "off")
    monkeypatch.setattr(settings, "detect_evidence_bundle_path", "")
    monkeypatch.setattr(settings, "detect_evidence_bundle_sha256", "")


@pytest.fixture
def snapshot():
    return {
        "status": "failed",
        "artifactVersion": "1" * 64,
        "featureSchemaVersion": 1,
        "route": None,
        "quality": {"level": "unavailable", "coverage": 0.0, "reasons": ["timeout"]},
        "signals": [],
        "patterns": None,
    }


def detection_response(evidence=None):
    return DetectionResponse(
        detection_id=1,
        label="ai",
        score=0.72,
        raw_score=0.72,
        threshold=0.5,
        model_name="test-model",
        currentCredits=100,
        history_id=1,
        input_text=LONG_TEXT,
        evidence=evidence,
    )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("path", DETECT_PATHS)
def test_actual_response_serialization_gates_evidence_only(
    db_session, monkeypatch, optional_settings, snapshot, mode, path
):
    baseline = detection_response().model_dump(mode="json", by_alias=True)
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", mode)
    before = copy.deepcopy(snapshot)

    async def fixture_result(*args, **kwargs):
        return detection_response(snapshot)

    monkeypatch.setattr(detection_api, "_detect_impl", fixture_result)
    _install_route_overrides(db_session)
    try:
        with TestClient(app) as client:
            result = client.post(
                path,
                json={"text": LONG_TEXT},
                headers={"Idempotency-Key": str(uuid4())},
            )
        assert result.status_code == 200
        data = result.json()
        if path == "/api/v1/detect":
            if mode == "serve":
                assert data.pop("evidence") == snapshot
            assert (
                data == baseline
            )  # Includes the old result:null and other null fields.
        else:
            assert "evidence" not in data
            assert data["summary"] == "No detailed analysis available."
        assert snapshot == before
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("mode", MODES)
def test_shadow_and_serve_share_real_local_calculation(
    tmp_path, artifacts, comparison_input, monkeypatch, optional_settings, mode
):
    path, sha = write_bundle(tmp_path, artifacts)
    engine = EvidenceEngine(mode=mode, bundle_path=str(path), bundle_sha256=sha)
    result = engine.analyze(comparison_input["text"], response(), main_label="AI")
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", mode)
    public = detection_response(result).model_dump(mode="json")
    if mode in {"shadow", "serve"}:
        assert result["status"] == "partial"
        assert EvidenceResult.model_validate(result).model_dump() == result
        other = EvidenceEngine(
            mode="serve" if mode == "shadow" else "shadow",
            bundle_path=str(path),
            bundle_sha256=sha,
        )
        assert (
            other.analyze(comparison_input["text"], response(), main_label="AI")
            == result
        )
    else:
        assert comparison_input["calls"] == []
    assert ("evidence" in public) == (mode == "serve")
    assert (
        public["score"],
        public["raw_score"],
        public["threshold"],
        public["label"],
    ) == (0.72, 0.72, 0.5, "ai")


@pytest.mark.parametrize("mode", MODES)
def test_lifespan_loads_once_and_never_runs_analysis(
    tmp_path, artifacts, monkeypatch, optional_settings, mode
):
    path, sha = write_bundle(tmp_path, artifacts)
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", mode)
    monkeypatch.setattr(get_settings(), "detect_evidence_bundle_path", str(path))
    monkeypatch.setattr(get_settings(), "detect_evidence_bundle_sha256", sha)
    reads = []
    original_open = Path.open

    def counted_open(target, *args, **kwargs):
        if target == path:
            reads.append(target)
        return original_open(target, *args, **kwargs)

    def forbidden_analysis(*args, **kwargs):
        raise AssertionError("Lifecycle/read routes must not compute Evidence")

    monkeypatch.setattr(Path, "open", counted_open)
    monkeypatch.setattr(EvidenceEngine, "analyze", forbidden_analysis)
    with TestClient(app) as client:
        engine = app.state.evidence_engine
        assert engine.status == {"off": "off", "invalid-mode": "failed"}.get(
            mode, "ready"
        )
        assert client.get("/").status_code == client.get("/").status_code == 200
        assert app.state.evidence_engine is engine
    assert len(reads) == (1 if mode in {"shadow", "serve"} else 0)
    assert app.state.evidence_engine is None


def test_unexpected_optional_initialization_failure_does_not_break_app(
    monkeypatch, caplog
):
    def fail(**kwargs):
        raise RuntimeError("private bundle path and document")

    monkeypatch.setattr("app.main.EvidenceEngine", fail)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert app.state.evidence_engine is None
    assert "private bundle path" not in caplog.text


def test_off_app_lifespan_does_not_import_feature_module():
    script = """
import asyncio, sys
sys.path.insert(0, sys.argv[1])
from app.main import app, lifespan, settings
settings.detect_evidence_mode = 'off'
settings.detect_evidence_bundle_path = 'unused-private-path'
settings.detect_evidence_bundle_sha256 = 'invalid-unused-sha'
async def check():
    async with lifespan(app):
        assert app.state.evidence_engine.status == 'off'
        assert 'app.services.evidence_features' not in sys.modules
asyncio.run(check())
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            script,
            str(Path(__file__).resolve().parents[1]),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "damage",
    [
        "extra",
        "nested_extra",
        "nan",
        "version_bool",
        "wrong_status",
        "private_reason",
        "non_object",
    ],
)
def test_malformed_snapshot_is_omitted_without_changing_main_result(
    snapshot, optional_settings, monkeypatch, damage
):
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", "serve")
    if damage == "extra":
        snapshot["path"] = "private-path"
    elif damage == "nested_extra":
        snapshot["quality"]["text"] = "private-text"
    elif damage == "nan":
        snapshot["quality"]["coverage"] = float("nan")
    elif damage == "version_bool":
        snapshot["featureSchemaVersion"] = True
    elif damage == "wrong_status":
        snapshot["status"] = "ready"
    elif damage == "private_reason":
        snapshot["quality"]["reasons"] = ["private failure text"]
    else:
        snapshot = [snapshot]
    assert (
        detection_response(snapshot).model_dump() == detection_response().model_dump()
    )


def test_serializing_prebuilt_response_after_switch_off_hides_evidence(
    snapshot, optional_settings, monkeypatch
):
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", "serve")
    built = detection_response(snapshot)
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", "off")
    assert "evidence" not in built.model_dump(mode="json")


@pytest.mark.parametrize(
    "failure",
    [
        "model_unavailable",
        "model_failure",
        "busy",
        "timeout",
        "language_undetermined",
        "unsupported_language",
    ],
)
def test_real_engine_degradation_remains_a_valid_public_result(
    ready_engine, monkeypatch, failure
):
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", "serve")
    status = "unsupported" if failure == "unsupported_language" else "failed"
    result = ready_engine.analyze(
        LONG_TEXT, response(status, reason=failure), main_label="AI"
    )
    public = detection_response(result).model_dump(mode="json")
    assert public["evidence"] == result
    assert public["evidence"]["quality"]["reasons"] == [failure]
    assert (
        public["score"] == 0.72
        and public["threshold"] == 0.5
        and public["label"] == "ai"
    )


@pytest.mark.parametrize("failure", ["feature", "comparison", "cell"])
def test_computation_failures_do_not_break_public_response(
    ready_engine, comparison_input, monkeypatch, failure
):
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", "serve")

    def fail(*args, **kwargs):
        raise RuntimeError("private input and exception")

    if failure == "feature":
        monkeypatch.setattr(
            "app.services.evidence_features.extract_document_features", fail
        )
    elif failure == "comparison":
        monkeypatch.setattr("app.services.evidence_engine._select_reference_cell", fail)
    else:
        ready_engine.reference["cells"] = []
    result = ready_engine.analyze(comparison_input["text"], response(), main_label="AI")
    public = detection_response(result).model_dump(mode="json")
    assert public["evidence"] == result
    assert result["status"] == ("insufficient" if failure == "cell" else "failed")
    assert "private input" not in json.dumps(public)


@pytest.mark.parametrize("mode", ["shadow", "serve"])
def test_bad_bundle_does_not_break_lifespan_or_main_result(monkeypatch, tmp_path, mode):
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", mode)
    monkeypatch.setattr(
        get_settings(), "detect_evidence_bundle_path", str(tmp_path / "missing.bundle")
    )
    monkeypatch.setattr(get_settings(), "detect_evidence_bundle_sha256", "1" * 64)
    with TestClient(app) as client:
        engine = app.state.evidence_engine
        assert engine.status == "failed"
        assert client.get("/").status_code == 200
        result = engine.analyze(LONG_TEXT, response(), main_label="AI")
        public = detection_response(result).model_dump(mode="json")
        assert ("evidence" in public) == (mode == "serve")
        assert public["score"] == 0.72 and public["label"] == "ai"


@pytest.mark.anyio
@pytest.mark.parametrize("mode", MODES)
async def test_new_detection_replay_history_and_admin_boundaries(
    db_session, unique_email, monkeypatch, optional_settings, snapshot, mode
):
    actor = await _user_actor(db_session, unique_email)
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", mode)
    key = uuid4()
    calls = []

    async def main_detect(text):
        calls.append(text)
        return _fake_result()

    def forbidden_evidence(*args, **kwargs):
        raise AssertionError(
            "Calls without a runtime and stored-result reads must not compute Evidence"
        )

    monkeypatch.setattr(repre_guard_client, "detect", main_detect)
    monkeypatch.setattr(EvidenceEngine, "analyze", forbidden_evidence)
    request = DetectionRequest(
        text=LONG_TEXT, functions=["scan"], options={"evidence": snapshot}
    )
    first = await detection_api.detect(
        payload=request, db=db_session, current_actor=actor, idempotency_key=key
    )
    count = len(calls)
    stored = db_session.get(Detection, first.detection_id)
    assert "evidence" not in first.model_dump()
    assert (
        "evidence" not in stored.meta_json
    )  # Client options never become server Evidence.
    assert "evidence" not in stored.meta_json["analysis"]
    stored.meta_json = {
        **stored.meta_json,
        "evidence": copy.deepcopy(snapshot),
        "artifactVersion": "1" * 64,
    }
    db_session.flush()  # Deliberate old-snapshot fixture, independent of runtime loading.
    before = copy.deepcopy(stored.meta_json)

    replay = await detection_api.detect(
        payload=request, db=db_session, current_actor=actor, idempotency_key=key
    )
    detail = await get_history(
        history_id=stored.id, db=db_session, current_user=actor.user
    )
    listed = await list_histories(
        db=db_session,
        current_user=actor.user,
        page=1,
        per_page=20,
        sort="created_at",
        order="desc",
        q=None,
        pinned=None,
    )
    for result in (
        replay,
        detail,
        listed.items[0],
        _detection_to_history_response(stored),
    ):
        public = result.model_dump(mode="json", by_alias=True)
        assert ("evidence" in public) == (mode == "serve")
        if mode == "serve":
            assert public["evidence"] == snapshot
    assert replay.model_dump(exclude={"evidence"}) == first.model_dump(
        exclude={"evidence"}
    )
    assert len(calls) == count
    assert (
        db_session.scalar(
            select(func.count(Detection.id)).where(Detection.user_id == actor.user.id)
        )
        == 1
    )
    usage = db_session.scalar(
        select(QuotaUsage).where(QuotaUsage.actor_id == actor.actor_id)
    )
    assert usage.used == len(LONG_TEXT)

    admin = await get_admin_detection(
        detection_id=stored.id, db=db_session, _=actor.user
    )
    for public in (
        DetectionItem.from_orm_detection(stored).meta_json,
        admin.meta_json,
        project_public_meta_json(before),
    ):
        assert "evidence" not in public and "artifactVersion" not in public
        assert (
            public["options"] == before["options"]
        )  # Preserve the existing option sanitizer output.
    db_session.refresh(stored)
    assert stored.meta_json == before

    # Exercise nested list serialization as well as detail through the real ASGI routes.
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: actor.user
    try:
        with TestClient(app) as client:
            for candidate in (snapshot, {**snapshot, "unexpected": "private value"}):
                stored.meta_json = {**before, "evidence": candidate}
                db_session.flush()
                for path in ("/api/v1/history", f"/api/v1/history/{stored.id}"):
                    result = client.get(path)
                    assert result.status_code == 200
                    data = result.json()
                    record = data["items"][0] if path.endswith("history") else data
                    assert ("evidence" in record) == (
                        mode == "serve" and candidate == snapshot
                    )
                rebuilt = await detection_api.detect(
                    payload=request,
                    db=db_session,
                    current_actor=actor,
                    idempotency_key=key,
                )
                assert rebuilt.model_dump(exclude={"evidence"}) == first.model_dump(
                    exclude={"evidence"}
                )
        assert len(calls) == count
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_client_history_cannot_write_server_evidence(
    db_session, unique_email, monkeypatch, optional_settings, snapshot
):
    actor = await _user_actor(db_session, unique_email)
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", "serve")
    payload = HistoryRecordCreate.model_validate(
        {
            "title": "manual",
            "functions": ["scan"],
            "inputText": LONG_TEXT,
            "editorHtml": "",
            "evidence": snapshot,
            "analysis": {
                "summary": {"ai": 0, "human": 100},
                "sentences": [],
                "aiLikelyCount": 0,
                "highlightedHtml": "",
                "evidence": snapshot,
            },
        }
    )
    created = await create_history(
        payload=payload, db=db_session, current_user=actor.user
    )
    stored = db_session.get(Detection, created.id)
    assert "evidence" not in created.model_dump()
    assert (
        "evidence" not in stored.meta_json
        and "evidence" not in stored.meta_json["analysis"]
    )


def test_modes_do_not_load_artifacts_while_projecting(
    snapshot, monkeypatch, optional_settings
):
    def forbidden(*args, **kwargs):
        raise AssertionError("Projection must not load an artifact")

    monkeypatch.setattr(Path, "open", forbidden)
    for mode in MODES:
        monkeypatch.setattr(get_settings(), "detect_evidence_mode", mode)
        assert (project_public_evidence(snapshot) is not None) == (mode == "serve")
    assert "evidence" not in project_public_meta_json({"evidence": object()})


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["extra_field", "nonfinite", "serialization"])
async def test_invalid_computed_snapshot_is_omitted_before_database_write(
    db_session, unique_email, monkeypatch, snapshot, failure
):
    actor = await _user_actor(db_session, unique_email)
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", "serve")
    candidate = copy.deepcopy(snapshot)
    if failure == "extra_field":
        candidate["private"] = "must not persist"
    elif failure == "nonfinite":
        candidate["quality"]["coverage"] = float("nan")
    else:

        def fail_serialization(*args, **kwargs):
            raise ValueError("private serialization error")

        monkeypatch.setattr(EvidenceResult, "model_dump", fail_serialization)
    calls = []

    async def run(*args, **kwargs):
        calls.append("evidence")
        return candidate

    async def main_detect(text):
        calls.append("main")
        return _fake_result()

    engine = EvidenceEngine(mode="serve")
    monkeypatch.setattr(engine, "run", run)
    monkeypatch.setattr(repre_guard_client, "detect", main_detect)
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=SimpleNamespace(evidence_engine=engine)),
        }
    )
    kwargs = dict(
        payload=DetectionRequest(text=LONG_TEXT),
        db=db_session,
        current_actor=actor,
        idempotency_key=uuid4(),
        request=request,
    )
    first = await detection_api.detect(**kwargs)
    replay = await detection_api.detect(**kwargs)
    assert first.evidence is replay.evidence is None
    assert first.detection_id == replay.detection_id
    stored = db_session.get(Detection, first.detection_id)
    assert (
        "evidence" not in stored.meta_json and "artifactVersion" not in stored.meta_json
    )
    assert (stored.score, stored.result_label) == (0.72, "ai")
    assert calls == ["main", "evidence"]
    assert db_session.scalar(
        select(QuotaUsage.used).where(QuotaUsage.actor_id == actor.actor_id)
    ) == len(LONG_TEXT)


@pytest.mark.anyio
async def test_saved_snapshot_survives_mode_bundle_history_edits_and_claim(
    db_session, unique_email, monkeypatch, snapshot
):
    actor = await _user_actor(db_session, unique_email)
    monkeypatch.setattr(get_settings(), "detect_evidence_mode", "shadow")

    async def run(*args, **kwargs):
        return copy.deepcopy(snapshot)

    async def main_detect(text):
        return _fake_result()

    engine = EvidenceEngine(mode="serve")
    monkeypatch.setattr(engine, "run", run)
    monkeypatch.setattr(repre_guard_client, "detect", main_detect)
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=SimpleNamespace(evidence_engine=engine)),
        }
    )
    kwargs = dict(
        payload=DetectionRequest(text=LONG_TEXT),
        db=db_session,
        current_actor=actor,
        idempotency_key=uuid4(),
        request=request,
    )
    first = await detection_api.detect(**kwargs)
    stored = db_session.get(Detection, first.detection_id)
    assert first.evidence is None
    assert stored.meta_json["evidence"] == snapshot
    assert stored.meta_json["artifactVersion"] == snapshot["artifactVersion"]
    before = copy.deepcopy(stored.meta_json)

    def forbidden(*args, **kwargs):
        raise AssertionError("Stored-result reads must not recompute")

    monkeypatch.setattr(engine, "run", forbidden)
    monkeypatch.setattr(repre_guard_client, "detect", forbidden)
    monkeypatch.setattr(
        get_settings(), "detect_evidence_bundle_path", "missing-new-bundle"
    )
    monkeypatch.setattr(get_settings(), "detect_evidence_bundle_sha256", "f" * 64)
    for mode in ("off", "serve", "shadow", "serve"):
        monkeypatch.setattr(get_settings(), "detect_evidence_mode", mode)
        replay = await detection_api.detect(**kwargs)
        detail = await get_history(
            history_id=stored.id, db=db_session, current_user=actor.user
        )
        for value in (replay, detail):
            assert value.model_dump(mode="json").get("evidence") == (
                snapshot if mode == "serve" else None
            )
        db_session.refresh(stored)
        assert stored.meta_json == before

    service = HistoryService(db_session)
    service.update_history(
        user_id=actor.user.id, history_id=stored.id, title="Renamed", is_pinned=True
    )
    db_session.refresh(stored)
    assert stored.meta_json == before and stored.is_pinned and stored.title == "Renamed"
    guest_id = str(uuid4())
    db_session.add(
        GuestSession(
            id=guest_id,
            refresh_token_hash="c" * 64,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    # Claim changes only ownership; keep this complete snapshot as the migration fixture.
    stored.user_id, stored.actor_type, stored.actor_id = None, "guest", guest_id
    db_session.commit()
    assert service.claim_guest_histories(user_id=actor.user.id, guest_id=guest_id) == 1
    db_session.refresh(stored)
    assert stored.meta_json == before and stored.user_id == actor.user.id


def test_openapi_exposes_evidence_only_on_output_models():
    schemas = app.openapi()["components"]["schemas"]
    for name in ("DetectionResponse", "HistoryRecordResponse"):
        assert "evidence" in schemas[name]["properties"]
        assert "evidence" not in schemas[name].get("required", [])
    for name, schema in schemas.items():
        if name in {
            "DetectionRequest",
            "HistoryRecordCreate",
            "AnalysisResponse",
        } or name.startswith("Analysis-"):
            assert "evidence" not in schema["properties"]
    assert "mixed" not in json.dumps(schemas).casefold()

    contract_path = Path(__file__).resolve().parents[2] / "contract" / "openapi.yaml"
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    notice = contract["components"]["schemas"]["EvidenceSignal"]["properties"]["notice"]
    assert notice["nullable"] is True
    # Nullable does not override enum: normal Engine results contain notice:null.
    assert set(notice["enum"]) == {None, "reference_mismatch", "outside_both"}
