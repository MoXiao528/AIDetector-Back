import asyncio

import pytest

from app.main import app


@pytest.mark.parametrize(
    "content_length",
    [None, b"64", b"999999999"],
)
def test_removed_parse_files_route_returns_404_without_reading_body(
    content_length: bytes | None,
) -> None:
    receive_calls = 0
    response_messages: list[dict] = []
    headers = [(b"content-type", b"multipart/form-data; boundary=attack")]
    if content_length is not None:
        headers.append((b"content-length", content_length))

    async def receive() -> dict:
        nonlocal receive_calls
        receive_calls += 1
        raise AssertionError("removed file endpoint must not read the request body")

    async def send(message: dict) -> None:
        response_messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/detections/parse-files",
        "raw_path": b"/api/v1/detections/parse-files",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "state": {},
    }

    asyncio.run(app(scope, receive, send))

    response_start = next(
        message for message in response_messages if message["type"] == "http.response.start"
    )
    assert response_start["status"] == 404
    assert receive_calls == 0


def test_removed_parse_files_route_is_absent_from_openapi() -> None:
    assert "/api/v1/detections/parse-files" not in app.openapi()["paths"]
