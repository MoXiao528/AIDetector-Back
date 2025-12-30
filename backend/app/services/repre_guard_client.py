# backend/app/services/repre_guard_client.py

from typing import Any, Dict

import httpx

from app.core.config import get_settings

settings = get_settings()


class RepreGuardError(Exception):
    """调用 RepreGuard 微服务失败时抛出的业务异常。"""


class RepreGuardClient:
    """
    与 RepreGuard 检测微服务交互的 HTTP 客户端。

    约定微服务接口：
    POST {DETECT_SERVICE_URL}/detect
    请求：{"text": "..."}
    响应：
    {
        "score": 2.93,
        "threshold": 2.4924452377944597,
        "label": "AI",
        "model_name": "Qwen/Qwen2.5-7B"
    }
    """

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self.base_url = (base_url or settings.detect_service_url).rstrip("/")
        self.timeout = timeout or settings.detect_service_timeout

    async def detect(self, text: str) -> Dict[str, Any]:
        payload = {"text": text}

        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            try:
                resp = await client.post("/detect", json=payload)
            except httpx.RequestError as exc:
                raise RepreGuardError(f"failed to call RepreGuard detect service: {exc}") from exc

        if resp.status_code != 200:
            raise RepreGuardError(
                f"RepreGuard detect service returned {resp.status_code}: {resp.text}"
            )

        data = resp.json()

        # 简单校验，确保核心字段存在
        for key in ("score", "threshold", "label", "model_name"):
            if key not in data:
                raise RepreGuardError(f"invalid response from RepreGuard: missing '{key}'")

        return data


repre_guard_client = RepreGuardClient()
