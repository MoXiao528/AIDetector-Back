"""Bounded localhost-only preflight/detection interference experiment.

Run inside the API container with PYTHONPATH=/app. Temporary ordinary users are
owned by this run, JWTs stay in memory, and cleanup targets only their exact IDs.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import secrets
import signal
import statistics
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


DETECT_TEXT = (
    "This is a deployment check for the text detection service. The request goes through the application API, "
    "reaches the model server, and stores the returned result. We use a complete paragraph so the example "
    "meets the minimum input length and exercises the same path as a normal user submission. "
) * 5
DETECT_TEXT = DETECT_TEXT.strip()
CHINESE_TEXT = (
    "本次实验比较实时长度预检与正常文本检测同时运行时的响应耗时。"
    "我们保持输入内容和并发数量一致，记录每次请求的开始时间、结束时间和状态。"
    "这些数据只用于观察本机服务的资源竞争，不代表线上环境的最大承载能力。"
)
USER_COUNT = 32
PREFLIGHT_USER_COUNT = 8
CHAR_BUDGET = 30_000
PENDING_CAP = 512
STALL_SECONDS = 20


@dataclass(frozen=True)
class Case:
    name: str
    rate: int
    chars: int


CASES = (
    Case("baseline", 0, 0),
    Case("preflight_20rps_2000chars", 20, 2000),
    Case("preflight_100rps_2000chars", 100, 2000),
    Case("preflight_100rps_20000chars", 100, 20000),
)
ORDERS = ((0, 1, 3, 2), (2, 3, 1, 0), (1, 0, 2, 3))


def local_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Only an explicit localhost HTTP origin is allowed")
    return value.rstrip("/")


def chinese_sample(size: int) -> str:
    return (CHINESE_TEXT * math.ceil(size / len(CHINESE_TEXT)))[:size]


def body_for(text: str, *, detect: bool = False) -> bytes:
    payload = {"text": text}
    if detect:
        payload["functions"] = ["scan"]
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def latency_summary(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    result = {"count": len(values), "mean_ms": round(statistics.fmean(values), 3)}
    for name, percentile in (("p50_ms", 0.5), ("p95_ms", 0.95), ("p99_ms", 0.99)):
        result[name] = round(ordered[math.ceil(len(ordered) * percentile) - 1], 3)
    result.update(min_ms=round(ordered[0], 3), max_ms=round(ordered[-1], 3))
    return result


def request_summary(records: list[dict]) -> dict:
    successes = [record for record in records if record["ok"]]
    failures = [record for record in records if not record["ok"]]
    return {
        "total": len(records),
        "successes": len(successes),
        "failures": len(failures),
        "success_latency": latency_summary([r["latency_ms"] for r in successes]),
        "failure_latency": latency_summary([r["latency_ms"] for r in failures]),
        "status_counts": dict(Counter(str(r["status"]) for r in records)),
        "error_counts": dict(Counter(r["error_code"] or "unknown" for r in failures)),
    }


@dataclass
class Actor:
    alias: str
    user_id: int
    token: str = field(repr=False)
    sent_chars: int = 0
    busy: bool = False


class ActorPool:
    def __init__(self, actors: list[Actor]):
        self.actors = actors
        self.cursor = 0

    def acquire(self, chars: int) -> Actor:
        # Called on one asyncio loop without an intervening await.
        for offset in range(len(self.actors)):
            index = (self.cursor + offset) % len(self.actors)
            actor = self.actors[index]
            if not actor.busy and actor.sent_chars + chars <= CHAR_BUDGET:
                actor.busy = True
                actor.sent_chars += chars  # Includes failures, deliberately conservative.
                self.cursor = (index + 1) % len(self.actors)
                return actor
        raise RuntimeError("No idle test actor with sufficient reserved character budget")


def provision(run_id: str, owned_ids: list[int]) -> list[Actor]:
    import app.db.base  # noqa: F401
    from app.core.roles import UserRole
    from app.core.security import create_access_token, get_password_hash
    from app.db.session import SessionLocal
    from app.models.user import User

    password_hash = get_password_hash(secrets.token_urlsafe(40))
    with SessionLocal.begin() as db:
        users = [
            User(
                email=f"{run_id}-{index}@example.invalid",
                name=f"{run_id}-{index}",
                password_hash=password_hash,
                role=UserRole.INDIVIDUAL,
            )
            for index in range(USER_COUNT + PREFLIGHT_USER_COUNT)
        ]
        db.add_all(users)
        db.flush()
        # Keep exact IDs before committing, so a post-commit error can still clean up.
        owned_ids.extend(user.id for user in users)
    return [
        Actor(
            alias=(f"detect-{i + 1:02}" if i < USER_COUNT else f"preflight-{i - USER_COUNT + 1:02}"),
            user_id=user_id,
            token=create_access_token(
                subject=str(user_id),
                expires_delta=timedelta(minutes=60),
                extra_claims={"sub_type": "user"},
            ),
        )
        for i, user_id in enumerate(owned_ids)
    ]


def metadata() -> dict:
    from app.core.config import get_settings
    from app.db.session import engine
    from app.services.token_chunker import build_token_aware_segments, count_tokens

    settings = get_settings()
    model = settings.detect_tokenizer_model
    segments = build_token_aware_segments([DETECT_TEXT], short_visible_chars=1, tokenizer_model=model)
    count = count_tokens(DETECT_TEXT, tokenizer_model=model)
    if count != 317 or len(DETECT_TEXT) != 1469 or len(segments) != 1:
        raise RuntimeError("Detection fixture must remain exactly 317 tokens, 1469 characters and one segment")
    return {
        "detection_text": {"characters": len(DETECT_TEXT), "tokens": count, "segments": len(segments),
                           "sha256": hashlib.sha256(DETECT_TEXT.encode()).hexdigest()},
        "preflight_texts": {
            str(size): {"characters": size, "tokens": count_tokens(chinese_sample(size), tokenizer_model=model)}
            for size in (2000, 20_000)
        },
        "pool_size": engine.pool.size(), "pool_max_overflow": engine.pool._max_overflow,
        "pool_timeout_seconds": engine.pool.timeout(), "evidence_mode": settings.detect_evidence_mode,
        "tokenizer_model": model, "detect_service_timeout_seconds": settings.detect_service_timeout,
        "detect_request_timeout_seconds": settings.detect_request_timeout,
        "detect_segment_concurrency": settings.detect_segment_concurrency,
    }


async def request_once(client, path, actor, body, kind, origin, scheduled=None) -> dict:
    started = time.perf_counter()
    headers = {"Authorization": f"Bearer {actor.token}", "Content-Type": "application/json"}
    if kind == "detect":
        headers["Idempotency-Key"] = str(uuid4())
    result = {"kind": kind, "actor": actor.alias, "start_ms": round((started - origin) * 1000, 3),
              "scheduled_ms": round((scheduled - origin) * 1000, 3) if scheduled is not None else None,
              "schedule_lag_ms": round(max(0, started - scheduled) * 1000, 3) if scheduled is not None else None,
              "status": None, "ok": False, "error_code": None}
    try:
        response = await client.post(path, headers=headers, content=body)
        finished = time.perf_counter()
        result.update(status=response.status_code, bytes=len(response.content))
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        result["ok"] = response.status_code == 200
        if not result["ok"]:
            detail = payload.get("detail", {}) if isinstance(payload, dict) else {}
            code = payload.get("code") if isinstance(payload, dict) else None
            result["error_code"] = str(code or (detail.get("code") if isinstance(detail, dict) else None) or "HTTP_ERROR")
        elif kind == "preflight":
            result["token_count"] = payload.get("tokenCount")
        elif kind == "detect":
            result["detection_id"] = payload.get("detection_id", payload.get("detectionId"))
            if not result["detection_id"]:
                result.update(ok=False, error_code="INVALID_DETECTION_RESPONSE")
    except Exception as exc:
        finished = time.perf_counter()
        result["error_code"] = type(exc).__name__
    result.update(end_ms=round((finished - origin) * 1000, 3), latency_ms=round((finished - started) * 1000, 3))
    return result


def client_for(base_url: str, kind: str) -> httpx.AsyncClient:
    limit = 8 if kind == "detect" else PENDING_CAP
    return httpx.AsyncClient(
        base_url=base_url, trust_env=False, follow_redirects=False,
        timeout=httpx.Timeout(150 if kind == "detect" else 60, connect=5, pool=5),
        limits=httpx.Limits(max_connections=limit, max_keepalive_connections=limit),
    )


async def run_case(base_url, pool, preview_actors, case, concurrency, samples, round_index, origin,
                   abort, report_cells):
    records = []
    pending = set()
    stop = asyncio.Event()
    generator = {"target_rps": case.rate, "max_pending": 0, "stop_reason": "completed"}
    result = {
        "round": round_index + 1, "concurrency": concurrency, "case": case.name,
        "preflight_characters": case.chars, "generator": generator, "requests": records,
        "complete": False,
    }
    # Publish the mutable cell before sending traffic; an interrupted cell must
    # retain already completed requests instead of disappearing from the report.
    report_cells.append(result)
    preview_body = body_for(chinese_sample(case.chars)) if case.rate else b""
    detect_body = body_for(DETECT_TEXT, detect=True)
    async with client_for(base_url, "detect") as detect_client, client_for(base_url, "preflight") as preview_client:
        async def send_preview(index, scheduled):
            records.append(await request_once(preview_client, "/api/v1/detect/preflight",
                                              preview_actors[index % len(preview_actors)], preview_body,
                                              "preflight", origin, scheduled))

        async def produce():
            started = time.perf_counter()
            generator["started_ms"] = round((started - origin) * 1000, 3)
            index = 0
            while case.rate and not stop.is_set() and not abort.is_set():
                scheduled = started + index / case.rate
                try:
                    await asyncio.wait_for(stop.wait(), timeout=max(0.000001, scheduled - time.perf_counter()))
                    break
                except asyncio.TimeoutError:
                    pass
                if stop.is_set():
                    break
                if len(pending) >= PENDING_CAP:
                    generator["stop_reason"] = "pending_cap"
                    abort.set()
                    break
                task = asyncio.create_task(send_preview(index, scheduled))
                pending.add(task)
                task.add_done_callback(pending.discard)
                generator["max_pending"] = max(generator["max_pending"], len(pending))
                index += 1
            ended = time.perf_counter()
            generator.update(ended_ms=round((ended - origin) * 1000, 3), sent=index,
                             achieved_rps=round(index / max(ended - started, 0.001), 3))

        next_request = 0
        active_detections = 0
        last_detection_completion = time.perf_counter()

        async def detect_worker():
            nonlocal next_request, active_detections, last_detection_completion
            while next_request < samples and not abort.is_set():
                sequence = next_request
                next_request += 1
                actor = pool.acquire(len(DETECT_TEXT))
                active_detections += 1
                try:
                    result = await request_once(detect_client, "/api/v1/detect", actor, detect_body, "detect", origin)
                    result["sequence"] = sequence
                    records.append(result)
                    last_detection_completion = time.perf_counter()
                finally:
                    active_detections -= 1
                    actor.busy = False

        async def watch_stall():
            while not stop.is_set():
                if abort.is_set():
                    if generator["stop_reason"] == "completed":
                        generator["stop_reason"] = "operator_abort"
                    stop.set()
                    return
                if active_detections and time.perf_counter() - last_detection_completion >= STALL_SECONDS:
                    generator["stop_reason"] = "no_detection_completion_for_20_seconds"
                    abort.set()
                    stop.set()
                    return
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.25)
                except asyncio.TimeoutError:
                    pass

        producer = asyncio.create_task(produce())
        watchdog = asyncio.create_task(watch_stall())
        try:
            await asyncio.sleep(1)  # Identical lead-in for baseline and mixed cells.
            measurement_start = time.perf_counter()
            last_detection_completion = measurement_start
            await asyncio.gather(*(detect_worker() for _ in range(concurrency)))
            measurement_end = time.perf_counter()
        finally:
            stop.set()
            await producer
            await watchdog
            if pending:
                await asyncio.gather(*pending)
    detects = [r for r in records if r["kind"] == "detect"]
    previews = [r for r in records if r["kind"] == "preflight"]
    generator["schedule_lag"] = latency_summary([r["schedule_lag_ms"] for r in previews])
    result.update({
        "measurement_start_ms": round((measurement_start - origin) * 1000, 3),
        "measurement_end_ms": round((measurement_end - origin) * 1000, 3),
        "detect": request_summary(detects), "preflight": request_summary(previews),
        "complete": len(detects) == samples, "aborted": abort.is_set(),
    })
    records.sort(key=lambda r: r["start_ms"])
    return result


async def experiment(base_url, actors, report, rounds, samples):
    import anyio

    report["configuration"]["anyio_default_thread_tokens"] = anyio.to_thread.current_default_thread_limiter().total_tokens
    abort = asyncio.Event()
    # Container execution uses Linux: stop producers/new detections on one signal,
    # drain existing HTTP requests, preserve the partial cell, then clean up.
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, abort.set)
    pool = ActorPool(actors[:USER_COUNT])
    previews = actors[USER_COUNT:]
    origin = time.perf_counter()
    warmup = []
    async with client_for(base_url, "detect") as client:
        for actor in actors:
            response = await client.get("/api/v1/quota", headers={"Authorization": f"Bearer {actor.token}"})
            if response.status_code != 200 or response.json().get("remaining") != CHAR_BUDGET:
                raise RuntimeError("Temporary actor did not start with exactly its ordinary-user quota")
        for size in (2000, 20_000):
            for actor in previews[:2]:
                warmup.append(await request_once(client, "/api/v1/detect/preflight", actor,
                                                 body_for(chinese_sample(size)), "preflight", origin))
        for _ in range(4):
            actor = pool.acquire(len(DETECT_TEXT))
            try:
                warmup.append(await request_once(client, "/api/v1/detect", actor,
                                                 body_for(DETECT_TEXT, detect=True), "detect", origin))
            finally:
                actor.busy = False
    report["warmup"] = warmup
    if not all(record["ok"] for record in warmup):
        raise RuntimeError("Warmup failed; measured experiment was not started")
    print(json.dumps({"event": "warmup", "results": request_summary(warmup)}), flush=True)
    for round_index in range(rounds):
        for concurrency in ((2, 4) if round_index % 2 == 0 else (4, 2)):
            for case_index in ORDERS[round_index]:
                result = await run_case(base_url, pool, previews, CASES[case_index], concurrency,
                                        samples, round_index, origin, abort, report["cells"])
                print(json.dumps({"event": "cell", **{key: result[key] for key in
                                 ("round", "concurrency", "case", "detect", "preflight", "generator")}}), flush=True)
                if abort.is_set():
                    report["aborted"] = result["generator"]["stop_reason"]
                    report["actor_sent_characters"] = {actor.alias: actor.sent_chars for actor in actors}
                    return
        print(json.dumps({"event": "round_complete", "round": round_index + 1}), flush=True)
    report["actor_sent_characters"] = {actor.alias: actor.sent_chars for actor in actors}


def cleanup(run_id: str, owned_ids: list[int]) -> dict:
    from sqlalchemy import delete, func, select
    from app.db.session import SessionLocal
    from app.models.detection import Detection
    from app.models.detection_request import DetectionRequest
    from app.models.quota_usage import QuotaUsage
    from app.models.user import User

    actor_ids = [str(user_id) for user_id in owned_ids]
    tables = (DetectionRequest, Detection, QuotaUsage)
    filters = {model: (model.actor_type == "user", model.actor_id.in_(actor_ids)) for model in tables}
    # A timeout can leave an indeterminate reservation even after a client finishes.
    # Refuse cleanup while any request is still processing, including expired leases.
    deadline = time.monotonic() + 180
    while True:
        with SessionLocal() as db:
            active = db.scalar(select(func.count()).select_from(DetectionRequest).where(
                *filters[DetectionRequest], DetectionRequest.status == "processing"))
        if not active:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Cleanup withheld: test actors still have processing request records")
        time.sleep(2)
    with SessionLocal.begin() as db:
        actual = db.scalars(select(User).where(User.id.in_(owned_ids))).all()
        if any(not user.name.startswith(run_id + "-") or not user.email.startswith(run_id + "-") for user in actual):
            raise RuntimeError("Cleanup ownership mismatch")
        before = {model.__tablename__: db.scalar(select(func.count()).select_from(model).where(*filters[model]))
                  for model in tables}
        before["users"] = len(actual)
        for model in tables:
            db.execute(delete(model).where(*filters[model]))
        db.execute(delete(User).where(User.id.in_(owned_ids)))
    with SessionLocal() as db:
        after = {model.__tablename__: db.scalar(select(func.count()).select_from(model).where(*filters[model]))
                 for model in tables}
        after["users"] = db.scalar(select(func.count()).select_from(User).where(User.id.in_(owned_ids)))
    return {"before": before, "after": after, "verified_empty": not any(after.values())}


def aggregate(report):
    summaries = []
    for concurrency in (2, 4):
        baseline = None
        for case in CASES:
            cells = [c for c in report["cells"] if c["concurrency"] == concurrency and c["case"] == case.name]
            detects = [r for cell in cells for r in cell["requests"] if r["kind"] == "detect"]
            previews = [r for cell in cells for r in cell["requests"] if r["kind"] == "preflight"]
            entry = {"concurrency": concurrency, "case": case.name, "detect": request_summary(detects),
                     "preflight": request_summary(previews), "completed_cells": len(cells)}
            current = entry["detect"]["success_latency"]
            if case.rate == 0:
                baseline = current
            elif baseline and baseline["count"] and current["count"]:
                entry["baseline_relative"] = {key: round(current[key] / baseline[key], 4)
                                              for key in ("p50_ms", "p95_ms")}
            summaries.append(entry)
    return summaries


def self_check():
    global client_for, STALL_SECONDS

    assert len(DETECT_TEXT) == 1469
    assert all(len(chinese_sample(size)) == size for size in (2000, 20_000))
    assert latency_summary(list(range(1, 61)))["p95_ms"] == 57
    for invalid in ("https://example.com", "http://example.com", "http://localhost.evil", "http://x@localhost"):
        try:
            local_url(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("Nonlocal origin accepted")
    assert local_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    pool = ActorPool([Actor("a", 1, "unused"), Actor("b", 2, "unused")])
    first, second = pool.acquire(1469), pool.acquire(1469)
    assert first is not second
    try:
        pool.acquire(1469)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Busy actor reused")
    original_client_for, original_stall = client_for, STALL_SECONDS

    async def check_run_case():
        async def response(request):
            await asyncio.sleep(0.35)
            return httpx.Response(200, json={"detection_id": 1}, request=request)

        def mock_client(base_url, kind):
            return httpx.AsyncClient(base_url=base_url, transport=httpx.MockTransport(response))

        globals()["client_for"] = mock_client
        globals()["STALL_SECONDS"] = 0.01
        actors = [Actor("a", 1, "unused"), Actor("b", 2, "unused")]
        cells = []
        result = await run_case("http://127.0.0.1", ActorPool(actors), [], CASES[0], 2, 4,
                                0, time.perf_counter(), asyncio.Event(), cells)
        assert result is cells[0]
        assert result["aborted"] and not result["complete"]
        assert result["detect"]["total"] == 2
        assert len(result["requests"]) == 2
        assert all(not actor.busy for actor in actors)

    try:
        asyncio.run(check_run_case())
    finally:
        client_for, STALL_SECONDS = original_client_for, original_stall
    print("Pure checks passed", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", default="/tmp/preflight-interference-20261111.json")
    parser.add_argument("--rounds", type=int, choices=range(1, 4), default=3)
    parser.add_argument("--samples", type=int, choices=range(4, 21), default=20)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    self_check()
    if args.self_check:
        return
    if Path(args.output).exists():
        raise SystemExit("Refusing to overwrite an existing experiment report; choose a new --output path")
    base_url = local_url(args.base_url)
    owned_ids = []
    run_id = f"preflight-perf-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(3)}"
    report = {"run_id": run_id, "started_at": datetime.now(timezone.utc).isoformat(), "base_url": base_url,
              "execution_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "scope": "Local single-host experiment through real authentication; not production/proxy capacity",
              "plan": {"rounds": args.rounds, "samples_per_cell": args.samples, "concurrencies": [2, 4],
                       "ordinary_users": USER_COUNT, "preflight_users": PREFLIGHT_USER_COUNT,
                       "preflight_pending_cap": PENDING_CAP, "lead_in_seconds": 1,
                       "abort_after_no_detection_completion_seconds": STALL_SECONDS,
                       "percentile_method": "nearest rank; success-only; warmup excluded"},
              "configuration": {}, "cells": []}
    failed = False
    try:
        report["configuration"] = metadata()
        print(json.dumps({"event": "configuration", **report["configuration"]}), flush=True)
        actors = provision(run_id, owned_ids)
        report["owned_user_ids"] = list(owned_ids)
        asyncio.run(experiment(base_url, actors, report, args.rounds, args.samples))
        failed = bool(report.get("aborted"))
    except BaseException as exc:
        failed = True
        report["fatal_error"] = {"type": type(exc).__name__, "message": str(exc)}
        print(json.dumps({"event": "fatal_error", **report["fatal_error"]}), flush=True)
    finally:
        try:
            report["cleanup"] = cleanup(run_id, owned_ids) if owned_ids else {"verified_empty": True, "created": 0}
        except Exception as exc:
            failed = True
            report["cleanup"] = {"verified_empty": False, "error": type(exc).__name__, "message": str(exc)}
        report["summary"] = aggregate(report)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"event": "finished", "output": str(output), "cleanup": report["cleanup"],
                          "summary": report["summary"]}), flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
