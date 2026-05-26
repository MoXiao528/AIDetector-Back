from __future__ import annotations

import json
from math import isfinite
from typing import Any, Dict
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings

settings = get_settings()
REQUIRED_RESPONSE_KEYS = ("score", "threshold", "label", "model_name", "score_type")
HEALTH_PROBE_TEXT = "This is a readiness probe."
VALID_LABELS = {"AI", "HUMAN"}
VALID_SCORE_TYPES = {"probability", "raw_logit"}


class RepreGuardError(Exception):
    """Raised when the downstream detect service returns a structured failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        code: str = "DETECT_BACKEND_ERROR",
        detail: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.detail = detail


class RepreGuardClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        detect_url: str | None = None,
        health_url: str | None = None,
    ) -> None:
        resolved_base_url = base_url if base_url is not None else settings.detect_service_url
        resolved_detect_url = detect_url if detect_url is not None else (
            None if base_url is not None else settings.detect_service_detect_url
        )
        resolved_health_url = health_url if health_url is not None else (
            None if base_url is not None or detect_url is not None else settings.detect_service_health_url
        )

        self.base_url = self._normalize_url(resolved_base_url)
        self.detect_url = self._normalize_url(resolved_detect_url)
        self.health_url = self._normalize_url(resolved_health_url)
        self.timeout = timeout or settings.detect_service_timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

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
        if not isinstance(data, dict):
            raise RepreGuardError(
                "invalid response from detect service: expected JSON object",
                code="INVALID_DETECT_RESPONSE",
                detail={"payload_type": type(data).__name__},
            )

        for key in REQUIRED_RESPONSE_KEYS:
            if key not in data:
                raise RepreGuardError(
                    f"invalid response from detect service: missing '{key}'",
                    code="INVALID_DETECT_RESPONSE",
                    detail={"missing": key},
                )

        try:
            score = float(data["score"])
            threshold = float(data["threshold"])
        except (TypeError, ValueError) as exc:
            raise RepreGuardError(
                "invalid response from detect service: score and threshold must be finite numbers",
                code="INVALID_DETECT_RESPONSE",
                detail={"score": data.get("score"), "threshold": data.get("threshold")},
            ) from exc

        if not isfinite(score) or not isfinite(threshold):
            raise RepreGuardError(
                "invalid response from detect service: score and threshold must be finite numbers",
                code="INVALID_DETECT_RESPONSE",
                detail={"score": data.get("score"), "threshold": data.get("threshold")},
            )

        score_type = str(data["score_type"]).strip()
        if score_type not in VALID_SCORE_TYPES:
            raise RepreGuardError(
                "invalid response from detect service: unsupported score_type",
                code="INVALID_DETECT_RESPONSE",
                detail={"score_type": data.get("score_type")},
            )

        if score_type == "probability" and (score < 0 or score > 1 or threshold < 0 or threshold > 1):
            raise RepreGuardError(
                "invalid response from detect service: probability score and threshold must be in [0, 1]",
                code="INVALID_DETECT_RESPONSE",
                detail={"score": score, "threshold": threshold, "score_type": score_type},
            )

        label = str(data["label"]).strip().upper()
        if label not in VALID_LABELS:
            raise RepreGuardError(
                "invalid response from detect service: unsupported label",
                code="INVALID_DETECT_RESPONSE",
                detail={"label": data.get("label")},
            )

        model_name = str(data["model_name"]).strip()
        if not model_name:
            raise RepreGuardError(
                "invalid response from detect service: model_name must be a non-empty string",
                code="INVALID_DETECT_RESPONSE",
                detail={"model_name": data.get("model_name")},
            )

        return {
            **data,
            "score": score,
            "threshold": threshold,
            "label": label,
            "model_name": model_name,
            "score_type": score_type,
        }

    @staticmethod
    def _decode_error_payload(resp: httpx.Response) -> Any:
        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                return resp.json()
            except json.JSONDecodeError:
                return {"message": resp.text}
        return {"message": resp.text}

    @staticmethod
    def _raise_for_error_response(resp: httpx.Response) -> None:
        payload = RepreGuardClient._decode_error_payload(resp)
        normalized_payload = payload.get("detail") if isinstance(payload, dict) and isinstance(payload.get("detail"), dict) else payload

        code = "DETECT_BACKEND_ERROR"
        message = f"detect service returned {resp.status_code}"
        detail = normalized_payload

        if isinstance(normalized_payload, dict):
            code = str(normalized_payload.get("code") or code)
            message = str(normalized_payload.get("message") or normalized_payload.get("error") or message)
        elif isinstance(payload, dict):
            code = str(payload.get("code") or code)
            message = str(payload.get("message") or payload.get("error") or message)
        elif resp.text:
            message = resp.text

        raise RepreGuardError(message, status_code=resp.status_code, code=code, detail=detail)

    async def health(self) -> Dict[str, Any]:
        health_url = self._resolve_health_url()
        if health_url is None:
            await self.detect(HEALTH_PROBE_TEXT)
            return {"status": "ok", "mode": "detect_probe"}

        try:
            resp = await self._get_client().get(health_url)
        except httpx.RequestError as exc:
            raise RepreGuardError(f"failed to call detect service health endpoint: {exc}") from exc

        if resp.status_code != 200:
            self._raise_for_error_response(resp)

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise RepreGuardError(
                "detect service health endpoint returned non-JSON response",
                detail={"status_code": resp.status_code, "content_type": resp.headers.get("content-type", "")},
            ) from exc
        if data.get("status") != "ok":
            raise RepreGuardError(f"unexpected detect service health payload: {data}")

        return data

    async def detect(self, text: str) -> Dict[str, Any]:
        payload = {"text": text}
        detect_url = self._resolve_detect_url()

        try:
            resp = await self._get_client().post(detect_url, json=payload)
        except httpx.RequestError as exc:
            raise RepreGuardError(f"failed to call detect service: {exc}") from exc

        if resp.status_code != 200:
            self._raise_for_error_response(resp)

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise RepreGuardError(
                "detect service returned non-JSON response",
                detail={"status_code": resp.status_code, "content_type": resp.headers.get("content-type", "")},
            ) from exc
        return self._validate_detect_payload(data)


repre_guard_client = RepreGuardClient()
