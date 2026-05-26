#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

DEFAULT_MEMBER_PASSWORD = "LoadTest!20260414"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_GUEST_COUNT = 5
DEFAULT_DETECTION_COUNT = 5
DEFAULT_DETECT_TEXT = (
    "This is a deliberately long sample paragraph for burst load testing. "
    "It contains enough visible characters to pass the backend minimum threshold, "
    "keeps sentence structure stable, and avoids accidental validation failures. "
    "The goal is to exercise the real detection path, database persistence path, "
    "quota deduction path, and downstream detect service integration under concurrent traffic."
)


class BootstrapError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provision online load test data and generate a local config.")
    parser.add_argument("--base-url", default="https://aiguard.isuperviz.net", help="Site base URL.")
    parser.add_argument("--admin-identifier", required=True, help="Admin login identifier.")
    parser.add_argument("--admin-password", required=True, help="Admin password.")
    parser.add_argument("--member-password", default=DEFAULT_MEMBER_PASSWORD, help="Password for the generated member account.")
    parser.add_argument(
        "--detect-member-count",
        type=int,
        default=3,
        help="How many dedicated member accounts to create for detect token rotation.",
    )
    parser.add_argument("--guest-count", type=int, default=DEFAULT_GUEST_COUNT, help="How many guest tokens to create.")
    parser.add_argument("--detection-count", type=int, default=DEFAULT_DETECTION_COUNT, help="How many detection/history records to create.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="HTTP timeout seconds.")
    parser.add_argument("--output-config", default="scripts/loadtest/scenarios.local.json", help="Path for the generated config.")
    parser.add_argument("--output-summary", default="scripts/loadtest/provisioned-resources.json", help="Path for the generated summary JSON.")
    return parser.parse_args()


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def unique_suffix() -> str:
    return f"{now_stamp()}-{secrets.token_hex(3)}"


class ApiSession:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout, follow_redirects=False)

    def close(self) -> None:
        self.client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        api_key: str | None = None,
        expected: tuple[int, ...] = (200,),
        **kwargs: Any,
    ) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if api_key:
            headers["X-API-Key"] = api_key

        response = self.client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        if response.status_code not in expected:
            try:
                detail = response.json()
            except Exception:  # noqa: BLE001
                detail = response.text
            raise BootstrapError(f"{method} {path} -> {response.status_code}: {detail}")

        content_type = response.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            return response.json()
        return response.content


def login(session: ApiSession, identifier: str, password: str) -> str:
    payload = {"identifier": identifier, "password": password}
    data = session.request("POST", "/api/v1/auth/login", json=payload)
    token = str(data.get("access_token") or data.get("accessToken") or "").strip()
    if not token:
        raise BootstrapError("login did not return access_token")
    return token


def register_member(session: ApiSession, password: str) -> dict[str, str]:
    return register_named_member(session=session, password=password, prefix="loadtest")


def register_named_member(session: ApiSession, password: str, prefix: str) -> dict[str, str]:
    suffix = unique_suffix()
    normalized_prefix = prefix.strip() or "loadtest"
    email = f"{normalized_prefix}-{suffix}@example.com"
    name = f"{normalized_prefix}-{suffix}"
    payload = {"email": email, "name": name, "password": password}
    session.request("POST", "/api/v1/auth/register", json=payload, expected=(201,))
    return {"email": email, "name": name}


def create_detect_member_pool(session: ApiSession, password: str, count: int) -> list[dict[str, Any]]:
    if count <= 0:
        raise BootstrapError("detect member count must be > 0")

    members: list[dict[str, Any]] = []
    for index in range(count):
        account = register_named_member(session=session, password=password, prefix=f"loadtest-detect-{index + 1}")
        token = login(session, account["email"], password)
        profile = fetch_me(session, token)
        members.append(
            {
                "email": account["email"],
                "name": account["name"],
                "password": password,
                "user_id": int(profile["id"]),
                "token": token,
            }
        )
    return members


def create_guest_tokens(session: ApiSession, count: int) -> list[str]:
    tokens: list[str] = []
    suffix = unique_suffix()
    for index in range(count):
        payload = {"guest_id": f"loadtest-guest-{suffix}-{index + 1}"}
        data = session.request("POST", "/api/v1/auth/guest", json=payload)
        token = str(data.get("access_token") or data.get("accessToken") or "").strip()
        if not token:
            raise BootstrapError("guest login did not return access_token")
        tokens.append(token)
    return tokens


def create_api_key(session: ApiSession, member_token: str) -> str:
    payload = {"name": f"loadtest-cli-{unique_suffix()}"}
    data = session.request("POST", "/api/v1/keys", token=member_token, json=payload, expected=(201,))
    api_key = str(data.get("key") or "").strip()
    if not api_key:
        raise BootstrapError("create api key did not return key")
    return api_key


def create_detections(session: ApiSession, member_token: str, count: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index in range(count):
        text = f"{DEFAULT_DETECT_TEXT}\n\nRequest sequence marker: {index + 1}."
        payload = {"text": text, "functions": ["scan"]}
        data = session.request("POST", "/api/v1/detect", token=member_token, json=payload)
        detection_id = data.get("detection_id", data.get("detectionId"))
        history_id = data.get("history_id", data.get("historyId"))
        if detection_id is None or history_id is None:
            raise BootstrapError(f"detect response missing ids: {data}")
        records.append(
            {
                "detection_id": int(detection_id),
                "history_id": int(history_id),
            }
        )
    return records


def create_team(session: ApiSession, member_token: str) -> int:
    payload = {"name": f"loadtest-team-{unique_suffix()}"}
    data = session.request("POST", "/api/v1/teams", token=member_token, json=payload, expected=(201,))
    return int(data["id"])


def fetch_me(session: ApiSession, token: str) -> dict[str, Any]:
    return session.request("GET", "/api/v1/auth/me", token=token)


def build_local_config(
    example_config_path: Path,
    output_config_path: Path,
    *,
    base_url: str,
    admin_token: str,
    member_token: str,
    member_tokens: list[str],
    member_api_key: str,
    guest_tokens: list[str],
    detection_records: list[dict[str, Any]],
    admin_user_id: int,
    member_user_ids: list[int],
    team_id: int,
) -> dict[str, Any]:
    config = json.loads(example_config_path.read_text(encoding="utf-8"))
    config["base_url"] = base_url.rstrip("/")
    config["auth"] = {
        "member_bearer_token": member_token,
        "member_bearer_tokens": member_tokens,
        "admin_bearer_token": admin_token,
        "member_api_key": member_api_key,
        "guest_bearer_tokens": guest_tokens,
    }

    history_ids = [item["history_id"] for item in detection_records]
    detection_ids = [item["detection_id"] for item in detection_records]
    config["datasets"]["history_ids"] = history_ids
    config["datasets"]["detection_ids"] = detection_ids
    config["datasets"]["user_ids"] = [admin_user_id, *member_user_ids]
    config["datasets"]["team_ids"] = [team_id]

    output_config_path.parent.mkdir(parents=True, exist_ok=True)
    output_config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config


def main() -> int:
    args = parse_args()
    root = Path.cwd()
    example_config_path = root / "scripts" / "loadtest" / "scenarios.example.json"
    output_config_path = (root / args.output_config).resolve()
    output_summary_path = (root / args.output_summary).resolve()

    session = ApiSession(base_url=args.base_url, timeout=args.timeout)
    try:
        admin_token = login(session, args.admin_identifier, args.admin_password)
        admin_me = fetch_me(session, admin_token)

        member_account = register_member(session, args.member_password)
        member_token = login(session, member_account["email"], args.member_password)
        member_me = fetch_me(session, member_token)
        detect_members = create_detect_member_pool(session, args.member_password, args.detect_member_count)

        guest_tokens = create_guest_tokens(session, args.guest_count)
        member_api_key = create_api_key(session, member_token)
        detection_records = create_detections(session, member_token, args.detection_count)
        team_id = create_team(session, member_token)

        build_local_config(
            example_config_path=example_config_path,
            output_config_path=output_config_path,
            base_url=args.base_url,
            admin_token=admin_token,
            member_token=member_token,
            member_tokens=[item["token"] for item in detect_members],
            member_api_key=member_api_key,
            guest_tokens=guest_tokens,
            detection_records=detection_records,
            admin_user_id=int(admin_me["id"]),
            member_user_ids=[int(member_me["id"]), *[item["user_id"] for item in detect_members]],
            team_id=team_id,
        )

        summary = {
            "base_url": args.base_url.rstrip("/"),
            "member_account": {
                "email": member_account["email"],
                "name": member_account["name"],
                "password": args.member_password,
                "user_id": int(member_me["id"]),
            },
            "detect_member_accounts": [
                {
                    "email": item["email"],
                    "name": item["name"],
                    "password": item["password"],
                    "user_id": item["user_id"],
                }
                for item in detect_members
            ],
            "admin_account": {
                "identifier": args.admin_identifier,
                "user_id": int(admin_me["id"]),
            },
            "guest_token_count": len(guest_tokens),
            "member_api_key": member_api_key,
            "team_id": team_id,
            "history_ids": [item["history_id"] for item in detection_records],
            "detection_ids": [item["detection_id"] for item in detection_records],
            "output_config": str(output_config_path),
        }
        output_summary_path.parent.mkdir(parents=True, exist_ok=True)
        output_summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[bootstrap] failed: {type(exc).__name__}: {exc}")
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
