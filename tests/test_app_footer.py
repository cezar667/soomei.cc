from __future__ import annotations

from pathlib import Path

from api.app import _brand_footer_inject
from api.routers.auth import _brand_footer


def test_brand_footer_injects_soomei_watermark_and_action_slot():
    body = "<main><section>conteudo</section></main>"

    rendered = _brand_footer_inject(body)

    assert "soomei-footer-mark" in rendered
    assert "soomei-watermark" in rendered
    assert "Soomei" in rendered
    assert "cartão digital" in rendered
    assert "{footer_action_html}" in rendered
    assert rendered.index("soomei-watermark") < rendered.index("{footer_action_html}")


def test_base_template_has_standard_soomei_footer():
    template = Path("templates/base.html").read_text(encoding="utf-8")

    assert "site-footer soomei-footer-mark" in template
    assert "soomei-watermark" in template
    assert "cartão digital" in template
    assert "footer-auth-slot" in template


def test_auth_manual_pages_can_receive_standard_soomei_footer():
    rendered = _brand_footer("<main><h1>Senha</h1></main>")

    assert "soomei-footer-mark" in rendered
    assert "soomei-watermark" in rendered
    assert "cartão digital" in rendered
    assert rendered.index("Senha") < rendered.index("soomei-watermark")
