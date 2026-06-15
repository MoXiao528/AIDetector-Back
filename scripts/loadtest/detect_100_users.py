from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


DEFAULT_BASE_URL = "https://aiguard.isuperviz.net"
DEFAULT_PATH = "/api/v1/detect"
DEFAULT_USERS = 100
DEFAULT_ROUNDS = 1
DEFAULT_TARGET_CHARS = 500
DEFAULT_TIMEOUT = 90.0
DEFAULT_MIN_MEMBER_TOKENS = 3
DEFAULT_MIN_GUEST_TOKENS = 13
RESULTS_DIR = Path(__file__).resolve().parent / "results"

NORMAL_SENTENCES = [
    "今天的工作主要围绕文档整理、接口验证和用户反馈跟进展开。",
    "团队先确认了当前版本的核心流程，然后逐项记录可能影响体验的问题。",
    "在测试过程中，我们保持输入内容接近真实业务场景，避免使用极端样例干扰判断。",
    "每段文字都包含明确的上下文、普通的叙述结构，以及相对稳定的信息密度。",
    "这种材料适合用来观察服务在正常请求下的延迟、吞吐、错误率和额度扣减情况。",
    "如果系统响应出现波动，报告会保留状态码、耗时和失败样例，方便后续定位。",
    "本次压测不会主动创建账号，也不会循环冲击登录或游客发放接口。",
    "所有检测请求都会通过正式后端进入下游检测服务，因此可以覆盖真实链路。",
]


@dataclass(frozen=True)
class TokenInfo:
    value: str
    source: str
    actor_type: str


@dataclass(frozen=True)
class LoginCredential:
    identifier: str
    password: str
    source: str


@dataclass(frozen=True)
class QuotaInfo:
    token_source: str
    actor_type: str
    limit: int | None
    used_today: int | None
    remaining: int | None
    error: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a quota-aware 100-user load test against the detect API."
    )
    parser.add_argument("--base-url", default=os.getenv("LOADTEST_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--path", default=os.getenv("LOADTEST_DETECT_PATH", DEFAULT_PATH))
    parser.add_argument("--users", type=int, default=int(os.getenv("LOADTEST_USERS", DEFAULT_USERS)))
    parser.add_argument("--rounds", type=int, default=int(os.getenv("LOADTEST_ROUNDS", DEFAULT_ROUNDS)))
    parser.add_argument(
        "--chars",
        type=int,
        default=int(os.getenv("LOADTEST_TEXT_CHARS", DEFAULT_TARGET_CHARS)),
        help="Approximate characters per detection request.",
    )
    parser.add_argument(
        "--auth-mode",
        choices=["member", "guest"],
        default=os.getenv("LOADTEST_AUTH_MODE", "member"),
    )
    parser.add_argument("--timeout", type=float, default=float(os.getenv("LOADTEST_TIMEOUT", DEFAULT_TIMEOUT)))
    parser.add_argument(
        "--verify-tls",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("LOADTEST_VERIFY_TLS", "true").lower() not in {"0", "false", "no"},
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even when quota preflight cannot confirm enough remaining quota.",
    )
    parser.add_argument(
        "--auto-guest-tokens",
        type=int,
        default=int(os.getenv("LOADTEST_AUTO_GUEST_TOKENS", "0")),
        help="Create this many guest tokens through /api/v1/auth/guest before the run.",
    )
    return parser.parse_args()


def require_positive(name: str, value: int) -> None:
    if value <= 0:
        raise SystemExit(f"{name} must be > 0")


def normalize_token(raw: str) -> str:
    token = raw.strip().strip('"').strip("'").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def min_tokens_for(auth_mode: str) -> int:
    return DEFAULT_MIN_MEMBER_TOKENS if auth_mode == "member" else DEFAULT_MIN_GUEST_TOKENS


def collect_env_tokens(auth_mode: str) -> list[TokenInfo]:
    if auth_mode == "member":
        prefixes = ["LOADTEST_MEMBER_TOKEN"]
    else:
        prefixes = ["LOADTEST_GUEST_TOKEN"]

    tokens: list[TokenInfo] = []
    seen: set[str] = set()

    for prefix in prefixes:
        raw = os.getenv(prefix)
        token = normalize_token(raw) if raw else ""
        if token and token not in seen:
            tokens.append(TokenInfo(value=token, source=prefix, actor_type=auth_mode))
            seen.add(token)

        for index in range(1, 101):
            key = f"{prefix}_{index}"
            raw = os.getenv(key)
            token = normalize_token(raw) if raw else ""
            if token and token not in seen:
                tokens.append(TokenInfo(value=token, source=key, actor_type=auth_mode))
                seen.add(token)

    return tokens


def enforce_min_tokens(tokens: list[TokenInfo], auth_mode: str) -> None:
    min_tokens = min_tokens_for(auth_mode)
    if len(tokens) < min_tokens:
        prefix = "LOADTEST_MEMBER_TOKEN" if auth_mode == "member" else "LOADTEST_GUEST_TOKEN"
        raise SystemExit(
            f"{auth_mode} mode needs at least {min_tokens} tokens for a safe 100x500-char run. "
            f"Set {prefix}_1 ... {prefix}_{min_tokens}."
        )


def collect_login_credentials() -> list[LoginCredential]:
    credentials: list[LoginCredential] = []

    raw_identifier = os.getenv("LOADTEST_LOGIN_IDENTIFIER")
    raw_password = os.getenv("LOADTEST_LOGIN_PASSWORD")
    if raw_identifier or raw_password:
        if not raw_identifier or not raw_password:
            raise SystemExit("set both LOADTEST_LOGIN_IDENTIFIER and LOADTEST_LOGIN_PASSWORD")
        credentials.append(
            LoginCredential(
                identifier=raw_identifier.strip(),
                password=raw_password,
                source="LOADTEST_LOGIN_IDENTIFIER",
            )
        )

    for index in range(1, 101):
        identifier_key = f"LOADTEST_LOGIN_IDENTIFIER_{index}"
        password_key = f"LOADTEST_LOGIN_PASSWORD_{index}"
        raw_identifier = os.getenv(identifier_key)
        raw_password = os.getenv(password_key)
        if raw_identifier or raw_password:
            if not raw_identifier or not raw_password:
                raise SystemExit(f"set both {identifier_key} and {password_key}")
            credentials.append(
                LoginCredential(
                    identifier=raw_identifier.strip(),
                    password=raw_password,
                    source=identifier_key,
                )
            )

    return credentials


def dedupe_tokens(tokens: list[TokenInfo]) -> list[TokenInfo]:
    deduped: list[TokenInfo] = []
    seen: set[str] = set()
    for token in tokens:
        if token.value in seen:
            continue
        deduped.append(token)
        seen.add(token.value)
    return deduped


def build_text(target_chars: int, request_id: int) -> str:
    if target_chars < 200:
        raise SystemExit("detect API requires at least 200 visible characters; keep --chars >= 200")

    parts = [f"压测样本编号 {request_id + 1}。"]
    cursor = request_id % len(NORMAL_SENTENCES)
    while len("".join(parts)) < target_chars:
        parts.append(NORMAL_SENTENCES[cursor % len(NORMAL_SENTENCES)])
        cursor += 1

    return "".join(parts)[:target_chars]


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return round(values[0], 2)
    ordered = sorted(values)
    index = math.ceil((p / 100) * len(ordered)) - 1
    return round(ordered[max(0, min(index, len(ordered) - 1))], 2)


def summarize_latencies(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "avg": 0.0, "p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    return {
        "min": round(min(values), 2),
        "avg": round(statistics.fmean(values), 2),
        "p50": percentile(values, 50),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": round(max(values), 2),
    }


async def fetch_quota(
    client: httpx.AsyncClient,
    base_url: str,
    token: TokenInfo,
) -> QuotaInfo:
    url = f"{base_url.rstrip('/')}/api/v1/quota"
    try:
        response = await client.get(url, headers={"Authorization": f"Bearer {token.value}"})
        if response.status_code != 200:
            return QuotaInfo(
                token_source=token.source,
                actor_type=token.actor_type,
                limit=None,
                used_today=None,
                remaining=None,
                error=f"quota returned HTTP {response.status_code}: {response.text[:200]}",
            )
        payload = response.json()
        return QuotaInfo(
            token_source=token.source,
            actor_type=str(payload.get("actor_type") or token.actor_type),
            limit=int(payload["limit"]) if payload.get("limit") is not None else None,
            used_today=int(payload["used_today"]) if payload.get("used_today") is not None else None,
            remaining=int(payload["remaining"]) if payload.get("remaining") is not None else None,
            error=None,
        )
    except Exception as exc:  # noqa: BLE001
        return QuotaInfo(
            token_source=token.source,
            actor_type=token.actor_type,
            limit=None,
            used_today=None,
            remaining=None,
            error=f"{type(exc).__name__}: {exc}",
        )


async def login_member_tokens(
    *,
    base_url: str,
    credentials: list[LoginCredential],
    timeout: float,
    verify_tls: bool,
) -> list[TokenInfo]:
    if not credentials:
        return []

    url = f"{base_url.rstrip('/')}/api/v1/auth/login"
    tokens: list[TokenInfo] = []
    async with httpx.AsyncClient(timeout=timeout, verify=verify_tls, follow_redirects=False) as client:
        for credential in credentials:
            response = await client.post(
                url,
                json={"identifier": credential.identifier, "password": credential.password},
                headers={"Accept": "application/json"},
            )
            if response.status_code != 200:
                raise SystemExit(
                    f"login failed for {credential.source}: HTTP {response.status_code}: {response.text[:200]}"
                )
            payload = response.json()
            token = normalize_token(str(payload.get("access_token") or ""))
            if not token:
                raise SystemExit(f"login response missing access_token for {credential.source}")
            tokens.append(TokenInfo(value=token, source=credential.source, actor_type="member"))

    return tokens


async def create_guest_tokens(
    *,
    base_url: str,
    count: int,
    timeout: float,
    verify_tls: bool,
) -> list[TokenInfo]:
    if count <= 0:
        return []

    url = f"{base_url.rstrip('/')}/api/v1/auth/guest"
    tokens: list[TokenInfo] = []
    async with httpx.AsyncClient(timeout=timeout, verify=verify_tls, follow_redirects=False) as client:
        for index in range(count):
            response = await client.post(url, json={}, headers={"Accept": "application/json"})
            if response.status_code != 200:
                raise SystemExit(f"guest token create failed at #{index + 1}: HTTP {response.status_code}: {response.text[:200]}")
            payload = response.json()
            token = normalize_token(str(payload.get("access_token") or ""))
            if not token:
                raise SystemExit(f"guest token response missing access_token at #{index + 1}")
            tokens.append(TokenInfo(value=token, source=f"AUTO_GUEST_TOKEN_{index + 1}", actor_type="guest"))

    return tokens


def planned_usage_by_token(tokens: list[TokenInfo], users: int, rounds: int, chars: int) -> dict[str, int]:
    planned = {token.source: 0 for token in tokens}
    total_requests = users * rounds
    for sequence in range(total_requests):
        token = tokens[sequence % len(tokens)]
        planned[token.source] += chars
    return planned


async def preflight_quota(
    *,
    base_url: str,
    tokens: list[TokenInfo],
    users: int,
    rounds: int,
    chars: int,
    timeout: float,
    verify_tls: bool,
    force: bool,
) -> tuple[list[QuotaInfo], dict[str, int]]:
    planned = planned_usage_by_token(tokens, users, rounds, chars)
    async with httpx.AsyncClient(timeout=timeout, verify=verify_tls, follow_redirects=False) as client:
        quotas = await asyncio.gather(*(fetch_quota(client, base_url, token) for token in tokens))

    quota_errors = [quota for quota in quotas if quota.error]
    insufficient = [
        quota
        for quota in quotas
        if quota.error is None
        and quota.remaining is not None
        and planned.get(quota.token_source, 0) > quota.remaining
    ]

    if quota_errors and not force:
        details = "; ".join(f"{quota.token_source}: {quota.error}" for quota in quota_errors[:5])
        raise SystemExit(f"quota preflight failed; use --force only if you accept the quota risk. {details}")

    if insufficient and not force:
        details = "; ".join(
            f"{quota.token_source}: planned={planned[quota.token_source]}, remaining={quota.remaining}"
            for quota in insufficient[:5]
        )
        raise SystemExit(f"not enough remaining quota; add more tokens or lower --users/--rounds/--chars. {details}")

    return quotas, planned


async def detect_once(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    path: str,
    token: TokenInfo,
    text: str,
    request_id: int,
    round_index: int,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        response = await client.post(
            url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token.value}",
            },
            json={"text": text, "functions": ["scan"]},
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        detail = None
        if response.status_code != 200:
            detail = response.text[:500]
        return {
            "request_id": request_id,
            "round": round_index + 1,
            "token_source": token.source,
            "status_code": response.status_code,
            "ok": response.status_code == 200,
            "elapsed_ms": elapsed_ms,
            "response_size_bytes": len(response.content),
            "failure_detail": detail,
            "error": None,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "request_id": request_id,
            "round": round_index + 1,
            "token_source": token.source,
            "status_code": None,
            "ok": False,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "response_size_bytes": 0,
            "failure_detail": None,
            "error": f"{type(exc).__name__}: {exc}",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }


async def run_loadtest(
    *,
    base_url: str,
    path: str,
    tokens: list[TokenInfo],
    users: int,
    rounds: int,
    chars: int,
    timeout: float,
    verify_tls: bool,
) -> dict[str, Any]:
    limits = httpx.Limits(max_connections=users, max_keepalive_connections=users)
    timeout_config = httpx.Timeout(timeout)
    all_results: list[dict[str, Any]] = []
    started = time.perf_counter()

    async with httpx.AsyncClient(
        timeout=timeout_config,
        verify=verify_tls,
        limits=limits,
        follow_redirects=False,
    ) as client:
        for round_index in range(rounds):
            batch = []
            for request_index in range(users):
                sequence = round_index * users + request_index
                token = tokens[sequence % len(tokens)]
                text = build_text(chars, sequence)
                batch.append(
                    detect_once(
                        client=client,
                        base_url=base_url,
                        path=path,
                        token=token,
                        text=text,
                        request_id=sequence + 1,
                        round_index=round_index,
                    )
                )
            all_results.extend(await asyncio.gather(*batch))

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    latencies = [float(item["elapsed_ms"]) for item in all_results]
    status_counts = Counter(
        str(item["status_code"]) if item["status_code"] is not None else "EXCEPTION"
        for item in all_results
    )
    failures = [item for item in all_results if not item["ok"]]

    return {
        "base_url": base_url,
        "path": path,
        "users": users,
        "rounds": rounds,
        "chars_per_request": chars,
        "total_requests": len(all_results),
        "duration_ms": duration_ms,
        "rps": round((len(all_results) / duration_ms) * 1000, 2) if duration_ms > 0 else 0.0,
        "status_counts": dict(status_counts),
        "success_count": len(all_results) - len(failures),
        "failure_count": len(failures),
        "latency": summarize_latencies(latencies),
        "failed_samples": failures[:10],
        "request_results": all_results,
    }


def write_report(report: dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"detect-100-users-{timestamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def async_main() -> None:
    args = parse_args()
    require_positive("--users", args.users)
    require_positive("--rounds", args.rounds)
    require_positive("--chars", args.chars)

    tokens = collect_env_tokens(args.auth_mode)
    if args.auth_mode == "member":
        login_credentials = collect_login_credentials()
        login_tokens = await login_member_tokens(
            base_url=args.base_url,
            credentials=login_credentials,
            timeout=args.timeout,
            verify_tls=args.verify_tls,
        )
        tokens = dedupe_tokens([*tokens, *login_tokens])
    elif args.auto_guest_tokens:
        guest_tokens = await create_guest_tokens(
            base_url=args.base_url,
            count=args.auto_guest_tokens,
            timeout=args.timeout,
            verify_tls=args.verify_tls,
        )
        tokens = dedupe_tokens([*tokens, *guest_tokens])

    enforce_min_tokens(tokens, args.auth_mode)
    total_chars = args.users * args.rounds * args.chars
    print(
        f"target={args.base_url.rstrip('/')}/{args.path.lstrip('/')} "
        f"users={args.users} rounds={args.rounds} requests={args.users * args.rounds} "
        f"chars≈{total_chars} auth={args.auth_mode} tokens={len(tokens)}"
    )

    quotas, planned = await preflight_quota(
        base_url=args.base_url,
        tokens=tokens,
        users=args.users,
        rounds=args.rounds,
        chars=args.chars,
        timeout=args.timeout,
        verify_tls=args.verify_tls,
        force=args.force,
    )
    print("quota plan:")
    for quota in quotas:
        print(
            f"  {quota.token_source}: planned={planned.get(quota.token_source, 0)} "
            f"remaining={quota.remaining} used_today={quota.used_today} limit={quota.limit} "
            f"{'error=' + quota.error if quota.error else ''}"
        )

    report = await run_loadtest(
        base_url=args.base_url,
        path=args.path,
        tokens=tokens,
        users=args.users,
        rounds=args.rounds,
        chars=args.chars,
        timeout=args.timeout,
        verify_tls=args.verify_tls,
    )
    report["quota_preflight"] = {
        "planned_by_token": planned,
        "quotas": [quota.__dict__ for quota in quotas],
    }
    report_path = write_report(report)

    print(
        f"done: success={report['success_count']} failure={report['failure_count']} "
        f"rps={report['rps']} latency={report['latency']} report={report_path}"
    )
    if report["failure_count"]:
        raise SystemExit(1)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
