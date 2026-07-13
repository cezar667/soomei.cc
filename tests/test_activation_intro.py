from __future__ import annotations

from types import SimpleNamespace

from api.routers import auth


def _request():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(css_href="/static/card.css")))


def test_activated_page_renders_intro_video_and_delayed_actions():
    response = auth.activated(_request(), next="/cezar", configure="/edit/cezar")
    body = response.body.decode("utf-8")

    assert "/static/intro/intro-soomei-activated.mp4" in body
    assert "/static/intro/intro-soomei-activated.MOV" in body
    assert "poster='/static/img/soomei_logo.png'" in body
    assert "id='activationVideo'" in body
    assert "id='activationActions'" in body
    assert "Configurar cartão" in body
    assert "href='/edit/cezar'" in body
    assert "Visualizar cartão" in body
    assert "href='/cezar'" in body
    assert "video.addEventListener('ended', reveal)" in body
    assert "}, 7000);" in body
    assert "activation-footer" in body
    assert "soomei-watermark__brand" in body
    assert "cartão digital" in body


def test_activated_page_rejects_external_next_urls():
    response = auth.activated(_request(), next="https://evil.example", configure="//evil.example")
    body = response.body.decode("utf-8")

    assert "https://evil.example" not in body
    assert "//evil.example" not in body
    assert "href='/'" in body


def test_verify_redirects_to_activation_intro(monkeypatch):
    request = SimpleNamespace(
        cookies={},
        headers={},
        query_params={},
        client=SimpleNamespace(host="127.0.0.1"),
        url=SimpleNamespace(scheme="http"),
    )
    monkeypatch.setattr(auth, "rate_limit_ip", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        auth.auth_service,
        "verify_email",
        lambda _token: SimpleNamespace(target_slug="cezar", session_token="session-token"),
    )

    response = auth.verify_email(request, "token-ok")

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/activated?next=%2Fcezar&configure=%2Fedit%2Fcezar"
