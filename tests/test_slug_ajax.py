from __future__ import annotations

import json

from starlette.requests import Request

from api.core import csrf
from api.routers import slug as slug_router
from api.services.slug_service import SlugUnavailableError


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/slug/select/tksc4o",
            "query_string": b"",
            "headers": [
                (b"host", b"127.0.0.1:8000"),
                (b"cookie", b"session=session-secret"),
                (b"accept", b"application/json"),
                (b"x-requested-with", b"XMLHttpRequest"),
            ],
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 12345),
        }
    )


def _plain_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/slug/select/tksc4o",
            "query_string": b"",
            "headers": [
                (b"host", b"127.0.0.1:8000"),
                (b"cookie", b"session=session-secret"),
            ],
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 12345),
        }
    )


class _SlugService:
    def __init__(self, *, unavailable: bool = False):
        self.unavailable = unavailable

    def assign_slug(self, uid: str, slug: str) -> str:
        if self.unavailable:
            raise SlugUnavailableError("indisponivel")
        assert uid == "tksc4o"
        return slug


def test_slug_select_ajax_returns_edit_url(monkeypatch):
    request = _request()
    token = csrf.ensure_csrf_token(request)
    monkeypatch.setattr(slug_router, "_find_card_context", lambda _card_id: ("tksc4o", {"user": "owner@example.com"}))
    monkeypatch.setattr(slug_router, "current_user_email", lambda _request: "owner@example.com")
    monkeypatch.setattr(slug_router, "_get_slug_service", lambda _request: _SlugService())

    response = slug_router.slug_select_post("tksc4o", request, value="novo-slug", csrf_token=token)
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert payload == {
        "ok": True,
        "slug": "novo-slug",
        "public_url": "/novo-slug",
        "edit_url": "/edit/novo-slug",
    }


def test_slug_select_ajax_returns_json_for_unavailable_slug(monkeypatch):
    request = _request()
    token = csrf.ensure_csrf_token(request)
    monkeypatch.setattr(slug_router, "_find_card_context", lambda _card_id: ("tksc4o", {"user": "owner@example.com"}))
    monkeypatch.setattr(slug_router, "current_user_email", lambda _request: "owner@example.com")
    monkeypatch.setattr(slug_router, "_get_slug_service", lambda _request: _SlugService(unavailable=True))

    response = slug_router.slug_select_post("tksc4o", request, value="novo-slug", csrf_token=token)
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 409
    assert payload["ok"] is False
    assert payload["error"] == "slug_unavailable"


def test_slug_select_form_can_return_to_edit_screen(monkeypatch):
    request = _plain_request()
    token = csrf.ensure_csrf_token(request)
    monkeypatch.setattr(slug_router, "_find_card_context", lambda _card_id: ("tksc4o", {"user": "owner@example.com"}))
    monkeypatch.setattr(slug_router, "current_user_email", lambda _request: "owner@example.com")
    monkeypatch.setattr(slug_router, "_get_slug_service", lambda _request: _SlugService())

    response = slug_router.slug_select_post(
        "tksc4o",
        request,
        value="novo-slug",
        csrf_token=token,
        next="edit",
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/edit/novo-slug"
