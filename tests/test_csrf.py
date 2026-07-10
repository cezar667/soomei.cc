from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.core import csrf


def _request(cookie: str = "", query_string: bytes = b"") -> Request:
    headers = [(b"host", b"127.0.0.1:8000")]
    if cookie:
        headers.append((b"cookie", cookie.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/edit/cezar",
            "query_string": query_string,
            "headers": headers,
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 12345),
        }
    )


def test_authenticated_csrf_survives_missing_csrf_cookie():
    request = _request("session=session-secret")
    token = csrf.ensure_csrf_token(request)

    csrf.validate_csrf(request, token)


def test_authenticated_csrf_rejects_unknown_token():
    request = _request("session=session-secret")

    with pytest.raises(HTTPException) as exc:
        csrf.validate_csrf(request, "unknown-token")

    assert exc.value.status_code == 403
    assert exc.value.detail == "CSRF token invalido."


def test_anonymous_csrf_still_uses_double_submit_cookie():
    request = _request("csrf_token=anonymous-token-value")

    csrf.validate_csrf(request, "anonymous-token-value")


def test_authenticated_csrf_accepts_query_token_for_native_multipart_form():
    base_request = _request("session=session-secret")
    token = csrf.ensure_csrf_token(base_request)
    request = _request(
        "session=session-secret",
        f"csrf_token={token}".encode("ascii"),
    )

    csrf.validate_csrf(request, None)
