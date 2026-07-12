from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.admin_app import _layout, _login_csrf_protect, _login_page


def _request(*, cookies: dict[str, str], origin: str = "http://localhost:8001"):
    return SimpleNamespace(
        cookies=cookies,
        headers={"host": "localhost:8001", "origin": origin},
    )


def test_admin_login_csrf_ignores_public_session_cookie():
    request = _request(
        cookies={
            "session": "public-app-session-cookie",
            "csrf_token": "admin-login-csrf-token",
        }
    )

    _login_csrf_protect(request, "admin-login-csrf-token")


def test_admin_login_csrf_rejects_token_mismatch():
    request = _request(cookies={"csrf_token": "admin-login-csrf-token"})

    with pytest.raises(HTTPException):
        _login_csrf_protect(request, "outro-token")


def test_admin_login_page_uses_premium_layout():
    response = _login_page()
    body = response.body.decode("utf-8")

    assert 'class="admin-body admin-body--login"' in body
    assert "admin-login-card" in body
    assert "Soomei Admin" in body
    assert "Entrar no painel" in body
    assert "admin-brand__mark" in body


def test_admin_layout_renders_navigation_and_active_link():
    request = SimpleNamespace(
        cookies={},
        headers={"host": "localhost:8001"},
        url=SimpleNamespace(path="/cards/demo"),
    )
    response = _layout(request, "Admin | Teste", "<article><h3>Conteúdo</h3></article>", csrf_token="csrf-token")
    body = response.body.decode("utf-8")

    assert "admin-brand" in body
    assert "admin-nav-link is-active" in body
    assert "Cartões" in body
    assert "admin-logout-btn" in body
