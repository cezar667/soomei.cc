from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from starlette.requests import Request

from api.routers import auth


def _register_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/auth/register",
            "query_string": b"",
            "headers": [
                (b"host", b"127.0.0.1:8000"),
                (b"cookie", b"session=session-from-other-tab"),
            ],
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 12345),
        }
    )


def test_register_invalid_csrf_redirects_back_to_onboarding(monkeypatch):
    monkeypatch.setattr(auth, "rate_limit_ip", lambda *args, **kwargs: None)

    response = auth.register(
        _register_request(),
        uid="uid-test",
        email="novo@example.com",
        pin="123456",
        password="SenhaTeste123",
        vanity="novo-slug",
        referral_code="CEZAR2026",
        lgpd="on",
        csrf_token="stale-token-from-old-tab",
    )

    assert response.status_code == 303
    location = response.headers["location"]
    parsed = urlparse(location)
    params = parse_qs(parsed.query)

    assert parsed.path == "/onboard/uid-test"
    assert params["email"] == ["novo@example.com"]
    assert params["vanity"] == ["novo-slug"]
    assert params["referral_code"] == ["CEZAR2026"]
    assert "sessão mudou ou expirou" in params["error"][0]
