"""Explicit, sanitized smoke check against a user-selected detector/PHP URL."""

from __future__ import annotations

import argparse
import asyncio
import math


MAIN_TEXT = "This is a temporary detector transport check."
EVIDENCE_TEXT = (
    '  学校图书馆正在整理本学期的阅读记录。工作人员逐项核对借阅日期、书名和归还情况，'
    '并将需要补充的信息交给各学院确认。\r\n'
    '这次核对关注记录是否完整，不根据单次借阅判断学生的学习表现。'
    '整理完成后，图书馆将发布一份说明，解释统计范围、处理方法和仍然缺失的数据。'
    '报告中的“待确认”表示信息不足，不能直接理解为记录错误。\n  '
) * 3


async def check(url: str) -> str | None:
    client = None
    error = "CONFIG_CHECK_FAILED"
    try:
        if not url.strip():
            raise ValueError
        from app.core.config import get_settings
        from app.services.evidence_engine import EvidenceEngine, _read_json
        from app.services.repre_guard_client import RepreGuardClient

        settings = get_settings()
        engine = EvidenceEngine(
            mode="serve",
            bundle_path=settings.detect_evidence_bundle_path,
            bundle_sha256=settings.detect_evidence_bundle_sha256,
            timeout_seconds=settings.detect_evidence_timeout_seconds,
        )
        if engine.status != "ready":
            raise ValueError
        client = RepreGuardClient(
            detect_url=url,
            service_token=settings.repre_guard_service_token.get_secret_value(),
            text_envelope_enabled=True,
        )
        error = "MAIN_CHECK_FAILED"
        result = await client.detect(MAIN_TEXT)
        score, threshold = result["score"], result["threshold"]
        if (
            result["score_type"] != "probability"
            or any(
                type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1
                for value in (score, threshold)
            )
            or result["label"] != ("AI" if score >= threshold else "HUMAN")
            or not isinstance(result["model_name"], str)
            or not result["model_name"].strip()
        ):
            raise ValueError
        error = "EVIDENCE_CHECK_FAILED"
        encoded = await asyncio.wait_for(
            client.route_evidence(EVIDENCE_TEXT), timeout=engine.timeout_seconds
        )
        evidence = engine.validate_router_response(_read_json(encoded))
        if evidence["status"] != "routed" or evidence["reason"] is not None:
            raise ValueError
        error = None
    except Exception:
        # Transport/config exceptions can contain private URLs, paths, or response bodies.
        pass
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                error = "CLIENT_CLOSE_FAILED"
    return error


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("INVALID_ARGUMENTS")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description="Check text-envelope transport using the configured service Token and Bundle.")
    parser.add_argument("--url", required=True, help="Explicit detector or PHP endpoint")
    try:
        args = parser.parse_args(argv)
    except ValueError:
        print("FAIL INVALID_ARGUMENTS")
        return 1
    try:
        error = asyncio.run(check(args.url))
    except (Exception, KeyboardInterrupt):
        error = "SMOKE_CHECK_FAILED"
    print(f"FAIL {error}" if error else "PASS TEXT_ENVELOPE")
    return int(error is not None)


if __name__ == "__main__":
    raise SystemExit(main())
