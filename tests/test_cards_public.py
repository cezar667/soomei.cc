from __future__ import annotations

from types import SimpleNamespace

from api.routers import cards


def test_v1_featured_button_renders_selected_left_icon(monkeypatch):
    monkeypatch.setattr(cards, "BRAND_FOOTER", lambda value: value)
    profile = {
        "full_name": "Cezar Damasceno",
        "title": "Diretor | Soomei",
        "whatsapp": "+55 34 99999-9999",
        "email_public": "contato@soomei.com.br",
        "site_url": "soomei.cc",
        "photo_url": "/static/uploads/tksc4o.jpg?v=abc",
        "pix_key": "contato@soomei.com.br",
        "featured_label": "Agendar experiência",
        "featured_url": "https://soomei.cc/agendar",
        "featured_icon": "briefcase",
        "featured_enabled": True,
        "links": [],
    }

    response = cards.visitor_public_card(
        profile,
        "cezar",
        is_owner=False,
        view_count=0,
        card={"uid": "tksc4o", "vanity": "cezar"},
        request=None,
    )
    body = response.body.decode("utf-8")

    assert "featured-cta__lead-icon" in body
    assert "M8 7V5a2 2 0 0 1 2-2h4" in body
    assert "Agendar experiência" in body
    assert "fixed-action-copy" in body
    assert "<strong>Site</strong><small>Conheça mais</small>" in body
    assert "<strong>Endereço</strong><small>Não informado</small>" in body
    assert "<strong>Pagamento Pix</strong><small>Pagar com QR Code</small>" in body


def test_footer_auth_actions_render_discreet_pill_buttons():
    login_html = cards._footer_action_markup(is_owner=False, slug="cezar")
    logout_html = cards._footer_action_markup(
        is_owner=True,
        slug="cezar",
        csrf_token_html="csrf-token",
    )

    assert "footer-auth-btn footer-auth-btn--login" in login_html
    assert "Entrar" in login_html
    assert "footer-auth-btn footer-auth-btn--logout" in logout_html
    assert "method='post' action='/auth/logout'" in logout_html
    assert "name='csrf_token' value='csrf-token'" in logout_html
    assert "Sair" in logout_html


def test_cover_can_be_hidden_without_removing_saved_image(monkeypatch):
    monkeypatch.setattr(cards, "BRAND_FOOTER", lambda value: value)
    profile = {
        "full_name": "Cezar Damasceno",
        "title": "Diretor | Soomei",
        "photo_url": "/static/uploads/tksc4o.jpg?v=abc",
        "cover_url": "/static/uploads/tksc4o_cover.jpg?v=cover",
        "cover_show": False,
        "links": [],
    }

    response = cards.visitor_public_card(
        profile,
        "cezar",
        is_owner=False,
        view_count=0,
        card={},
        request=None,
    )
    body = response.body.decode("utf-8")

    assert "card-cover" not in body
    assert "/static/uploads/tksc4o_cover.jpg?v=cover" not in body


def test_additional_links_respect_visibility_and_explicit_type(monkeypatch):
    monkeypatch.setattr(cards, "BRAND_FOOTER", lambda value: value)
    profile = {
        "full_name": "Cezar Damasceno",
        "title": "Diretor | Soomei",
        "photo_url": "/static/uploads/tksc4o.jpg?v=abc",
        "links": [
            {
                "label": "Meu curso",
                "href": "https://soomei.com.br/curso",
                "type": "course",
                "visible": True,
            },
            {
                "label": "Link oculto",
                "href": "https://soomei.com.br/oculto",
                "type": "site",
                "visible": False,
            },
        ],
    }

    response = cards.visitor_public_card(
        profile,
        "cezar",
        is_owner=False,
        view_count=0,
        card={},
        request=None,
    )
    body = response.body.decode("utf-8")

    assert "brand-course" in body
    assert "Meu curso" in body
    assert "Link oculto" not in body


def test_active_spotlight_badge_renders_clickable_explanation(monkeypatch):
    monkeypatch.setattr(cards, "BRAND_FOOTER", lambda value: value)
    monkeypatch.setattr(
        cards._referral_service.repository,
        "active_badge",
        lambda _uid: SimpleNamespace(label="Destaque Soomei"),
    )
    profile = {
        "full_name": "Cezar Damasceno",
        "title": "Diretor | Soomei",
        "photo_url": "/static/uploads/tksc4o.jpg?v=abc",
        "links": [],
    }

    response = cards.visitor_public_card(
        profile,
        "cezar",
        is_owner=False,
        view_count=0,
        card={"uid": "tksc4o", "vanity": "cezar"},
        request=None,
    )
    body = response.body.decode("utf-8")

    assert "id='soomeiSpotlightBtn'" in body
    assert "aria-controls='soomeiSpotlightModal'" in body
    assert "id='soomeiSpotlightModal'" in body
    assert "Perfil em Destaque Soomei" in body
    assert "/static/img/logo_single.png" in body


def test_active_spotlight_badge_can_be_hidden_by_profile(monkeypatch):
    monkeypatch.setattr(cards, "BRAND_FOOTER", lambda value: value)
    monkeypatch.setattr(
        cards._referral_service.repository,
        "active_badge",
        lambda _uid: SimpleNamespace(label="Destaque Soomei"),
    )
    profile = {
        "full_name": "Cezar Damasceno",
        "title": "Diretor | Soomei",
        "photo_url": "/static/uploads/tksc4o.jpg?v=abc",
        "spotlight_badge_show": False,
        "links": [],
    }

    response = cards.visitor_public_card(
        profile,
        "cezar",
        is_owner=False,
        view_count=0,
        card={"uid": "tksc4o", "vanity": "cezar"},
        request=None,
    )
    body = response.body.decode("utf-8")

    assert "id='soomeiSpotlightBtn'" not in body
    assert "id='soomeiSpotlightModal'" not in body


def test_root_without_slug_redirects_to_login(monkeypatch):
    request = SimpleNamespace(headers={"host": "localhost:8000"}, cookies={})

    monkeypatch.setattr(cards, "find_card_by_custom_domain", lambda _host: ({}, "", None))
    monkeypatch.setattr(cards, "current_user_email", lambda _request: None)

    response = cards.custom_domain_root(request)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_blocked_route_is_registered_before_slug_catch_all():
    paths = [getattr(route, "path", "") for route in cards.router.routes]

    assert paths.index("/blocked") < paths.index("/{slug}")


def test_blocked_card_redirects_to_blocked_screen():
    request = SimpleNamespace(query_params={}, headers={}, cookies={})
    response = cards._serve_slug(
        "cezar",
        request,
        prefetched=(
            {},
            "uid-blocked",
            {
                "uid": "uid-blocked",
                "status": "blocked",
                "user": "cezar@soomei.com.br",
                "vanity": "cezar",
            },
        ),
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/blocked"
