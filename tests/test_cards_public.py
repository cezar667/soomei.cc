from __future__ import annotations

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
