from __future__ import annotations

import asyncio

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from api.app import http_exception_handler


def _request(accept: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/onboarding/teste",
            "headers": [(b"accept", accept.encode("ascii"))],
            "scheme": "http",
            "server": ("localhost", 8000),
            "client": ("127.0.0.1", 12345),
            "query_string": b"",
            "root_path": "",
        }
    )


def test_unknown_browser_path_renders_branded_404_page():
    response = asyncio.run(
        http_exception_handler(
            _request("text/html"),
            StarletteHTTPException(status_code=404, detail="Not Found"),
        )
    )
    body = response.body.decode("utf-8")

    assert response.status_code == 404
    assert "Essa página não existe" in body
    assert "Erro 404" in body
    assert 'href="/"' in body
    assert 'href="/login"' in body
    assert "soomei-footer-mark" in body
    assert response.headers["cache-control"] == "no-store"


def test_unknown_api_path_keeps_json_404_contract():
    response = asyncio.run(
        http_exception_handler(
            _request("application/json"),
            StarletteHTTPException(status_code=404, detail="Not Found"),
        )
    )

    assert response.status_code == 404
    assert response.body == b'{"detail":"Not Found"}'
