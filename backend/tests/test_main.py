from __future__ import annotations

import json

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
import pytest

from app.main import app, http_exception_handler


@pytest.mark.anyio
async def test_http_exception_handler_preserves_http_exception_headers() -> None:
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/detect",
            "raw_path": b"/api/v1/detect",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )
    exc = HTTPException(
        status_code=409,
        detail={
            "code": "DETECTION_IN_PROGRESS",
            "message": "Detection is still processing",
            "detail": None,
        },
        headers={"Retry-After": "3"},
    )

    response = await http_exception_handler(request, exc)

    assert response.status_code == 409
    assert response.headers["retry-after"] == "3"
    assert json.loads(response.body)["code"] == "DETECTION_IN_PROGRESS"


def test_cors_exposes_retry_after_header() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/__cors_probe__",
            headers={"Origin": "http://localhost:5173"},
        )

    exposed = {value.strip().lower() for value in response.headers["access-control-expose-headers"].split(",")}
    assert "retry-after" in exposed
