from __future__ import annotations

from pathlib import Path

from api.app import _brand_footer_inject
from api.routers.auth import _brand_footer
from api.routers.slug import _slug_message_response


def test_brand_footer_injects_soomei_watermark_and_action_slot():
    body = "<main><section>conteudo</section></main>"

    rendered = _brand_footer_inject(body)

    assert "soomei-footer-mark" in rendered
    assert "soomei-watermark" in rendered
    assert "Soomei" in rendered
    assert "cartão digital" in rendered
    assert "href='https://soomei.com.br'" in rendered
    assert "/static/brand/soomei-logo-horizontal-white.svg" in rendered
    assert "{footer_action_html}" in rendered
    assert rendered.index("soomei-watermark") < rendered.index("{footer_action_html}")


def test_brand_footer_stays_outside_utility_shell():
    body = "<main class='wrap utility-shell'><section>pix</section></main>"

    rendered = _brand_footer_inject(body)

    assert "</main>" in rendered
    assert rendered.index("</main>") < rendered.index("soomei-footer-mark")
    assert rendered.index("utility-shell") < rendered.index("</main>")


def test_base_template_has_standard_soomei_footer():
    template = Path("templates/base.html").read_text(encoding="utf-8")

    assert "site-footer soomei-footer-mark" in template
    assert "soomei-watermark" in template
    assert "cartão digital" in template
    assert 'href="https://soomei.com.br"' in template
    assert "/static/brand/soomei-logo-horizontal-white.svg" in template
    assert "footer-auth-slot" in template


def test_auth_manual_pages_can_receive_standard_soomei_footer():
    rendered = _brand_footer("<main><h1>Senha</h1></main>")

    assert "soomei-footer-mark" in rendered
    assert "soomei-watermark" in rendered
    assert "cartão digital" in rendered
    assert "href='https://soomei.com.br'" in rendered
    assert rendered.index("Senha") < rendered.index("soomei-watermark")


def test_slug_message_pages_receive_standard_soomei_footer():
    request = type("Request", (), {"app": type("App", (), {"state": type("State", (), {"css_href": "/static/card.css"})()})()})()

    response = _slug_message_response(request, heading="Link indisponível", message="Escolha outro link.", status_code=409)
    body = response.body.decode("utf-8")

    assert response.status_code == 409
    assert "soomei-footer-mark" in body
    assert "href='https://soomei.com.br'" in body
