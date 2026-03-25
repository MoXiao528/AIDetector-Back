# backend/app/services/repre_guard_client.py

from typing import Any, Dict
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings

settings = get_settings()
REQUIRED_RESPONSE_KEYS = ("score", "threshold", "label", "model_name")
HEALTH_PROBE_TEXT = "This is a readiness probe."


class RepreGuardError(Exception):
    """调用 RepreGuard 微服务失败时抛出的业务异常。"""


class RepreGuardClient:
    """
    与 RepreGuard 检测微服务交互的 HTTP 客户端。

    支持两种下游接法：
    1. 传统 base URL：POST {DETECT_SERVICE_URL}/detect
    2. 完整 detect URL：POST DETECT_SERVICE_DETECT_URL
    请求：{"text": "..."}
    响应：
    {
        "score": 2.93,
        "threshold": 2.4924452377944597,
        "label": "AI",
        "model_name": "Qwen/Qwen2.5-7B"
    }
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        detect_url: str | None = None,
        health_url: str | None = None,
    ) -> None:
        self.base_url = self._normalize_url(base_url or settings.detect_service_url)
        self.detect_url = self._normalize_url(detect_url or settings.detect_service_detect_url)
        self.health_url = self._normalize_url(health_url or settings.detect_service_health_url)
        self.timeout = timeout or settings.detect_service_timeout

    @staticmethod
    def _normalize_url(url: str | None) -> str | None:
        if url is None:
            return None
        normalized = str(url).strip()
        return normalized.rstrip("/") if normalized else None

    @staticmethod
    def _looks_like_direct_detect_url(url: str | None) -> bool:
        if not url:
            return False
        path = (urlparse(url).path or "").rstrip("/")
        return bool(path) and (path.endswith("/detect") or path.endswith(".php"))

    def _resolve_detect_url(self) -> str:
        if self.detect_url:
            return self.detect_url
        if self._looks_like_direct_detect_url(self.base_url):
            return self.base_url or ""
        return f"{self.base_url}/detect"

    def _resolve_health_url(self) -> str | None:
        if self.health_url:
            return self.health_url
        if self.detect_url or self._looks_like_direct_detect_url(self.base_url):
            return None
        return f"{self.base_url}/health"

    @staticmethod
    def _validate_detect_payload(data: Dict[str, Any]) -> Dict[str, Any]:
        for key in REQUIRED_RESPONSE_KEYS:
            if key not in data:
                raise RepreGuardError(f"invalid response from detect service: missing '{key}'")
        return data

    async def health(self) -> Dict[str, Any]:
        health_url = self._resolve_health_url()
        if health_url is None:
            await self.detect(HEALTH_PROBE_TEXT)
            return {"status": "ok", "mode": "detect_probe"}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.get(health_url)
            except httpx.RequestError as exc:
                raise RepreGuardError(f"failed to call detect service health endpoint: {exc}") from exc

        if resp.status_code != 200:
            raise RepreGuardError(
                f"detect service health endpoint returned {resp.status_code}: {resp.text}"
            )

        data = resp.json()
        if data.get("status") != "ok":
            raise RepreGuardError(f"unexpected detect service health payload: {data}")

        return data

    async def detect(self, text: str) -> Dict[str, Any]:
        payload = {"text": text}
        detect_url = self._resolve_detect_url()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(detect_url, json=payload)
            except httpx.RequestError as exc:
                raise RepreGuardError(f"failed to call detect service: {exc}") from exc

        if resp.status_code != 200:
            raise RepreGuardError(
                f"detect service returned {resp.status_code}: {resp.text}"
            )

        data = resp.json()
        return self._validate_detect_payload(data)


repre_guard_client = RepreGuardClient()
