#!/usr/bin/env python
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import httpx

DEFAULT_CONCURRENCY_LEVELS = [50, 100]
DEFAULT_ROUNDS = 3
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_USER_AGENT = "AIDetector-LoadTest/1.0"
OUTPUT_DIR_NAME = "results"
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class ConfigError(ValueError):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def expand_env_placeholders(value: Any, strict: bool = True) -> Any:
    if isinstance(value, dict):
        return {key: expand_env_placeholders(item, strict=strict) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_env_placeholders(item, strict=strict) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        env_name = match.group(1)
        if env_name not in os.environ:
            if not strict:
                return match.group(0)
            raise ConfigError(f"missing environment variable: {env_name}")
        return os.environ[env_name]

    return ENV_PATTERN.sub(replace, value)


def coerce_dataset_scalar(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: coerce_dataset_scalar(item) for key, item in value.items()}
    if isinstance(value, list):
        return [coerce_dataset_scalar(item) for item in value]
    if not isinstance(value, str):
        return value

    normalized = value.strip()
    if re.fullmatch(r"-?\d+", normalized):
        return int(normalized)
    if re.fullmatch(r"-?\d+\.\d+", normalized):
        return float(normalized)

    lowered = normalized.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    return value


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise ConfigError(f"config file not found: {config_path}")

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid json config: {exc}") from exc

    config = expand_env_placeholders(raw, strict=False)
    if not isinstance(config, dict):
        raise ConfigError("root config must be a JSON object")

    config.setdefault("defaults", {})
    config.setdefault("auth", {})
    config.setdefault("datasets", {})
    config.setdefault("scenarios", [])

    if not isinstance(config["defaults"], dict):
        raise ConfigError("defaults must be an object")
    if not isinstance(config["auth"], dict):
        raise ConfigError("auth must be an object")
    if not isinstance(config["datasets"], dict):
        raise ConfigError("datasets must be an object")
    if not isinstance(config["scenarios"], list):
        raise ConfigError("scenarios must be an array")

    config["datasets"] = coerce_dataset_scalar(config["datasets"])
    return config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run burst load tests against AIDetector APIs.")
    parser.add_argument("--config", required=True, help="Path to the JSON scenario config.")
    parser.add_argument("--output", help="Optional output JSON path.")
    parser.add_argument("--only", help="Comma-separated scenario names to run.")
    parser.add_argument(
        "--concurrency",
        help="Optional override, e.g. 50 or 50,100. Applies to every selected scenario.",
    )
    parser.add_argument("--rounds", type=int, help="Optional override for rounds per concurrency level.")
    parser.add_argument("--timeout", type=float, help="Optional override for timeout seconds.")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification.")
    parser.add_argument(
        "--max-failure-details",
        type=int,
        default=5,
        help="How many failure samples to keep per round. Default: 5.",
    )
    return parser.parse_args()


def parse_name_filter(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def parse_concurrency_override(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    levels = []
    for part in raw.split(","):
        text = part.strip()
        if not text:
            continue
        try:
            level = int(text)
        except ValueError as exc:
            raise ConfigError(f"invalid concurrency value: {text}") from exc
        if level <= 0:
            raise ConfigError(f"concurrency must be > 0: {text}")
        levels.append(level)
    if not levels:
        raise ConfigError("concurrency override is empty")
    return levels


def ensure_list_of_ints(value: Any, field_name: str, default: list[int]) -> list[int]:
    if value is None:
        return list(default)
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{field_name} must be a non-empty array")
    output: list[int] = []
    for item in value:
        if not isinstance(item, int) or item <= 0:
            raise ConfigError(f"{field_name} items must be positive integers")
        output.append(item)
    return output


def ensure_int(value: Any, field_name: str, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{field_name} must be a positive integer")
    return value


def ensure_float(value: Any, field_name: str, default: float) -> float:
    if value is None:
        return default
    if not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError(f"{field_name} must be a positive number")
    return float(value)


def percentile(sorted_values: list[float], ratio: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = math.ceil(ratio * len(sorted_values)) - 1
    index = max(0, min(index, len(sorted_values) - 1))
    return sorted_values[index]


def summarize_latencies(latencies_ms: list[float]) -> dict[str, float]:
    if not latencies_ms:
        return {
            "min_ms": 0.0,
            "max_ms": 0.0,
            "mean_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
        }

    ordered = sorted(latencies_ms)
    return {
        "min_ms": round(ordered[0], 2),
        "max_ms": round(ordered[-1], 2),
        "mean_ms": round(mean(ordered), 2),
        "p50_ms": round(percentile(ordered, 0.50), 2),
        "p95_ms": round(percentile(ordered, 0.95), 2),
        "p99_ms": round(percentile(ordered, 0.99), 2),
    }


def build_runtime_context(
    datasets: dict[str, Any],
    request_index: int,
    round_index: int,
    concurrency: int,
) -> dict[str, Any]:
    sequence = round_index * concurrency + request_index
    context: dict[str, Any] = {
        "request_index": request_index,
        "round_index": round_index,
        "concurrency": concurrency,
        "sequence": sequence,
    }

    for key, value in datasets.items():
        if isinstance(value, list):
            if not value:
                raise ConfigError(f"dataset '{key}' cannot be empty")
            context[key] = expand_env_placeholders(value[sequence % len(value)])
        else:
            context[key] = expand_env_placeholders(value)
    return context


def render_template(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: render_template(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render_template(item, context) for item in value]
    if not isinstance(value, str):
        return value

    full_match = PLACEHOLDER_PATTERN.fullmatch(value)
    if full_match:
        placeholder_name = full_match.group(1)
        if placeholder_name not in context:
            raise ConfigError(f"unknown placeholder: {placeholder_name}")
        return context[placeholder_name]

    def replace(match: re.Match[str]) -> str:
        placeholder_name = match.group(1)
        if placeholder_name not in context:
            raise ConfigError(f"unknown placeholder: {placeholder_name}")
        return str(context[placeholder_name])

    return PLACEHOLDER_PATTERN.sub(replace, value)


def resolve_auth_headers(
    auth_mode: str,
    auth_config: dict[str, Any],
    sequence: int,
) -> dict[str, str]:
    if auth_mode == "none":
        return {}

    if auth_mode == "member_bearer":
        token = expand_env_placeholders(auth_config.get("member_bearer_token"))
        if not token:
            raise ConfigError("missing auth.member_bearer_token")
        return {"Authorization": f"Bearer {token}"}

    if auth_mode == "member_bearer_pool":
        tokens = expand_env_placeholders(auth_config.get("member_bearer_tokens"))
        if not isinstance(tokens, list) or not tokens:
            raise ConfigError("auth.member_bearer_tokens must be a non-empty array")
        token = tokens[sequence % len(tokens)]
        if not token:
            raise ConfigError("member token pool contains an empty token")
        return {"Authorization": f"Bearer {token}"}

    if auth_mode == "admin_bearer":
        token = expand_env_placeholders(auth_config.get("admin_bearer_token"))
        if not token:
            raise ConfigError("missing auth.admin_bearer_token")
        return {"Authorization": f"Bearer {token}"}

    if auth_mode == "guest_bearer_pool":
        tokens = expand_env_placeholders(auth_config.get("guest_bearer_tokens"))
        if not isinstance(tokens, list) or not tokens:
            raise ConfigError("auth.guest_bearer_tokens must be a non-empty array")
        token = tokens[sequence % len(tokens)]
        if not token:
            raise ConfigError("guest token pool contains an empty token")
        return {"Authorization": f"Bearer {token}"}

    if auth_mode == "member_api_key":
        api_key = expand_env_placeholders(auth_config.get("member_api_key"))
        if not api_key:
            raise ConfigError("missing auth.member_api_key")
        return {"X-API-Key": str(api_key)}

    raise ConfigError(f"unsupported auth mode: {auth_mode}")


def load_file_bytes(path_text: str, cache: dict[str, bytes], config_dir: Path) -> bytes:
    raw_path = Path(path_text)
    file_path = raw_path if raw_path.is_absolute() else (config_dir / raw_path)
    file_path = file_path.resolve()
    cache_key = str(file_path)
    if cache_key not in cache:
        if not file_path.exists():
            raise ConfigError(f"file payload not found: {file_path}")
        cache[cache_key] = file_path.read_bytes()
    return cache[cache_key]


def build_request_kwargs(
    scenario: dict[str, Any],
    config_dir: Path,
    file_cache: dict[str, bytes],
    rendered_query: dict[str, Any] | None,
    rendered_json_body: Any,
    rendered_form_body: Any,
    rendered_raw_body: Any,
    rendered_files: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if rendered_query is not None:
        kwargs["params"] = rendered_query

    body_modes = sum(
        1
        for item in (rendered_json_body, rendered_form_body, rendered_raw_body, rendered_files)
        if item is not None
    )
    if body_modes > 1:
        raise ConfigError(
            f"scenario '{scenario['name']}' can only use one of json_body/form_body/raw_body/files"
        )

    if rendered_json_body is not None:
        kwargs["json"] = rendered_json_body
    elif rendered_form_body is not None:
        kwargs["data"] = rendered_form_body
    elif rendered_raw_body is not None:
        kwargs["content"] = rendered_raw_body
    elif rendered_files is not None:
        files_payload: list[tuple[str, tuple[str, bytes, str]]] = []
        for file_item in rendered_files:
            if not isinstance(file_item, dict):
                raise ConfigError(f"scenario '{scenario['name']}' files items must be objects")
            field = str(file_item.get("field") or "").strip()
            path_text = str(file_item.get("path") or "").strip()
            if not field or not path_text:
                raise ConfigError(f"scenario '{scenario['name']}' files require field and path")
            filename = str(file_item.get("filename") or Path(path_text).name)
            content_type = str(file_item.get("content_type") or "application/octet-stream")
            file_bytes = load_file_bytes(path_text=path_text, cache=file_cache, config_dir=config_dir)
            files_payload.append((field, (filename, file_bytes, content_type)))
        kwargs["files"] = files_payload

    return kwargs


async def run_single_request(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    scenario: dict[str, Any],
    defaults_headers: dict[str, str],
    auth_config: dict[str, Any],
    datasets: dict[str, Any],
    config_dir: Path,
    file_cache: dict[str, bytes],
    request_index: int,
    round_index: int,
    concurrency: int,
    max_failure_details: int,
) -> dict[str, Any]:
    context = build_runtime_context(
        datasets=datasets,
        request_index=request_index,
        round_index=round_index,
        concurrency=concurrency,
    )
    rendered_path = render_template(scenario["path"], context)
    rendered_query = render_template(scenario.get("query"), context) if "query" in scenario else None
    rendered_json_body = render_template(scenario.get("json_body"), context) if "json_body" in scenario else None
    rendered_form_body = render_template(scenario.get("form_body"), context) if "form_body" in scenario else None
    rendered_raw_body = render_template(scenario.get("raw_body"), context) if "raw_body" in scenario else None
    rendered_headers = render_template(scenario.get("headers", {}), context)
    rendered_files = render_template(scenario.get("files"), context) if "files" in scenario else None

    headers = dict(defaults_headers)
    if not isinstance(rendered_headers, dict):
        raise ConfigError(f"scenario '{scenario['name']}' headers must be an object")
    headers.update({str(key): str(value) for key, value in rendered_headers.items()})
    headers.update(
        resolve_auth_headers(
            auth_mode=str(scenario.get("auth") or "none"),
            auth_config=auth_config,
            sequence=int(context["sequence"]),
        )
    )

    request_kwargs = build_request_kwargs(
        scenario=scenario,
        config_dir=config_dir,
        file_cache=file_cache,
        rendered_query=rendered_query,
        rendered_json_body=rendered_json_body,
        rendered_form_body=rendered_form_body,
        rendered_raw_body=rendered_raw_body,
        rendered_files=rendered_files,
    )

    url = f"{base_url.rstrip('/')}/{str(rendered_path).lstrip('/')}"
    method = str(scenario["method"]).upper()
    expected_statuses = set(int(item) for item in scenario.get("expected_statuses", [200]))
    started_at = time.perf_counter()
    started_at_iso = utc_now_iso()

    try:
        response = await client.request(method, url, headers=headers, **request_kwargs)
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        content_type = response.headers.get("content-type", "")
        failure_detail = None
        if response.status_code not in expected_statuses and max_failure_details > 0:
            if content_type.startswith("application/json") or content_type.startswith("text/"):
                failure_detail = response.text[:400]
            else:
                failure_detail = f"<{content_type or 'binary'} {len(response.content)} bytes>"

        return {
            "request_index": request_index,
            "round_index": round_index + 1,
            "method": method,
            "path": str(rendered_path),
            "status_code": response.status_code,
            "ok": response.status_code in expected_statuses,
            "elapsed_ms": elapsed_ms,
            "response_size_bytes": len(response.content),
            "error": None,
            "failure_detail": failure_detail,
            "started_at": started_at_iso,
            "finished_at": utc_now_iso(),
        }
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
        return {
            "request_index": request_index,
            "round_index": round_index + 1,
            "method": method,
            "path": str(rendered_path),
            "status_code": None,
            "ok": False,
            "elapsed_ms": elapsed_ms,
            "response_size_bytes": 0,
            "error": f"{type(exc).__name__}: {exc}",
            "failure_detail": None,
            "started_at": started_at_iso,
            "finished_at": utc_now_iso(),
        }


async def run_round(
    *,
    scenario: dict[str, Any],
    base_url: str,
    defaults_headers: dict[str, str],
    auth_config: dict[str, Any],
    datasets: dict[str, Any],
    config_dir: Path,
    verify_tls: bool,
    timeout_seconds: float,
    concurrency: int,
    round_index: int,
    max_failure_details: int,
) -> dict[str, Any]:
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    timeout = httpx.Timeout(timeout_seconds)
    file_cache: dict[str, bytes] = {}
    batch_started = time.perf_counter()

    async with httpx.AsyncClient(
        timeout=timeout,
        verify=verify_tls,
        limits=limits,
        follow_redirects=False,
    ) as client:
        results = await asyncio.gather(
            *[
                run_single_request(
                    client=client,
                    base_url=base_url,
                    scenario=scenario,
                    defaults_headers=defaults_headers,
                    auth_config=auth_config,
                    datasets=datasets,
                    config_dir=config_dir,
                    file_cache=file_cache,
                    request_index=request_index,
                    round_index=round_index,
                    concurrency=concurrency,
                    max_failure_details=max_failure_details,
                )
                for request_index in range(concurrency)
            ]
        )

    duration_ms = round((time.perf_counter() - batch_started) * 1000, 2)
    latencies = [float(item["elapsed_ms"]) for item in results]
    status_counts = Counter(
        str(item["status_code"]) if item["status_code"] is not None else "EXCEPTION"
        for item in results
    )
    failed_requests = [item for item in results if not item["ok"]]

    return {
        "round": round_index + 1,
        "concurrency": concurrency,
        "total_requests": len(results),
        "expected_success_count": len(results) - len(failed_requests),
        "unexpected_count": len(failed_requests),
        "exception_count": sum(1 for item in results if item["status_code"] is None),
        "status_counts": dict(status_counts),
        "duration_ms": duration_ms,
        "rps": round((len(results) / duration_ms) * 1000, 2) if duration_ms > 0 else 0.0,
        "latency": summarize_latencies(latencies),
        "failed_samples": failed_requests[:max_failure_details],
        "request_results": results,
    }


def build_output_path(script_dir: Path, explicit_output: str | None) -> Path:
    if explicit_output:
        return Path(explicit_output).resolve()
    output_dir = script_dir / OUTPUT_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return (output_dir / f"loadtest-result-{stamp}.json").resolve()


def validate_scenario(scenario: dict[str, Any]) -> None:
    if not isinstance(scenario, dict):
        raise ConfigError("each scenario must be an object")
    if not str(scenario.get("name") or "").strip():
        raise ConfigError("scenario.name is required")
    if not str(scenario.get("method") or "").strip():
        raise ConfigError(f"scenario '{scenario['name']}' method is required")
    if not str(scenario.get("path") or "").strip():
        raise ConfigError(f"scenario '{scenario['name']}' path is required")

    expected_statuses = scenario.get("expected_statuses", [200])
    if not isinstance(expected_statuses, list) or not expected_statuses:
        raise ConfigError(f"scenario '{scenario['name']}' expected_statuses must be a non-empty array")


def print_round_summary(scenario_name: str, round_result: dict[str, Any]) -> None:
    latency = round_result["latency"]
    print(
        "[{name}] c={concurrency} round={round} total={total} unexpected={unexpected} "
        "exceptions={exceptions} p95={p95}ms p99={p99}ms rps={rps}".format(
            name=scenario_name,
            concurrency=round_result["concurrency"],
            round=round_result["round"],
            total=round_result["total_requests"],
            unexpected=round_result["unexpected_count"],
            exceptions=round_result["exception_count"],
            p95=latency["p95_ms"],
            p99=latency["p99_ms"],
            rps=round_result["rps"],
        )
    )


async def run_all(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    config_path = Path(args.config).resolve()
    config_dir = config_path.parent
    script_dir = Path(__file__).resolve().parent
    config = load_config(config_path)

    base_url = str(config.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise ConfigError("base_url is required")

    defaults = expand_env_placeholders(config["defaults"])
    defaults_headers = {"User-Agent": DEFAULT_USER_AGENT}
    if "headers" in defaults:
        if not isinstance(defaults["headers"], dict):
            raise ConfigError("defaults.headers must be an object")
        defaults_headers.update({str(key): str(value) for key, value in defaults["headers"].items()})

    only_names = parse_name_filter(args.only)
    concurrency_override = parse_concurrency_override(args.concurrency)
    default_concurrency_levels = ensure_list_of_ints(
        concurrency_override if concurrency_override is not None else defaults.get("concurrency_levels"),
        "defaults.concurrency_levels",
        DEFAULT_CONCURRENCY_LEVELS,
    )
    default_rounds = ensure_int(args.rounds if args.rounds is not None else defaults.get("rounds"), "defaults.rounds", DEFAULT_ROUNDS)
    default_timeout = ensure_float(
        args.timeout if args.timeout is not None else defaults.get("timeout_seconds"),
        "defaults.timeout_seconds",
        DEFAULT_TIMEOUT_SECONDS,
    )
    verify_tls = False if args.insecure else bool(defaults.get("verify_tls", True))

    selected_scenarios: list[dict[str, Any]] = []
    for raw_scenario in config["scenarios"]:
        validate_scenario(raw_scenario)
        if not raw_scenario.get("enabled", True):
            continue
        if only_names and raw_scenario["name"] not in only_names:
            continue
        selected_scenarios.append(expand_env_placeholders(raw_scenario))

    if not selected_scenarios:
        raise ConfigError("no scenarios selected")

    result_doc: dict[str, Any] = {
        "meta": {
            "generated_at": utc_now_iso(),
            "config_path": str(config_path),
            "base_url": base_url,
            "verify_tls": verify_tls,
            "default_concurrency_levels": default_concurrency_levels,
            "default_rounds": default_rounds,
            "default_timeout_seconds": default_timeout,
        },
        "scenario_results": [],
    }

    has_failure = False
    for scenario in selected_scenarios:
        scenario_name = str(scenario["name"])
        scenario_concurrency_levels = ensure_list_of_ints(
            concurrency_override if concurrency_override is not None else scenario.get("concurrency_levels"),
            f"scenario '{scenario_name}' concurrency_levels",
            default_concurrency_levels,
        )
        scenario_rounds = ensure_int(scenario.get("rounds"), f"scenario '{scenario_name}' rounds", default_rounds)
        scenario_timeout = ensure_float(
            scenario.get("timeout_seconds"),
            f"scenario '{scenario_name}' timeout_seconds",
            default_timeout,
        )

        scenario_result = {
            "name": scenario_name,
            "method": str(scenario["method"]).upper(),
            "path": str(scenario["path"]),
            "auth": str(scenario.get("auth") or "none"),
            "expected_statuses": scenario.get("expected_statuses", [200]),
            "concurrency_levels": scenario_concurrency_levels,
            "rounds": scenario_rounds,
            "timeout_seconds": scenario_timeout,
            "runs": [],
        }

        for concurrency in scenario_concurrency_levels:
            for round_index in range(scenario_rounds):
                round_result = await run_round(
                    scenario=scenario,
                    base_url=base_url,
                    defaults_headers=defaults_headers,
                    auth_config=config["auth"],
                    datasets=config["datasets"],
                    config_dir=config_dir,
                    verify_tls=verify_tls,
                    timeout_seconds=scenario_timeout,
                    concurrency=concurrency,
                    round_index=round_index,
                    max_failure_details=args.max_failure_details,
                )
                scenario_result["runs"].append(round_result)
                print_round_summary(scenario_name=scenario_name, round_result=round_result)
                if round_result["unexpected_count"] > 0:
                    has_failure = True

        scenario_runs = scenario_result["runs"]
        combined_latencies = [
            float(item["elapsed_ms"])
            for run in scenario_runs
            for item in run["request_results"]
        ]
        combined_status_counts = Counter()
        total_unexpected = 0
        total_exceptions = 0
        for run in scenario_runs:
            combined_status_counts.update(run["status_counts"])
            total_unexpected += int(run["unexpected_count"])
            total_exceptions += int(run["exception_count"])

        scenario_result["summary"] = {
            "total_requests": sum(int(run["total_requests"]) for run in scenario_runs),
            "unexpected_count": total_unexpected,
            "exception_count": total_exceptions,
            "status_counts": dict(combined_status_counts),
            "latency": summarize_latencies(combined_latencies),
        }
        result_doc["scenario_results"].append(scenario_result)

    global_status_counts = Counter()
    total_requests = 0
    total_unexpected = 0
    total_exceptions = 0
    global_latencies: list[float] = []
    for scenario_result in result_doc["scenario_results"]:
        summary = scenario_result["summary"]
        total_requests += int(summary["total_requests"])
        total_unexpected += int(summary["unexpected_count"])
        total_exceptions += int(summary["exception_count"])
        global_status_counts.update(summary["status_counts"])
        for run in scenario_result["runs"]:
            global_latencies.extend(float(item["elapsed_ms"]) for item in run["request_results"])

    result_doc["global_summary"] = {
        "scenario_count": len(result_doc["scenario_results"]),
        "total_requests": total_requests,
        "unexpected_count": total_unexpected,
        "exception_count": total_exceptions,
        "status_counts": dict(global_status_counts),
        "latency": summarize_latencies(global_latencies),
        "ok": total_unexpected == 0 and total_exceptions == 0,
    }

    output_path = build_output_path(script_dir=script_dir, explicit_output=args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[loadtest] result written to: {output_path}")

    return result_doc, has_failure


def main() -> int:
    args = parse_args()
    try:
        _, has_failure = asyncio.run(run_all(args))
    except ConfigError as exc:
        print(f"[loadtest] config error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("[loadtest] interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"[loadtest] fatal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 1 if has_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
