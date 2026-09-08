"""Opt-in real models + backend API + isolated SQLite snapshot acceptance."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base_class import Base
from app.db.session import get_db
from app.main import app
from app.models.detection import Detection
from app.models.quota_usage import QuotaUsage
from app.services.evidence_engine import EvidenceEngine
from app.services.repre_guard_client import repre_guard_client
from app.services.token_chunker import get_tokenizer
from test_evidence_engine import REAL_BUNDLE_SHA


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_EVIDENCE_REAL_RUNTIME") != "1",
    reason="Explicit real-model acceptance only; never downloads or installs",
)


# Override conftest's unit-test tokenizer fixture: this entire path must be real.
@pytest.fixture(autouse=True)
def fake_tokenizer():
    yield
    get_tokenizer.cache_clear()


EN_PARAGRAPH = (
    "The city library opened a new reading room on Monday morning. "
    "Students can reserve quiet desks through the library website before they arrive. "
    "A local teacher said the extra space would help families during examination weeks. "
    "Staff members moved the reference collection to the first floor last month. "
    "Visitors can still borrow novels and newspapers from the main entrance desk. "
    "The renovation includes brighter lamps and adjustable chairs for younger readers. "
    "Several residents asked the council to extend opening hours during the weekend. "
    "Officials will review attendance records after the first three months of operation. "
    "The project was funded by the city budget and donations from local businesses. "
    "Community volunteers will offer weekly workshops about finding reliable sources. "
)
ZH_PARAGRAPH = (
    "市图书馆本周开放了新的阅览室，附近学校的学生可以提前预约座位。"
    "工作人员介绍，这次改造增加了照明设备，也保留了原来的报刊区。"
    "一位老师认为，安静的学习环境能够帮助学生准备期末考试。"
    "居民希望周末延长开放时间，方便平时需要上班的家长陪孩子阅读。"
    "管理人员计划三个月后统计使用情况，再决定是否增加晚间服务。"
    "社区志愿者还会举办资料检索活动，介绍如何核对新闻来源。"
    "装修费用来自年度预算和本地企业捐款，具体开支将在网站公布。"
    "儿童阅读区使用可以调节高度的桌椅，入口附近也新增了无障碍通道。"
    "借阅规则没有改变，读者仍然可以使用原来的借书卡办理手续。"
    "图书馆提醒大家妥善保管个人物品，离开时把阅读材料放回指定位置。"
)
CASES = {
    "en": "\n\n".join([EN_PARAGRAPH] * 3),
    "zh": "\n\n".join([ZH_PARAGRAPH] * 3),
    "unsupported": (
        "La biblioteca sarà aperta anche la domenica e gli studenti potranno studiare insieme. "
        "Il comune ha rinnovato le sale di lettura con nuove sedie e lampade. "
        "I cittadini potranno prenotare un posto sul sito della biblioteca. "
    )
    * 4,
}


@contextmanager
def real_router(root, model, main_model, lid_site, sha, output, report):
    """Reuse the already accepted D1 worker, including its real forward counters."""
    import psutil

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = secrets.token_hex(32)
    env = os.environ.copy()
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONUNBUFFERED="1",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        REPRE_GUARD_LOCAL_FILES_ONLY="true",
        REPRE_GUARD_DEVICE="cuda",
        REPRE_GUARD_SERVICE_TOKEN=token,
        REPRE_GUARD_MODEL_PATH=str(main_model),
        REPRE_GUARD_EVIDENCE_ENABLED="true",
        REPRE_GUARD_EVIDENCE_MODEL_PATH=str(model),
        REPRE_GUARD_EVIDENCE_ARTIFACT_SHA256=sha,
        REPRE_GUARD_EVIDENCE_TIMEOUT_SECONDS="10",
        PYTHONPATH=str(lid_site) + os.pathsep + env.get("PYTHONPATH", ""),
    )
    info_path = output.with_name(output.stem + "-worker.json")
    log_path = output.with_suffix(".log")
    assert not info_path.exists()
    reserve = max(2 * 1024**3, psutil.virtual_memory().total * 0.1)
    stopped = threading.Event()
    with log_path.open("x", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(root / "smoke_evidence_runtime.py"),
                "--worker",
                "on",
                "--port",
                str(port),
                "--output",
                str(info_path),
            ],
            cwd=root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        def monitor():
            while not stopped.wait(0.1):
                if psutil.virtual_memory().available < reserve:
                    report["resource_abort"] = True
                    if process.poll() is None:
                        process.kill()
                    return

        watch = threading.Thread(target=monitor, daemon=True)
        watch.start()
        try:
            started = time.monotonic()
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                headers={"X-RepreGuard-Token": token},
                trust_env=False,
            ) as client:
                while time.monotonic() - started < 180:
                    assert process.poll() is None, (
                        "Temporary RepreGuard exited during startup"
                    )
                    try:
                        if client.get("/health", timeout=0.5).status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail("Temporary RepreGuard startup timed out")
            info = json.loads(info_path.read_text(encoding="utf-8"))
            assert info["router_status"] == "ready" and info["router_sha"] == sha
            assert (
                info["main_device"].startswith("cuda")
                and info["router_device"] == "cpu"
            )
            report["startup_seconds"] = time.monotonic() - started
            yield f"http://127.0.0.1:{port}/detect", token
        finally:
            if process.poll() is None:
                process.stdin.close()
                try:
                    process.wait(timeout=40)
                except subprocess.TimeoutExpired:
                    report["forced_stop"] = True
                    process.kill()
                    process.wait(timeout=5)
            stopped.set()
            watch.join(timeout=1)
            report["exit_code"] = process.poll()
            if info_path.exists():
                report["worker"] = json.loads(info_path.read_text(encoding="utf-8"))
    assert report["exit_code"] == 0 and not report.get("forced_stop")
    assert not report.get("resource_abort")


def test_real_models_snapshot_business_chain(tmp_path, monkeypatch):
    def path(name):
        value = Path(os.environ[name]).resolve()
        assert value.exists(), f"Missing explicit local input: {name}"
        return value

    root = path("EVIDENCE_REAL_REPREGUARD_ROOT")
    model = path("EVIDENCE_REAL_ROUTER_MODEL_PATH")
    main_model = path("EVIDENCE_REAL_MAIN_MODEL_PATH")
    lid_site = path("EVIDENCE_REAL_LID_SITE")
    bundle = path("EVIDENCE_TEST_BUNDLE_PATH")
    output = Path(os.environ["EVIDENCE_REAL_REPORT_PATH"]).resolve()
    assert output.parent.is_dir() and not output.exists()
    engine = EvidenceEngine(
        mode="serve", bundle_path=str(bundle), bundle_sha256=REAL_BUNDLE_SHA
    )
    assert engine.status == "ready"
    report = {
        "passed": False,
        "deployment_verified": False,
        "database": "isolated SQLite; no PostgreSQL or existing records",
        "bundle_sha256": REAL_BUNDLE_SHA,
        "router_sha256": engine.router_artifact_sha256,
        "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": [],
    }
    database = create_engine(
        URL.create("sqlite+pysqlite", database=str(tmp_path / "real-runtime.db"))
    )
    Base.metadata.create_all(database)

    def isolated_db():
        with Session(database) as session:
            yield session

    assert not app.dependency_overrides
    app.dependency_overrides[get_db] = isolated_db
    settings = get_settings()
    for name, value in {
        "detect_evidence_bundle_path": str(bundle),
        "detect_evidence_bundle_sha256": REAL_BUNDLE_SHA,
        "detect_evidence_timeout_seconds": "12",
        "detect_tokenizer_model": str(main_model),
        "detect_request_timeout": 120,
        "detect_service_timeout": 60,
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    original_detect, original_route = (
        repre_guard_client.detect,
        repre_guard_client.route_evidence,
    )
    counts = {"main": 0, "router": 0}

    async def observed_detect(text):
        counts["main"] += 1
        return await original_detect(text)

    async def observed_route(text):
        counts["router"] += 1
        return await original_route(text)

    monkeypatch.setattr(repre_guard_client, "detect", observed_detect)
    monkeypatch.setattr(repre_guard_client, "route_evidence", observed_route)
    try:
        with real_router(
            root,
            model,
            main_model,
            lid_site,
            engine.router_artifact_sha256,
            output,
            report,
        ) as (url, token):
            monkeypatch.setattr(repre_guard_client, "detect_url", url)
            monkeypatch.setattr(repre_guard_client, "service_token", token)
            baseline = {}
            used = records = 0
            user_token = None
            for mode in ("off", "shadow", "serve"):
                monkeypatch.setattr(settings, "detect_evidence_mode", mode)
                with TestClient(app) as client:
                    if user_token is None:
                        credentials = {
                            "email": f"evidence-{uuid4().hex}@example.com",
                            "password": secrets.token_urlsafe(24),
                        }
                        registration = client.post(
                            "/api/v1/auth/register", json=credentials
                        )
                        assert registration.status_code == 201
                        login = client.post(
                            "/api/v1/auth/login",
                            json={
                                "identifier": credentials["email"],
                                "password": credentials["password"],
                            },
                        )
                        assert login.status_code == 200
                        user_token = login.json()["accessToken"]
                    client.headers["Authorization"] = f"Bearer {user_token}"
                    for name, text in CASES.items():
                        if mode == "shadow" and name != "en":
                            continue
                        headers = {"Idempotency-Key": str(uuid4())}
                        before = counts.copy()
                        started = time.monotonic()
                        first = client.post(
                            "/api/v1/detect", json={"text": text}, headers=headers
                        )
                        assert first.status_code == 200
                        data = first.json()
                        elapsed = time.monotonic() - started
                        assert counts["router"] - before["router"] == (
                            0 if mode == "off" else 1
                        )
                        if name == "en":
                            assert counts["main"] - before["main"] > 1
                        primary = {
                            key: data[key]
                            for key in ("score", "rawScore", "threshold", "label")
                        }
                        if mode == "off":
                            baseline[name] = primary
                        else:
                            assert primary["label"] == baseline[name]["label"]
                            assert primary["threshold"] == baseline[name]["threshold"]
                            for key in ("score", "rawScore"):
                                assert abs(primary[key] - baseline[name][key]) <= 1e-6
                        with Session(database) as session:
                            saved = session.get(Detection, data["detectionId"])
                            snapshot = saved.meta_json.get("evidence")
                            if mode == "off":
                                assert (
                                    snapshot is None
                                    and "artifactVersion" not in saved.meta_json
                                )
                            else:
                                assert (
                                    saved.meta_json["artifactVersion"]
                                    == snapshot["artifactVersion"]
                                    == REAL_BUNDLE_SHA
                                )
                                assert snapshot["status"] == (
                                    "unsupported"
                                    if name == "unsupported"
                                    else "partial"
                                )
                                if name != "unsupported":
                                    assert snapshot["route"]["language"] == name
                                    assert snapshot["quality"]["coverage"] > 0
                        assert data.get("evidence") == (
                            snapshot if mode == "serve" else None
                        )
                        after = counts.copy()
                        replay = client.post(
                            "/api/v1/detect", json={"text": text}, headers=headers
                        )
                        assert replay.status_code == 200 and replay.json() == data
                        for history_path in (
                            "/api/v1/history",
                            f"/api/v1/history/{data['detectionId']}",
                        ):
                            history = client.get(history_path)
                            assert history.status_code == 200
                            history_data = history.json()
                            item = (
                                next(
                                    item
                                    for item in history_data["items"]
                                    if item["id"] == data["detectionId"]
                                )
                                if history_path.endswith("history")
                                else history_data
                            )
                            assert item.get("evidence") == data.get("evidence")
                        assert counts == after
                        used += len(text)
                        records += 1
                        with Session(database) as session:
                            assert (
                                session.scalar(select(func.count(Detection.id)))
                                == records
                            )
                            assert session.scalar(select(QuotaUsage.used)) == used
                        report["cases"].append(
                            {
                                "mode": mode,
                                "case": name,
                                "chars": len(text),
                                "seconds": elapsed,
                                "primary": primary,
                                "evidence_status": snapshot["status"]
                                if snapshot
                                else None,
                                "calls": {
                                    key: after[key] - before[key] for key in counts
                                },
                                "replay_and_history_equal": True,
                            }
                        )
        worker = report["worker"]
        assert worker["calls"] == worker["completions"] == counts["router"] == 4
        assert worker["forwards"] == 3
        assert (
            worker["final_admitted"]
            == worker["final_active"]
            == worker["final_workers"]
            == 0
        )
        report["passed"] = True
    finally:
        app.dependency_overrides.clear()
        database.dispose()
        output.write_text(
            json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
            encoding="utf-8",
        )
