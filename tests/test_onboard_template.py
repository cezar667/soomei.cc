from __future__ import annotations

from pathlib import Path


def test_onboard_template_keeps_activation_contract_and_new_design():
    template = Path("templates/onboard.html").read_text(encoding="utf-8")

    assert 'class="onboard-shell"' in template
    assert 'class="onboard-card carbon"' in template
    assert "Ative seu cartão digital" in template
    assert "Ativação segura" in template

    assert 'id="onbForm" method="post" action="/auth/register"' in template
    assert 'name="csrf_token"' in template
    assert 'name="uid"' in template
    assert 'id="emailInput" name="email"' in template
    assert 'id="pinInput" name="pin"' in template
    assert 'id="passwordInput" name="password"' in template
    assert 'id="vanityInput" name="vanity"' in template
    assert 'name="lgpd"' in template
    assert 'id="openTerms"' in template


def test_onboard_css_has_responsive_premium_components():
    css = Path("web/card.css").read_text(encoding="utf-8-sig")

    assert ".onboard-card" in css
    assert ".onboard-steps" in css
    assert ".onboard-slug-row" in css
    assert "@media (max-width:640px)" in css


def test_confirm_email_template_keeps_pending_email_contract_and_design():
    template = Path("templates/confirm_email.html").read_text(encoding="utf-8")

    assert 'class="confirm-shell"' in template
    assert 'class="confirm-card carbon"' in template
    assert "Última etapa" in template
    assert 'id="currentEmail"' in template
    assert 'id="devVerifyLink"' in template
    assert 'id="openPendingEmail"' in template
    assert 'id="pendingEmailCard"' in template
    assert 'id="pendingEmailForm"' in template
    assert 'name="new_email"' in template
    assert 'name="pin"' in template
    assert 'id="resendPendingBtn"' in template


def test_slug_select_template_keeps_contract_and_premium_design():
    template = Path("templates/slug_select.html").read_text(encoding="utf-8")

    assert 'class="slug-shell"' in template
    assert 'class="slug-card carbon"' in template
    assert "Escolha o link do seu cartão" in template
    assert 'method="post" action="/slug/select/{{ uid }}"' in template
    assert 'name="csrf_token"' in template
    assert 'name="value" id="slug"' in template
    assert 'data-current="{{ current }}"' in template
    assert 'id="slugPreview"' in template
    assert 'id="slugInfoBtn"' in template
    assert 'id="slugInfoTip"' in template
    assert 'id="msg"' in template
    assert "currentSlug" in template


def test_slug_select_css_has_responsive_components():
    css = Path("web/card.css").read_text(encoding="utf-8-sig")

    assert ".slug-card" in css
    assert ".slug-preview" in css
    assert ".slug-tips" in css
    assert ".slug-submit" in css


def test_card_under_construction_template_uses_premium_status_design():
    template = Path("templates/card_under_construction.html").read_text(encoding="utf-8-sig")

    assert 'class="status-shell"' in template
    assert 'class="status-card carbon"' in template
    assert "Quase pronto para impressionar" in template
    assert "status-link-preview" in template
    assert "status-progress" in template
    assert "{{ cta_url }}" in template
    assert "/onboard/{{ uid }}" in template
    assert "/login" in template


def test_card_under_construction_css_has_responsive_status_components():
    css = Path("web/card.css").read_text(encoding="utf-8-sig")

    assert ".status-card" in css
    assert ".status-link-preview" in css
    assert ".status-progress" in css
    assert ".status-actions .btn.primary" in css
    assert ".status-actions .btn{flex:0 1 auto" in css


def test_login_template_keeps_auth_contract_and_premium_design():
    template = Path("templates/login.html").read_text(encoding="utf-8")

    assert 'class="login-shell"' in template
    assert 'class="login-card carbon"' in template
    assert "Entre para gerenciar seu cartão" in template
    assert 'method="post" action="/auth/login"' in template
    assert 'name="csrf_token"' in template
    assert 'name="uid"' in template
    assert 'id="loginEmail" name="email"' in template
    assert 'id="loginPassword" name="password"' in template
    assert 'href="/auth/forgot"' in template


def test_auth_recovery_pages_use_shared_premium_design():
    source = Path("api/routers/auth.py").read_text(encoding="utf-8-sig")

    assert "class='auth-shell'" in source
    assert "class='auth-card carbon'" in source
    assert "action='/auth/forgot'" in source
    assert "name='email' type='email'" in source
    assert "action='/auth/reset'" in source
    assert "name='password' type='password'" in source
    assert "name='confirm' type='password'" in source


def test_login_and_recovery_css_has_shared_components():
    css = Path("web/card.css").read_text(encoding="utf-8-sig")

    assert ".login-card,.auth-card" in css
    assert ".login-benefits" in css
    assert ".auth-submit" in css
    assert ".auth-actions" in css


def test_remaining_static_templates_use_premium_status_design():
    pin = Path("templates/onboard_pin.html").read_text(encoding="utf-8")
    invalid = Path("templates/invalid.html").read_text(encoding="utf-8")
    blocked = Path("templates/blocked.html").read_text(encoding="utf-8")
    legal = Path("templates/legal_terms.html").read_text(encoding="utf-8")

    assert 'class="pin-shell"' in pin
    assert 'class="pin-card carbon"' in pin
    assert 'method="post" action="/onboard/{{ uid }}/pin"' in pin
    assert 'name="csrf_token"' in pin
    assert 'name="pin"' in pin

    assert 'class="status-shell"' in invalid
    assert 'class="status-card carbon"' in invalid
    assert "Esse cartão não foi localizado" in invalid

    assert 'class="status-shell"' in blocked
    assert 'class="status-card carbon"' in blocked
    assert "Cartão temporariamente bloqueado" in blocked

    assert 'class="legal-shell"' in legal
    assert 'class="legal-card carbon"' in legal
    assert "{{ safe|safe }}" in legal


def test_remaining_screen_css_has_pin_legal_utility_and_modal_components():
    css = Path("web/card.css").read_text(encoding="utf-8-sig")

    assert ".pin-card" in css
    assert ".legal-card" in css
    assert ".utility-card" in css
    assert ".utility-qr" in css
    assert ".utility-amount-row" in css
    assert ".slug-modal-card" in css
    assert ".slug-modal-preview" in css


def test_cards_utility_pages_and_error_states_use_shared_design():
    source = Path("api/routers/cards.py").read_text(encoding="utf-8-sig")

    assert "_public_message_response" in source
    assert "class='utility-card carbon'" in source
    assert "class='utility-qr'" in source
    assert "class='utility-amount-row'" in source
    assert "Falha ao gerar QR Offline" in source
    assert "Falha ao gerar QR Pix" in source
    assert "class='status-card carbon'" in source


def test_pages_and_edit_modal_use_redesigned_fallbacks():
    pages = Path("api/routers/pages.py").read_text(encoding="utf-8-sig")
    edit = Path("api/routers/card_edit.py").read_text(encoding="utf-8-sig")
    slug = Path("api/routers/slug.py").read_text(encoding="utf-8-sig")

    assert "Termos indisponíveis" in pages
    assert "class='status-card carbon'" in pages
    assert "slug-modal-card carbon" in edit
    assert "slug-modal-preview" in edit
    assert "soomei.cc/" in edit
    assert "_slug_message_response" in slug
    assert "class='slug-card carbon'" in slug
