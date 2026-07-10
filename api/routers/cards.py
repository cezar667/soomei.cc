from __future__ import annotations
import base64
import html
import io
import json
import os
import re
import urllib.parse as urlparse
import qrcode
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from api.core import csrf
from api.services.card_service import find_card_by_slug
from api.services.card_display import (
    DEFAULT_AVATAR,
    FEATURED_DEFAULT_COLOR,
    build_pix_emv,
    get_card_view_count,
    increment_card_view,
    normalize_external_url,
    profile_complete,
    resolve_photo,
    should_track_view,
    _absolute_asset_url,
    _request_host,
    _card_entry_path,
    _card_public_base,
    _card_share_url,
    _normalize_hex_color,
    _mix_hex_color,
    _pick_text_color,
    _rgb_string,
)
from api.services.custom_domain_service import find_card_by_custom_domain
from api.services.session_service import current_user_email
from api.repositories.sql_repository import SQLRepository
router = APIRouter(prefix="", tags=["cards"])
CSS_HREF = "/static/card.css"
BRAND_FOOTER = lambda html_doc: html_doc
SETTINGS = None
PUBLIC_BASE = ""
PUBLIC_BASE_HOST = ""
UPLOADS_DIR = ""
DEFAULT_LOCAL_ROOTS = {"localhost", "127.0.0.1", "::1"}
_sql_repo = SQLRepository()
def set_css_href(value: str) -> None:
    global CSS_HREF
    CSS_HREF = value or "/static/card.css"
def set_brand_footer(func):
    global BRAND_FOOTER
    BRAND_FOOTER = func or (lambda html_doc: html_doc)
def configure_environment(*, settings, public_base: str, public_base_host: str, uploads_dir: str) -> None:
    global SETTINGS, PUBLIC_BASE, PUBLIC_BASE_HOST, UPLOADS_DIR
    SETTINGS = settings
    PUBLIC_BASE = public_base or ""
    PUBLIC_BASE_HOST = (public_base_host or "").strip()
    UPLOADS_DIR = uploads_dir or ""


def _find_card(slug: str):
    return find_card_by_slug(slug)
def _templates(request: Request):
    tpl = getattr(getattr(request.app, "state", None), "templates", None)
    if tpl:
        return tpl
    raise RuntimeError("Templates nao configurados")
_FOOTER_SLOT = "<span id='footerActionSlot' class='footer-auth-slot'></span>"
_FOOTER_PLACEHOLDER = "{footer_action_html}"


def _apply_brand_footer(html_doc: str, action_html: str | None = None) -> str:
    footer_html = BRAND_FOOTER(html_doc) if BRAND_FOOTER else html_doc
    if _FOOTER_PLACEHOLDER in footer_html:
        replacement = action_html or ""
        return footer_html.replace(_FOOTER_PLACEHOLDER, replacement, 1)
    if _FOOTER_SLOT not in footer_html and "footer-auth-slot" not in footer_html:
        marker = "</footer>"
        if marker in footer_html:
            footer_html = footer_html.replace(marker, f"{_FOOTER_SLOT}{marker}", 1)
    if action_html:
        footer_html = footer_html.replace(
            _FOOTER_SLOT,
            f"<span id='footerActionSlot' class='footer-auth-slot'>{action_html}</span>",
            1,
        )
    return footer_html


def _footer_action_markup(*, is_owner: bool, slug: str, csrf_token_html: str = "") -> str:
    slug_safe = html.escape(slug)
    if is_owner:
        return (
            "<form method='post' action='/auth/logout' class='logout-inline' data-skip-global-loading='true'>"
            f"<input type='hidden' name='csrf_token' value='{csrf_token_html}'>"
            f"<input type='hidden' name='next' value='/{slug_safe}'>"
            "<button type='submit' class='muted link-btn' style='background:none;border:0;padding:0;margin:0;cursor:pointer'>Sair</button>"
            "</form>"
        )
    return "<a href='/login' class='muted'>Entrar</a>"


def _footer_action_context(
    request: Request | None,
    *,
    is_owner: bool,
    slug: str,
    csrf_token_value: str | None = None,
) -> tuple[str, str]:
    token_value = csrf_token_value or ""
    if is_owner and request and not token_value:
        token_value = csrf.ensure_csrf_token(request)
    csrf_token_html = html.escape(token_value) if token_value else ""
    action_html = _footer_action_markup(
        is_owner=is_owner,
        slug=slug,
        csrf_token_html=csrf_token_html,
    )
    return action_html, token_value
def visitor_public_card(
    prof: dict,
    slug: str,
    is_owner: bool = False,
    view_count: int = 0,
    card: dict | None = None,
    request: Request | None = None,
):
    footer_action_html, csrf_token_value = _footer_action_context(
        request,
        is_owner=is_owner,
        slug=slug,
    )
    raw_photo = (prof.get("photo_url", "") or "") if prof else ""
    raw_cover = (prof.get("cover_url", "") or "") if prof else ""
    photo_src = resolve_photo(raw_photo)
    photo = html.escape(photo_src) if photo_src else ""
    cover = html.escape(raw_cover) if raw_cover else ""
    wa_raw = (prof.get("whatsapp", "") or "").strip()
    wa_digits = "".join([c for c in wa_raw if c.isdigit()])
    email_pub = (prof.get("email_public", "") or "").strip()
    address_text = (prof.get("address", "") or "").strip() if prof else ""
    pix_key = (prof.get("pix_key", "") or "").strip()
    google_review_url = (prof.get("google_review_url", "") or "").strip()
    google_review_show = bool(prof.get("google_review_show", True))
    try:
        total_views = max(0, int(view_count))
    except (TypeError, ValueError):
        total_views = 0
    view_chip = ""
    if is_owner:
        formatted_views = f"{total_views:,}".replace(",", ".")
        view_chip = (
            "<div class='view-chip' title='Total de acessos de visitantes'>"
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true'><path fill='currentColor' d='M12 5c-5 0-9.27 3.11-11 7 1.73 3.89 6 7 11 7s9.27-3.11 11-7c-1.73-3.89-6-7-11-7zm0 11a4 4 0 1 1 0-8 4 4 0 0 1 0 8zm0-6a2 2 0 1 0 .001 4.001A2 2 0 0 0 12 10z'/></svg>"
            f"<span class='view-chip__count'>{formatted_views}</span>"
            "<span class='view-chip__label'>visualizações</span>"
            "</div>"
        )
    links_list = prof.get("links", []) or []
    def platform(label: str, href: str) -> str:
        s = f"{(label or '').lower()} {(href or '').lower()}"
        if "instagram" in s or s.strip().startswith("@"): return "instagram"
        if "linkedin" in s: return "linkedin"
        if "facebook" in s or "fb.com" in s: return "facebook"
        if "youtube" in s or "youtu.be" in s or s.strip().startswith("@"): return "youtube"
        if "tiktok" in s: return "tiktok"
        if "twitter" in s or "x.com" in s: return "twitter"
        if "github" in s: return "github"
        if "behance" in s: return "behance"
        if "dribbble" in s: return "dribbble"
        if (href or "").startswith("tel:"): return "phone"
        if (href or "").startswith("mailto:"): return "email"
        if ("site" in s or "website" in s or "pagina" in s): return "site"
        return "link"
    site_link = None
    other_links = []
    for item in links_list:
        label = item.get("label", "")
        href = item.get("href", "")
        plat = platform(label, href)
        # Avoid duplicate maps icon: if address in profile, skip map links in grid
        if address_text and href:
            _hl = (href or "").lower()
            if ("maps.google" in _hl) or ("goo.gl/maps" in _hl) or ("maps.app.goo.gl" in _hl) or ("waze.com" in _hl) or ("maps.apple.com" in _hl):
                continue
        if plat == "site" and site_link is None:
            site_link = (label, href)
        else:
            other_links.append((label, href, plat))
    share_url = _card_share_url(card, slug, request)
    card_base = _card_public_base(card, request)
    share_text = urlparse.quote_plus(f"Ola! Vim pelo seu cartao da Soomei.")
    share_base_message = "Este é o meu Cartão de Visita Digital"
    share_base_message_js = json.dumps(share_base_message)
    cover_block = (
        "<div class='card-cover'>"
        f"<img src='{cover}' alt='capa do cartão'>"
        "</div>"
        if cover
        else ""
    )
    actions = []
    if wa_digits:
        actions.append(f"<a class='btn action whatsapp' target='_blank' rel='noopener' href='https://wa.me/{wa_digits}?text={share_text}'>WhatsApp</a>")
    if site_link:
        _, href = site_link
        actions.append(f"<a class='btn action website' target='_blank' rel='noopener' href='{html.escape(href)}'>Site</a>")
    if email_pub:
        actions.append(f"<a class='btn action email' href='mailto:{html.escape(email_pub)}'>E-mail</a>")
    actions.append("<a class='btn action share' id='shareBtn' href='#'>Compartilhar</a>")
    if pix_key:
        actions.append(f"<a class='btn action pix' id='pixBtn' data-key='{html.escape(pix_key)}' href='#'>Copiar PIX</a>")
    # Engrenagem de edição discreta no canto superior direito (somente dono)
    owner_gear = (
        "<a class='edit-gear' href='/edit/"
        + html.escape(slug)
        + "' title='Editar' aria-label='Editar'>"
        + "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='18' height='18'>"
        + "<path fill='currentColor' d='M19.14 12.94c.04-.31.06-.63.06-.94s-.02-.63-.06-.94l2.03-1.58a.5.5 0 0 0 .12-.64l-1.92-3.32a.5.5 0 0 0-.6-.22l-2.39.96c-.5-.4-1.05-.73-1.63-.95l-.36-2.5A.5.5 0 0 0 13.9 2h-3.8a.5.5 0 0 0-.5.42l-.36 2.5c-.58.22-1.12.55-1.63.95l-2.39-.96a.5.5 0 0 0-.6.22L.7 7.84a.5.5 0 0 0 .12.64L2.85 10.06c-.04.31-.06.63-.06.94s.02.63.06.94L.82 13.52a.5.5 0 0 0-.12.64l1.92 3.32a.5.5 0 0 0 .6.22l2.39-.96c.5.4 1.05.73 1.63.95l.36 2.5a.5.5 0 0 0 .5.42h3.8a.5.5 0 0 0 .5-.42l.36-2.5c.58-.22 1.12-.55 1.63-.95l2.39.96a.5.5 0 0 0 .6-.22l1.92-3.32a.5.5 0 0 0-.12-.64l-2.03-1.58zM12 15a3 3 0 1 1 0-6 3 3 0 0 1 0 6z'/>"
        + "</svg>"
        + "</a>"
        if is_owner
        else ""
    )
    actions_html = "".join(actions)
    link_items = []
    for label, href, plat in other_links:
        cls = f"brand-{plat}"
        icon = ""
        if plat == "facebook":
            icon = (
                "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='16' height='16'>"
                "<path fill='currentColor' d='M22 12A10 10 0 1 0 10.5 21.9v-6.9H7.9v-3h2.6V9.2c0-2.6 1.6-4 3.9-4 1.1 0 2.2.2 2.2.2v2.5h-1.2c-1.2 0-1.6.8-1.6 1.6V12h2.8l-.4 3h-2.4v6.9A10 10 0 0 0 22 12z'/>"
                "</svg> "
            )
        elif plat == "linkedin":
            icon = (
                "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='16' height='16'>"
                "<path fill='currentColor' d='M4.98 3.5A2.5 2.5 0 1 1 0 3.5a2.5 2.5 0 0 1 4.98 0zM0 8h5v16H0V8zm7 0h4.8v2.2h.1c.7-1.3 2.5-2.7 5.1-2.7 5.4 0 6.4 3.6 6.4 8.3V24h-5v-8c0-1.9 0-4.4-2.7-4.4-2.7 0-3.1 2.1-3.1 4.3V24H7V8z'/>"
                "</svg> "
            )
        elif plat == "youtube":
            icon = (
                "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' width='16' height='16' aria-hidden='true'>"
                "<path fill='currentColor' d='M23.5 6.2c-.2-1.1-1.1-2-2.2-2.3C19.3 3.5 12 3.5 12 3.5s-7.3 0-9.3.4C1.6 4.2.7 5.1.5 6.2.1 8.4 0 10.2 0 12s.1 3.6.5 5.8c.2 1.1 1.1 2 2.2 2.3 2 .4 9.3.4 9.3.4s7.3 0 9.3-.4c1.1-.3 2-1.2 2.2-2.3.4-2.2.5-4 .5-5.8s-.1-3.6-.5-5.8zM9.8 15.5v-7l6 3.5-6 3.5z'/>"
                "</svg> "
            )
        elif plat == "instagram":
            icon = (
                "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='18' height='18'>"
                "<path fill='currentColor' d='M7 2C4.24 2 2 4.24 2 7v10c0 2.76 2.24 5 5 5h10c2.76 0 5-2.24 5-5V7c0-2.76-2.24-5-5-5H7zm0 2h10c1.66 0 3 1.34 3 3v10c0 1.66-1.34 3-3 3H7c-1.66 0-3-1.34-3-3V7c0-1.66 1.34-3 3-3zm11 1.5a1 1 0 100 2 1 1 0 000-2zM12 7a5 5 0 100 10 5 5 0 000-10z'/>"
                "</svg>"
            )
        text = html.escape(label or plat.title())
        link_items.append(
            f"<li><a class='link {cls}' href='{html.escape(href)}' target='_blank' rel='noopener'>{icon}{text}</a></li>"
        )
    links_grid_html = "".join(link_items)
    portfolio_raw = prof.get("portfolio_images") or []
    portfolio_images = []
    if isinstance(portfolio_raw, list):
        for item in portfolio_raw[:5]:
            val = (item or "").strip()
            if val:
                portfolio_images.append(html.escape(val))
    portfolio_enabled_flag = bool(prof.get("portfolio_enabled"))
    portfolio_section = ""
    if portfolio_enabled_flag and portfolio_images:
        slides_html = "".join(
            f"<div class='portfolio-slide{' is-active' if idx == 0 else ''}' style='--i:{idx};'>"
            f"<div class='portfolio-frame'><div class='portfolio-glow'></div><img src='{src}' alt='Portfolio {idx + 1}' loading='lazy'></div>"
            f"</div>"
            for idx, src in enumerate(portfolio_images)
        )
        dots_html = "".join(
            f"<button type='button' class='portfolio-dot{' is-active' if idx == 0 else ''}' data-index='{idx}' aria-label='Mostrar foto {idx + 1}' aria-current='{'true' if idx == 0 else 'false'}'></button>"
            for idx in range(len(portfolio_images))
        )
        plural = "s" if len(portfolio_images) != 1 else ""
        portfolio_section = f"""
        <section class='portfolio-showcase'>
          <div class='portfolio-head'>
            <div>
              <p class='section-kicker'>Portfólio</p>
            </div>
          </div>
          <div class='portfolio-carousel' data-total='{len(portfolio_images)}'>
            <div class='portfolio-ring' style='--total:{len(portfolio_images)};--active:0;--radius:420px;'>
              {slides_html}
            </div>
            <div class='portfolio-dots'>
              {dots_html}
            </div>
          </div>
        </section>
        """
    scripts = """

    <script>

    (function(){

      var __s = document.createElement('style');

      if (__s) {

        __s.textContent = '.is-hidden{display:none!important}';

        document.head.appendChild(__s);

      }

      function getShareData(){

        var pageUrl = window.location.href;

        var text = """ + share_base_message_js + """ + " " + pageUrl;

        return {url: pageUrl, text: text};

      }

      var shareBtn = document.getElementById('shareBtn');

      if (shareBtn) {

        shareBtn.addEventListener('click', function(e){

          e.preventDefault();

          var data = getShareData();

          if (navigator.share) {

            navigator.share({title: document.title, text: data.text}).catch(function(err){

              if (err && err.name === 'AbortError') { return; }

            });

            return;

          }

          if (navigator.clipboard && window.isSecureContext) {

            navigator.clipboard.writeText(data.url).then(function(){

              shareBtn.textContent = 'Link copiado';

              setTimeout(function(){ shareBtn.textContent = 'Compartilhar'; }, 1500);

            }).catch(function(){});

            return;

          }

          var ta = document.createElement('textarea');

          ta.value = data.url;

          ta.setAttribute('readonly','');

          ta.style.position = 'absolute';

          ta.style.left = '-9999px';

          document.body.appendChild(ta);

          ta.select();

          try {

            document.execCommand('copy');

            shareBtn.textContent = 'Link copiado';

            setTimeout(function(){ shareBtn.textContent = 'Compartilhar'; }, 1500);

          } catch (_e) {}

          document.body.removeChild(ta);

        });

      }

      var pixBtn = document.getElementById('pixBtn');

      if (pixBtn) {

        pixBtn.addEventListener('click', function(e){

          e.preventDefault();

          var key = pixBtn.getAttribute('data-key') || '';

          function fallbackCopy(){

            var ta = document.createElement('textarea');

            ta.value = key;

            ta.setAttribute('readonly','');

            ta.style.position='fixed';

            ta.style.top='0';

            ta.style.left='0';

            ta.style.opacity='0';

            document.body.appendChild(ta);

            ta.focus(); ta.select(); ta.setSelectionRange(0, ta.value.length);

            try {

              if (document.execCommand('copy')) {

                pixBtn.textContent = 'PIX copiado';

                setTimeout(function(){ pixBtn.textContent = 'Copiar PIX'; }, 1500);

              }

            } catch (_e) {}

            document.body.removeChild(ta);

          }

          if (navigator.clipboard && window.isSecureContext) {

            navigator.clipboard.writeText(key).then(function(){

              pixBtn.textContent = 'PIX copiado';

              setTimeout(function(){ pixBtn.textContent = 'Copiar PIX'; }, 1500);

            }).catch(fallbackCopy);

          } else {

            fallbackCopy();

          }

        });

      }

      (function(){

        var shareCardBtn = document.getElementById('shareCardBtn');

        if (!shareCardBtn) return;

        var shareBackdrop = document.getElementById('shareBackdrop');

        var sharePhone = document.getElementById('sharePhone');

        var shareSend = document.getElementById('shareSend');

        var shareCancel = document.getElementById('shareCancel');

        var shareClose = document.getElementById('shareClose');

        var shareError = document.getElementById('shareError');

        function isMobile(){

          return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent || '');

        }

        function toggleShareError(msg){

          if (!shareError) return;

          if (msg){

            shareError.textContent = msg;

            shareError.style.display = 'block';

          } else {

            shareError.textContent = '';

            shareError.style.display = 'none';

          }

        }

        function openShareModal(){

          if (!shareBackdrop) return;

          toggleShareError('');

          shareBackdrop.style.display = 'flex';

          shareBackdrop.classList.add('show');

          shareBackdrop.setAttribute('aria-hidden','false');

          if (sharePhone){

            sharePhone.focus();

            sharePhone.select();

          }

        }

        function closeShareModal(){

          if (!shareBackdrop) return;

          shareBackdrop.classList.remove('show');

          shareBackdrop.style.display = 'none';

          shareBackdrop.setAttribute('aria-hidden','true');

          if (sharePhone) sharePhone.value = '';

          toggleShareError('');

        }

        function sendViaModal(){

          if (!sharePhone) return;

          var digits = (sharePhone.value || '').replace(/\D/g,'');

          if (digits.length < 10){

            toggleShareError('Informe DDD + telefone com pelo menos 10 dígitos.');

            return;

          }

          var shareData = getShareData();

          var target = 'https://wa.me/' + digits + '?text=' + encodeURIComponent(shareData.text);

          window.open(target, '_blank');

          closeShareModal();

        }

        shareCardBtn.addEventListener('click', function(e){

          e.preventDefault();

          var shareData = getShareData();

          if (navigator.share && isMobile()){

            navigator.share({title: document.title, text: shareData.text}).catch(function(err){

              if (err && err.name === 'AbortError') { return; }

              window.location.href = 'https://wa.me/?text=' + encodeURIComponent(shareData.text);

            });

            return;

          }

          if (isMobile()){

            window.location.href = 'https://wa.me/?text=' + encodeURIComponent(shareData.text);

            return;

          }

          openShareModal();

        });

        if (shareSend) shareSend.addEventListener('click', function(e){ e.preventDefault(); sendViaModal(); });

        if (shareCancel) shareCancel.addEventListener('click', function(e){ e.preventDefault(); closeShareModal(); });

        if (shareClose) shareClose.addEventListener('click', function(e){ e.preventDefault(); closeShareModal(); });

        if (shareBackdrop){

          shareBackdrop.addEventListener('click', function(ev){

            if (ev.target === shareBackdrop){ closeShareModal(); }

          });

        }

        document.addEventListener('keydown', function(ev){

          if (ev.key === 'Escape'){ closeShareModal(); }

        });

      })();

    var editForm = document.getElementById('editForm');

    var loaderCtrl = window.soomeiLoader || null;

    if (editForm) {

      var saveBtn = document.getElementById('saveBtn');

      var loaderEl = document.getElementById('formLoading');

      var submitted = false;

      var originalBtnText = saveBtn ? saveBtn.textContent : '';

      var canAsyncSubmit = typeof window.fetch === 'function' && typeof window.FormData !== 'undefined';

      var showLoader = function(){

        if (loaderCtrl && loaderCtrl.show) {

          loaderCtrl.show();

        } else if (loaderEl) {

          loaderEl.classList.add('show');

          loaderEl.setAttribute('aria-hidden','false');

        }

      };

      var hideLoader = function(){

        if (loaderCtrl && loaderCtrl.hide) {

          loaderCtrl.hide();

        } else if (loaderEl) {

          loaderEl.classList.remove('show');

          loaderEl.setAttribute('aria-hidden','true');

        }

      };

      var resetSubmission = function(message){

        submitted = false;

        hideLoader();

        if (saveBtn) {

          saveBtn.disabled = false;

          saveBtn.textContent = originalBtnText || 'Salvar alteraçes';

        }

        if (message) {

          alert(message);

        }

      };

      editForm.addEventListener('submit', function(ev){

        if (submitted) { return; }

        if (typeof editForm.reportValidity === 'function') {

          if (!editForm.reportValidity()) { return; }

        } else if (typeof editForm.checkValidity === 'function' && !editForm.checkValidity()) {

          return;

        }

        ev.preventDefault();

        submitted = true;

        showLoader();

        if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Salvando...'; }

        if (canAsyncSubmit) {

          var formData = new FormData(editForm);

          fetch(editForm.action, {

            method: 'POST',

            body: formData,

            credentials: 'same-origin',

          }).then(function(response){

            if (response.type === 'opaqueredirect') {

              window.location.assign(editForm.action);

              return;

            }

            if (response.redirected) {

              window.location.assign(response.url);

              return;

            }

            if (response.ok) {

              return response.text().then(function(html){

                document.open('text/html','replace');

                document.write(html);

                document.close();

              });

            }

            return response.text().then(function(body){

              var clean = (body || '').replace(/<[^>]+>/g, '').trim();

              throw new Error(clean || 'Erro ao salvar. Tente novamente.');

            });

          }).catch(function(err){

            console.error('Falha ao salvar edicao', err);

            var msg = (err && err.message) || 'Não foi possível salvar. Verifique sua conexão e tente novamente.';

            resetSubmission(msg);

          });

          return;

        }

        var submitAfterPaint = function(){

          if (window.requestAnimationFrame) {

            window.requestAnimationFrame(function(){

              window.requestAnimationFrame(function(){ editForm.submit(); });

            });

          } else {

            setTimeout(function(){ editForm.submit(); }, 16);

          }

        };

        submitAfterPaint();

      });

    }

    })();

    (function(){
      var ring = document.querySelector('.portfolio-ring');
      if (!ring) { return; }
      var slides = ring.querySelectorAll('.portfolio-slide');
      if (!slides.length) { return; }
      var dots = document.querySelectorAll('.portfolio-dot');
      var carousel = document.querySelector('.portfolio-carousel');
      var total = slides.length;
      var active = 0;
      var timer = null;
      var prefersReduce = false;
      var startX = null;
      var lastDx = 0;
      var moved = false;
      try {
        prefersReduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      } catch (_e) {}
      function setActive(idx){
        active = ((idx % total) + total) % total;
        ring.style.setProperty('--active', active);
        slides.forEach(function(slide, i){
          slide.classList.toggle('is-active', i === active);
        });
        dots.forEach(function(dot, i){
          var isCurrent = i === active;
          dot.classList.toggle('is-active', isCurrent);
          dot.setAttribute('aria-current', isCurrent ? 'true' : 'false');
        });
      }
      function schedule(){
        if (timer){ clearInterval(timer); }
        if (prefersReduce){ return; }
        timer = setInterval(function(){
          setActive(active + 1);
        }, 4200);
      }
      dots.forEach(function(dot){
        dot.addEventListener('click', function(ev){
          ev.preventDefault();
          var idx = parseInt(dot.getAttribute('data-index') || '0', 10) || 0;
          setActive(idx);
          schedule();
        });
      });
      if (carousel){
        carousel.addEventListener('pointerdown', function(ev){
          startX = ev.clientX;
          moved = false;
          lastDx = 0;
          if (timer){ clearInterval(timer); }
          try { carousel.setPointerCapture(ev.pointerId); } catch(_e){}
        });
        carousel.addEventListener('pointermove', function(ev){
          if (startX === null) return;
          var dx = ev.clientX - startX;
          lastDx = dx;
          if (Math.abs(dx) > 8){ moved = true; }
        });
        carousel.addEventListener('pointerup', function(ev){
          if (startX === null) return;
          if (Math.abs(lastDx) > 40){
            var step = lastDx > 0 ? -1 : 1;
            setActive(active + step);
          } else if (!moved){
            setActive(active + 1);
          }
          startX = null;
          schedule();
        });
        carousel.addEventListener('mouseenter', function(){ if (timer) { clearInterval(timer); } });
        carousel.addEventListener('mouseleave', function(){ schedule(); });
        carousel.addEventListener('wheel', function(ev){
          ev.preventDefault();
          var delta = ev.deltaY || ev.deltaX || 0;
          if (delta === 0) return;
          setActive(active + (delta > 0 ? 1 : -1));
          schedule();
        }, { passive: false });
      }
      setActive(0);
      schedule();
    })();

    </script>

    """
    # Cor de fundo suavizada para o card público
    theme_base = (prof.get("theme_color", "#000000") or "#000000") if prof else "#000000"
    if not re.fullmatch(r"#([0-9a-fA-F]{6})", theme_base or ""):
        theme_base = "#000000"
    bg_hex = theme_base + "30"
    # vCard offline QR pré-gerado para subseção inline
    try:
        off_full_name = (prof.get("full_name", "") or slug) if prof else slug
        off_title = prof.get("title", "") if prof else ""
        off_email = (prof.get("email_public", "") or "") if prof else ""
        off_wa_raw = (prof.get("whatsapp", "") or "") if prof else ""
        off_wa_digits = "".join([c for c in off_wa_raw if c.isdigit()])
        off_share_url = _card_share_url(card, slug, request)
        # PHOTO inline (base64, downscaled for QR). Em offline, omite em caso de falha.
        photo_line_off = ""
        off_photo_url = (prof.get("photo_url", "") or "").strip() if prof else ""
        if off_photo_url:
            try:
                fname = os.path.basename(off_photo_url.split("?", 1)[0])
                local_path = os.path.join(UPLOADS_DIR, fname)
                from PIL import Image  # type: ignore
                im = Image.open(local_path).convert("RGB")
                im.thumbnail((160,160))
                _tmp = io.BytesIO(); im.save(_tmp, format='JPEG', quality=70); _tmp.seek(0)
                raw = _tmp.read()
                if raw:
                    data_b64 = base64.b64encode(raw).decode('ascii')
                    chunks = [data_b64[i:i+76] for i in range(0, len(data_b64), 76)]
                    folded = "\r\n ".join(chunks)
                    photo_line_off = f"PHOTO;ENCODING=b;TYPE=JPEG:{folded}"
            except Exception:
                photo_line_off = ""
        def _build_vcard(include_photo: bool) -> str:
            parts = [
                "BEGIN:VCARD",
                "VERSION:3.0",
                f"FN:{off_full_name}",
            ]
            if include_photo and photo_line_off:
                parts.append(photo_line_off)
            if off_title:
                parts.append(f"TITLE:{off_title}")
            if off_wa_digits:
                parts.append(f"TEL;TYPE=CELL:{off_wa_digits}")
            if off_email:
                parts.append(f"EMAIL:{off_email}")
            parts.append(f"URL:{off_share_url}")
            parts.append("END:VCARD")
            return "\r\n".join(parts)
        def _qr_data_url(payload: str) -> str:
            buf = io.BytesIO()
            qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
            qr.add_data(payload)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            img.save(buf, format='PNG')
            buf.seek(0)
            return "data:image/png;base64," + base64.b64encode(buf.read()).decode('ascii')
        # Tenta com foto; se falhar por tamanho, tenta sem foto; depois fallback para SVG do basico
        try:
            _vcard1 = _build_vcard(include_photo=True)
            offline_data_url = _qr_data_url(_vcard1)
        except Exception:
            try:
                _vcard2 = _build_vcard(include_photo=False)
                offline_data_url = _qr_data_url(_vcard2)
            except Exception:
                try:
                    import qrcode.image.svg as qsvg  # type: ignore
                    _buf2 = io.BytesIO()
                    qrcode.make(_build_vcard(include_photo=False), image_factory=qsvg.SvgImage).save(_buf2)
                    offline_data_url = "data:image/svg+xml;base64," + base64.b64encode(_buf2.getvalue()).decode('ascii')
                except Exception:
                    offline_data_url = ""
    except Exception:
        offline_data_url = ""
    # Normaliza URL do site para garantir esquema (https://) quando ausente
    site_href = normalize_external_url(prof.get("site_url", ""))
    # Botão destaque configurável
    featured_label = (prof.get("featured_label", "") or "").strip()
    featured_url = normalize_external_url(prof.get("featured_url", ""))
    featured_enabled = bool(prof.get("featured_enabled", True))
    featured_color = _normalize_hex_color(prof.get("featured_color"), FEATURED_DEFAULT_COLOR)
    feat_start = _mix_hex_color(featured_color, 0.25)
    feat_end = _mix_hex_color(featured_color, -0.15)
    feat_shadow_rgb = _rgb_string(featured_color)
    feat_text_color = _pick_text_color(featured_color)
    featured_block = ""
    if featured_label and featured_url and featured_enabled:
        featured_style = (
            f"--featured-start:{feat_start};"
            f"--featured-end:{feat_end};"
            f"--featured-shadow-rgb:{feat_shadow_rgb};"
            f"--featured-text:{feat_text_color};"
        )
        featured_block = f"""
        <a class='featured-cta' href='{html.escape(featured_url)}' target='_blank' rel='noopener' data-cta='featured-{html.escape(slug)}' style='{html.escape(featured_style)}'>
          <div class='featured-cta__text'>
            <span class='featured-cta__eyebrow'>Em destaque</span>
            <span class='featured-cta__label'>{html.escape(featured_label)}</span>
            <span class='featured-cta__hint'>Toque para continuar</span>
          </div>
          <span class='featured-cta__icon' aria-hidden='true'>
            <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' width='22' height='22' fill='none' stroke='currentColor' stroke-width='2'>
              <path d='M5 12h14'></path>
              <path d='M13 6l6 6-6 6'></path>
            </svg>
          </span>
        </a>
        """
    # Endereço (opcional) para link do Maps
    address_text = (prof.get("address", "") or "").strip() if prof else ""
    if address_text:
        maps_q = urlparse.quote(address_text, safe="")
        maps_href = f"https://www.google.com/maps/search/?api=1&query={maps_q}"
    else:
        maps_href = ""
    og_title = f"{prof.get('full_name','')} | Soomei Card".strip(" ?") if prof else "Soomei Card"
    og_desc = prof.get("title") if prof and prof.get("title") else "Clique para me chamar no WhatsApp e salvar meu contato."
    primary_image = raw_photo or raw_cover or DEFAULT_AVATAR
    secondary_image = raw_cover if (raw_cover and raw_cover != primary_image) else ""
    og_image_url = html.escape(_absolute_asset_url(primary_image, base=card_base))
    og_image_second = html.escape(_absolute_asset_url(secondary_image, base=card_base)) if secondary_image else ""
    html_doc = f"""<!doctype html><html lang='pt-br'><head>
    <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <link rel='stylesheet' href='{CSS_HREF}'><title>Soomei | {html.escape(prof.get('full_name',''))}</title>
    <meta property='og:type' content='website'>
    <meta property='og:url' content='{html.escape(share_url)}'>
    <meta property='og:title' content='{html.escape(og_title)}'>
    <meta property='og:description' content='{html.escape(og_desc)}'>
    <meta property='og:image' content='{og_image_url}'>
    {f"<meta property='og:image' content='{og_image_second}'>" if og_image_second else ""}
    <meta property='og:image:width' content='1200'>
    <meta property='og:image:height' content='630'>
    <meta name='twitter:card' content='summary_large_image'>
    <meta name='twitter:title' content='{html.escape(og_title)}'>
    <meta name='twitter:description' content='{html.escape(og_desc)}'>
    <meta name='twitter:image' content='{og_image_url}'>
    </head><body>
    <main class='wrap'>
      <section class='card card-public carbon card-center' style='background-color: {html.escape(bg_hex)}'>
        {owner_gear}
        {cover_block}
        <header class='card-header'>
          {f"<img class='avatar avatar-small' src='{photo}' alt='foto'>" if photo else ""}
          <h1 class='name'>{html.escape(prof.get('full_name',''))}</h1>
          <p class='title'>{html.escape(prof.get('title',''))}</p>
          {view_chip}
        </header>
          {f"""
            <div class='google-review'>
              <a href='{html.escape(google_review_url)}' target='_blank' rel='noopener' class='btn-google-review'>
                <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48' width='18' height='18' class='g-icon'>
                  <path fill='#fff' d='M24 9.5c3.94 0 7.06 1.7 9.18 3.12l6.77-6.77C36.26 2.52 30.62 0 24 0 14.5 0 6.36 5.4 2.4 13.22l7.9 6.14C12.12 13.32 17.63 9.5 24 9.5z'/>
                  <path fill='#fff' d='M46.5 24.5c0-1.6-.14-3.1-.4-4.5H24v9h12.7c-.6 3.2-2.4 5.9-5.1 7.7l7.9 6.1C43.8 38.8 46.5 32.1 46.5 24.5z'/>
                  <path fill='#fff' d='M10.3 28.36A14.5 14.5 0 0 1 9.5 24c0-1.53.26-3.02.74-4.36l-7.9-6.14A23.74 23.74 0 0 0 0 24c0 3.83.93 7.46 2.54 10.64l7.76-6.28z'/>
                  <path fill='#fff' d='M24 48c6.48 0 11.92-2.13 15.89-5.8l-7.9-6.14C29.8 37.75 27.06 38.5 24 38.5c-6.37 0-11.88-3.82-14.7-9.86l-7.9 6.14C6.36 42.6 14.5 48 24 48z'/>
                </svg>
                ★★★★★ Avaliar no Google
              </a>
            </div>
          """ if google_review_url and google_review_show else ""}
          <div class='section-divider'></div>
          <div class='quick-actions qa4'>
          <div class='qa-item'>
            {(
              f"""
              <a class='icon-btn brand-wa' href='https://wa.me/{wa_digits}?text={share_text}' target='_blank' rel='noopener' title='WhatsApp' aria-label='WhatsApp'>
                <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='18' height='18'>
                  <path fill='currentColor' d='M20.52 3.48A11.86 11.86 0 0 0 12.02 0C5.39 0 .04 5.35.04 11.98c0 2.11.56 4.16 1.62 5.98L0 24l6.2-1.62a11.96 11.96 0 0 0 5.82 1.49h0c6.63 0 12.02-5.35 12.02-11.98 0-3.21-1.25-6.23-3.52-8.41ZM12.02 22.1h0c-1.9 0-3.76-.5-5.39-1.44l-.39-.23-3.68.96.98-3.59-.25-.37A9.77 9.77 0 0 1 2 11.98C2 6.48 6.52 2 12.02 2c2.62 0 5.08 1.02 6.93 2.86A9.71 9.71 0 0 1 22.06 12c0 5.5-4.52 10.1-10.04 10.1Zm5.53-7.49c-.3-.15-1.78-.88-2.05-.98-.27-.1-.47-.15-.68.15-.2.3-.78.98-.96 1.18-.18.2-.36.22-.66.07-.3-.15-1.27-.47-2.42-1.5-.9-.8-1.5-1.78-1.68-2.08-.18-.3-.02-.46.13-.61.13-.13.3-.34.45-.51.15-.17.2-.3.3-.5.1-.2.05-.37-.03-.52-.08-.15-.68-1.63-.93-2.23-.25-.6-.5-.52-.68-.53l-.58-.01c-.2 0-.52.08-.8.37-.27.3-1.05 1.03-1.05 2.5s1.07 2.9 1.23 3.1c.15.2 2.1 3.2 5.07 4.48.71.31 1.27.5 1.7.64.72.23 1.37.2 1.88.12.57-.08 1.78-.73 2.03-1.44.25-.7.25-1.3.18-1.43-.07-.13-.27-.2-.57-.35Z'/>
                </svg>
              </a>
              <div class='qa-label'>WhatsApp</div>
              """
            ) if wa_digits else (
              """

              <div class='icon-btn disabled' title='WhatsApp indisponvel' aria-disabled='true'>

                <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='18' height='18'>

                  <path fill='currentColor' d='M20.52 3.48A11.86 11.86 0 0 0 12.02 0C5.39 0 .04 5.35.04 11.98c0 2.11.56 4.16 1.62 5.98L0 24l6.2-1.62a11.96 11.96 0 0 0 5.82 1.49h0c6.63 0 12.02-5.35 12.02-11.98 0-3.21-1.25-6.23-3.52-8.41ZM12.02 22.1h0c-1.9 0-3.76-.5-5.39-1.44l-.39-.23-3.68.96.98-3.59-.25-.37A9.77 9.77 0 0 1 2 11.98C2 6.48 6.52 2 12.02 2c2.62 0 5.08 1.02 6.93 2.86A9.71 9.71 0 0 1 22.06 12c0 5.5-4.52 10.1-10.04 10.1Zm5.53-7.49c-.3-.15-1.78-.88-2.05-.98-.27-.1-.47-.15-.68.15-.2.3-.78.98-.96 1.18-.18.2-.36.22-.66.07-.3-.15-1.27-.47-2.42-1.5-.9-.8-1.5-1.78-1.68-2.08-.18-.3-.02-.46.13-.61.13-.13.3-.34.45-.51.15-.17.2-.3.3-.5.1-.2.05-.37-.03-.52-.08-.15-.68-1.63-.93-2.23-.25-.6-.5-.52-.68-.53l-.58-.01c-.2 0-.52.08-.8.37-.27.3-1.05 1.03-1.05 2.5s1.07 2.9 1.23 3.1c.15.2 2.1 3.2 5.07 4.48.71.31 1.27.5 1.7.64.72.23 1.37.2 1.88.12.57-.08 1.78-.73 2.03-1.44.25-.7.25-1.3.18-1.43-.07-.13-.27-.2-.57-.35Z'/>

                </svg>

              </div>

              <div class='qa-label'>WhatsApp</div>

              """
            )}
          </div>
          <div class='qa-item'>
            <a class='icon-btn elevated' href='/v/{html.escape(slug)}.vcf' title='Salvar contato' aria-label='Salvar contato'>
              <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='18' height='18'>
                <rect x='3' y='4' width='18' height='16' rx='2' ry='2' fill='none' stroke='currentColor' stroke-width='2'/>
                <circle cx='9' cy='10' r='2' fill='currentColor'/>
                <path d='M3 16c2.5-2 5-3 6-3s3.5 1 6 3' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round'/>
              </svg>
            </a>
            <div class='qa-label'>Salvar contato</div>
          </div>
          <div class='qa-item'>
            <a class='icon-btn' id='offlineBtn' href='javascript:void(0)' title='Modo Offline' aria-label='Modo Offline' >
              <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='18' height='18'>
                <rect x='3' y='3' width='6' height='6' fill='none' stroke='currentColor' stroke-width='2'/>
                <rect x='15' y='3' width='6' height='6' fill='none' stroke='currentColor' stroke-width='2'/>
                <rect x='3' y='15' width='6' height='6' fill='none' stroke='currentColor' stroke-width='2'/>
                <path d='M15 11h2v2h2v2h-4v-4zm-2 6h2v2h-2v-2zm6-6h2v2h-2v-2z' fill='currentColor'/>
              </svg>
            </a>
            <div class='qa-label'>Modo Offline</div>
          </div>
          <div class='qa-item'>
            <button type="button" class="icon-btn brand-share" id="shareCardBtn"
                    title="Compartilhar" aria-label="Compartilhar este cartão">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
                  width="18" height="18" aria-hidden="true" focusable="false">
                <!-- seta para cima -->
                <path d="M12 4v10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                <path d="M8.5 7.5L12 4l3.5 3.5" fill="none" stroke="currentColor" stroke-width="2"
                      stroke-linecap="round" stroke-linejoin="round"/>
                <!-- quadrado (caixa) -->
                <rect x="4" y="10" width="16" height="10" rx="2" ry="2"
                      fill="none" stroke="currentColor" stroke-width="2"/>
              </svg>
            </button>
            <div class='qa-label'>Compartilhar cartão</div>
          </div>
        </div>
        {featured_block}
        <div class='modal-backdrop' id='shareBackdrop' role='dialog' aria-modal='true' aria-hidden='true' style='display:none'>
          <div class='modal'>
            <header>
              <h2>Enviar por WhatsApp</h2>
              <button class='close' type='button' id='shareClose' aria-label='Fechar' title='Fechar'>&#10005;</button>
            </header>
            <div>
              <label>Telefone com DDD</label>
              <input id='sharePhone' type='tel' inputmode='tel' placeholder='11999990000' style='width:100%;margin:8px 0;padding:10px;border-radius:10px;border:1px solid #2a2a2a;background:#0b0b0c;color:#eaeaea'>
              <p class='muted' style='font-size:12px;margin:4px 0 12px'>Digite apenas números (DDD+telefone). Usaremos o WhatsApp Web para enviar.</p>
              <div style='display:flex;gap:10px;justify-content:flex-end'>
                <button type='button' class='btn ghost' id='shareCancel'>Cancelar</button>
                <button type='button' class='btn' id='shareSend'>Enviar</button>
              </div>
              <div id='shareError' class='banner bad' style='display:none;margin-top:10px'></div>
            </div>
          </div>
        </div>
        <div id='offlineSection' class='is-hidden' style='margin:12px 0 0'>
          <div class='card' style='background:transparent;border:1px solid #242427;border-radius:12px;padding:16px;text-align:center'>
            <h3 class='page-title' style='margin:0 0 8px'>Modo Offline</h3>
            <p>Modo Offline permite salvar o seu contato diretamente na agenda do cliente, sem utilizar a internet. Basta acessar o QR Code com a camera do celular:</p>
            <div style='margin:12px 0'>
              <img src='{offline_data_url}' alt='QR Offline' style='width:220px;height:220px;image-rendering:pixelated;background:#fff;padding:8px;border-radius:8px'>
            </div>
            <p class='muted' style='font-size:11px'>Dica: Tire um print dessa tela e marque-a como favorita em sua galeria de fotos, para facilitar o acesso em caso de falta de internet.</p>
            <p class='muted' style='font-size:11px'>Atencao: Nao e recomendado imprimir este codigo em placas ou cartao, pois ele nao sera atualizado online. Utilize o codigo QRCode Online para impressao.</p>
          </div>
        </div><div class='section-divider'></div>
        {(
          "<div class='quick-actions'>" +
          "".join([
            (
              f"<div class='qa-item'>"
              f"<a class='icon-btn brand-{plat}' href='{html.escape(href)}' {'target=\'_blank\' rel=\'noopener\'' if (href.startswith('http')) else ''} title='{html.escape(label or plat.title())}' aria-label='{html.escape(label or plat.title())}'>"
              f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='18' height='18'>"
              f"{(
                  '<path fill=\"currentColor\" d=\"M22 12A10 10 0 1 0 10.5 21.9v-6.9H7.9v-3h2.6V9.2c0-2.6 1.6-4 3.9-4 1.1 0 2.2.2 2.2.2v2.5h-1.2c-1.2 0-1.6.8-1.6 1.6V12h2.8l-.4 3h-2.4v6.9A10 10 0 0 0 22 12z\"/>'
                  if plat == 'facebook' else (
                    '<path fill=\"currentColor\" d=\"M4.98 3.5A2.5 2.5 0 1 1 0 3.5a2.5 2.5 0 0 1 4.98 0zM0 8h5v16H0V8zm7 0h4.8v2.2h.1c.7-1.3 2.5-2.7 5.1-2.7 5.4 0 6.4 3.6 6.4 8.3V24h-5v-8c0-1.9 0-4.4-2.7-4.4-2.7 0-3.1 2.1-3.1 4.3V24H7V8z\"/>'
                    if plat == 'linkedin' else (
                    '<rect x=\"3\" y=\"3\" width=\"18\" height=\"18\" rx=\"5\" ry=\"5\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"/>'
                    '<circle cx=\"12\" cy=\"12\" r=\"4\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"/>'
                    '<circle cx=\"17.5\" cy=\"6.5\" r=\"1.5\" fill=\"currentColor\"/>'
                    if plat == 'instagram' else (
                    '<path fill=\"currentColor\" d=\"M23.5 6.2c-.2-1.1-1.1-2-2.2-2.3C19.3 3.5 12 3.5 12 3.5s-7.3 0-9.3.4C1.6 4.2.7 5.1.5 6.2.1 8.4 0 10.2 0 12s.1 3.6.5 5.8c.2 1.1 1.1 2 2.2 2.3 2 .4 9.3.4 9.3.4s7.3 0 9.3-.4c1.1-.3 2-1.2 2.2-2.3.4-2.2.5-4 .5-5.8s-.1-3.6-.5-5.8zM9.8 15.5v-7l6 3.5-6 3.5z\"/>'
                    if plat == 'youtube' else
                    '<circle cx=\"12\" cy=\"12\" r=\"9\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"/>'
                    ))
                  )
                )}"
              f"</svg>"
              f"</a>"
              f"<div class='qa-label'>{html.escape(label or plat.title())}</div>"
              f"</div>"
            ) for (label, href, plat) in other_links[:4]
          ]) +
          "</div>"
        ) if (len(other_links) > 0) else ""}
        {portfolio_section}
        <div class='fixed-actions'>
          {(
            f"<a class='btn fixed website' target='_blank' rel='noopener' href='{html.escape(site_href)}'>"
            f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='16' height='16'>"
            f"<circle cx='12' cy='12' r='10' stroke='currentColor' stroke-width='2' fill='none'/><path d='M2 12h20M12 2c3 3 3 19 0 20M12 2c-3 3-3 19 0 20' stroke='currentColor' stroke-width='2' fill='none'/></svg> "
            f"Site</a>"
          ) if site_href else (
            "<span class='btn fixed website disabled' role='button' aria-disabled='true' tabindex='-1'>"
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='16' height='16'><circle cx='12' cy='12' r='10' stroke='currentColor' stroke-width='2' fill='none'/><path d='M2 12h20M12 2c3 3 3 19 0 20M12 2c-3 3-3 19 0 20' stroke='currentColor' stroke-width='2' fill='none'/></svg> Site</span>"
          )}
          {(
            f"<a class='btn fixed email' href='mailto:{html.escape(email_pub)}'>"
            f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='16' height='16'><path fill='currentColor' d='M4 6h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1zm8 6 9-6H3l9 6zm0 2L3 8v9h18V8l-9 6z'/></svg> "
            f"E-mail</a>"
          ) if email_pub else (
            "<span class='btn fixed email disabled' role='button' aria-disabled='true' tabindex='-1'>"
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='16' height='16'><path fill='currentColor' d='M4 6h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1zm8 6 9-6H3l9 6zm0 2L3 8v9h18V8l-9 6z'/></svg> E-mail</span>"
          )}
          {(
            f"<a class='btn fixed maps' id='mapsBtn' target='_blank' rel='noopener' href='{html.escape(maps_href)}'>"
            f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='16' height='16'>"
            f"<path fill='currentColor' d='M12 2C8.69 2 6 4.69 6 8c0 4.5 6 12 6 12s6-7.5 6-12c0-3.31-2.69-6-6-6zm0 8a2 2 0 110-4 2 2 0 010 4z'/>"
            f"</svg> "
            f"Endere\u00e7o</a>"
          ) if maps_href else ('')}
          {(
            f"<a class='btn fixed pix' id='payPixBtn' href='/{html.escape(slug)}?pix=amount'>"
            f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='16' height='16'>"
            f"<path fill='currentColor' d='M3 3h6v6H3V3zm2 2v2h2V5H5zm10-2h6v6h-6V3zm2 2v2h2V5h-2zM3 15h6v6H3v-6zm2 2v2h2v-2H5zm10 0h2v2h2v2h-4v-4zm0-4h2v2h-2v-2zm4 0h2v2h-2v-2z'/></svg> "
            f"Pagamento Pix</a>"
          ) if pix_key else (
            "<span class='btn fixed pix disabled' role='button' aria-disabled='true' tabindex='-1'>"
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='16' height='16'><path fill='currentColor' d='M3 3h6v6H3V3zm2 2v2h2V5H5zm10-2h6v6h-6V3zm2 2v2h2V5h-2zM3 15h6v6H3v-6zm2 2v2h2v-2H5zm10 0h2v2h2v2h-4v-4zm0-4h2v2h-2v-2zm4 0h2v2h-2v-2z'/></svg> Pagamento Pix</span>"
          )}
        </div>
      </section>
      {scripts}
      <script>
      (function(){{
        var off = document.getElementById('offlineBtn');
        var sec = document.getElementById('offlineSection');
        if (!off || !sec) return;
        off.setAttribute('aria-controls', 'offlineSection');
        off.setAttribute('aria-expanded', 'false');
        function isVisible(el){{
          var cs = window.getComputedStyle(el);
          return cs.display !== 'none';
        }}
        off.addEventListener('click', function(e){{
          e.preventDefault();
          var willHide = isVisible(sec);
          sec.classList.toggle('is-hidden', willHide);
          off.setAttribute('aria-expanded', (!willHide).toString());
          if (!willHide) {{
            try {{ sec.scrollIntoView({{ behavior: 'smooth', block: 'start' }}); }} catch(_e){{}}
          }}
        }});
      }})();
      </script>
    </main></body></html>"""
    response = HTMLResponse(_apply_brand_footer(html_doc, footer_action_html))
    if request and csrf_token_value:
        csrf.set_csrf_cookie(response, csrf_token_value)
    return response
@router.get("/u/{slug}", response_class=HTMLResponse)
def public_card(slug: str, request: Request):
    db, uid, card = _find_card(slug)
    if not card:
        raise HTTPException(404, "Cartao nao encontrado")
    templates = _templates(request)
    owner = card.get("user", "")
    prof = _sql_repo.get_profile(owner) or {}
    who = current_user_email(request)
    is_owner = bool(owner and who == owner)
    if is_owner:
        view_count = get_card_view_count(uid)
    else:
        view_count = increment_card_view(uid) if should_track_view(request, slug) else get_card_view_count(uid)
    return visitor_public_card(prof, slug, is_owner, view_count, card=card, request=request)
@router.get("/q/{slug}.png")
def qr(slug: str, request: Request):
    db, uid, card = _find_card(slug)
    if not card:
        raise HTTPException(404, "Cartao nao encontrado")
    slug_value = card.get("vanity") or slug
    share_url = _card_share_url(card, slug_value, request)
    img = qrcode.make(share_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")
@router.get("/v/{slug}.vcf")
def vcard(slug: str, request: Request):
    db, uid, card = _find_card(slug)
    if not card:
        raise HTTPException(404, "Cartao nao encontrado")
    prof = _sql_repo.get_profile(card.get("user", "")) or {}
    name = prof.get("full_name", "")
    tel = prof.get("whatsapp", "")
    email = prof.get("email_public", "")
    slug_value = card.get("vanity") or slug
    url = _card_share_url(card, slug_value, request)
    card_base = _card_public_base(card, request)
    # Photo handling: embed as base64 (preferred), fallback to URI, with line folding
    photo_line = None
    photo_url = (prof.get("photo_url", "") or "").strip()
    if photo_url:
        try:
            fname = os.path.basename(photo_url.split("?", 1)[0])
            local_path = os.path.join(UPLOADS_DIR, fname)
            with open(local_path, "rb") as fh:
                data = fh.read()
            if data:
                ext = (os.path.splitext(fname)[1] or "").lower()
                typ = "JPEG" if ext in (".jpg", ".jpeg") else ("PNG" if ext == ".png" else "JPEG")
                b64 = base64.b64encode(data).decode("ascii")
                # fold base64 to 76 chars per line with CRLF + space continuation
                chunks = [b64[i:i+76] for i in range(0, len(b64), 76)]
                folded = "\r\n ".join(chunks)
                photo_line = f"PHOTO;ENCODING=b;TYPE={typ}:{folded}"
        except Exception:
            abs_url = photo_url
            if abs_url.startswith("/"):
                abs_url = f"{card_base}{photo_url}"
            photo_line = f"PHOTO;VALUE=URI:{abs_url}"
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"N:{name};;;;",
        f"FN:{name}",
    ]
    if photo_line:
        lines.append(photo_line)
    lines.extend([
        "ORG:Soomei",
        f"TITLE:{prof.get('title','')}",
        f"TEL;TYPE=CELL:{tel}",
        f"EMAIL;TYPE=INTERNET:{email}",
        f"URL:{url}",
        "END:VCARD",
    ])
    vcf = "\r\n".join(lines) + "\r\n"
    return Response(vcf, media_type="text/vcard; charset=utf-8", headers={
        "Content-Disposition": f"attachment; filename=\"{slug}.vcf\""
    })
def _serve_slug(slug: str, request: Request, prefetched: tuple[dict, str, dict] | None = None):
    if prefetched:
        db, uid, card = prefetched
    else:
        db, uid, card = _find_card(slug)
    if card and card.get("vanity") and slug != card.get("vanity"):
        return RedirectResponse(f"/{html.escape(card.get('vanity'))}", status_code=302)
    if not card:
        return RedirectResponse("/invalid", status_code=302)
    if not card.get("status") or card.get("status") == "pending":
        return RedirectResponse(f"/onboard/{html.escape(uid)}", status_code=302)
    if card.get("status") == "blocked":
        return RedirectResponse("/blocked", status_code=302)
    templates = _templates(request)
    owner = card.get("user", "")
    prof = _sql_repo.get_profile(owner) or {}
    who = current_user_email(request)
    is_owner = bool(owner and who == owner)
    slug = (card.get("vanity") or slug or uid)
    entry_path = _card_entry_path(card, slug)
    card_base = _card_public_base(card, request)
    offline = request.query_params.get("offline", "")
    if offline:
        full_name = (prof.get("full_name", "") or slug) if prof else slug
        title = prof.get("title", "") if prof else ""
        email_pub = (prof.get("email_public", "") or "") if prof else ""
        wa_raw = (prof.get("whatsapp", "") or "") if prof else ""
        wa_digits = "".join([c for c in wa_raw if c.isdigit()])
        share_url = _card_share_url(card, slug, request)
        photo_line = None
        photo_url = (prof.get("photo_url", "") or "").strip() if prof else ""
        if photo_url:
            try:
                fname = os.path.basename(photo_url.split("?", 1)[0])
                local_path = os.path.join(UPLOADS_DIR, fname)
                data_b64 = None
                try:
                    from PIL import Image  # type: ignore
                    im = Image.open(local_path).convert("RGB")
                    im.thumbnail((160, 160))
                    _tmp = io.BytesIO()
                    im.save(_tmp, format="JPEG", quality=70)
                    _tmp.seek(0)
                    raw = _tmp.read()
                    data_b64 = base64.b64encode(raw).decode("ascii") if raw else None
                    typ = "JPEG"
                except Exception:
                    with open(local_path, "rb") as fh:
                        raw = fh.read()
                    if raw:
                        ext = (os.path.splitext(fname)[1] or "").lower()
                        typ = "JPEG" if ext in (".jpg", ".jpeg") else ("PNG" if ext == ".png" else "JPEG")
                        data_b64 = base64.b64encode(raw).decode("ascii")
                if data_b64:
                    chunks = [data_b64[i : i + 76] for i in range(0, len(data_b64), 76)]
                    folded = "\r\n ".join(chunks)
                    photo_line = f"PHOTO;ENCODING=b;TYPE={typ}:{folded}"
            except Exception:
                abs_url = photo_url
                if abs_url.startswith("/"):
                    abs_url = f"{card_base}{photo_url}"
                photo_line = f"PHOTO;VALUE=URI:{abs_url}"
        def _build_off(include_photo: bool) -> str:
            parts = [
                "BEGIN:VCARD",
                "VERSION:3.0",
                f"FN:{full_name}",
            ]
            if include_photo and photo_line:
                parts.append(photo_line)
            if title:
                parts.append(f"TITLE:{title}")
            if wa_digits:
                parts.append(f"TEL;TYPE=CELL:{wa_digits}")
            if email_pub:
                parts.append(f"EMAIL:{email_pub}")
            parts.append(f"URL:{share_url}")
            parts.append("END:VCARD")
            return "\r\n".join(parts)
        def _qr_png(payload: str) -> str:
            qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
            qr.add_data(payload)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")
        data_url = ""
        try:
            data_url = _qr_png(_build_off(True))
        except Exception:
            try:
                data_url = _qr_png(_build_off(False))
            except Exception:
                try:
                    import qrcode.image.svg as qsvg  # type: ignore
                    buf2 = io.BytesIO()
                    qrcode.make(_build_off(False), image_factory=qsvg.SvgImage).save(buf2)
                    data_url = "data:image/svg+xml;base64," + base64.b64encode(buf2.getvalue()).decode("ascii")
                except Exception:
                    return HTMLResponse("<h1>Falha ao gerar QR Offline</h1>", status_code=500)
        theme_base = (prof.get("theme_color", "#000000") or "#000000") if prof else "#000000"
        if not re.fullmatch(r"#([0-9a-fA-F]{6})", theme_base or ""):
            theme_base = "#000000"
        bg_hex = theme_base + "30"
        photo = html.escape(prof.get("photo_url", "")) if prof else ""
        entry_href = html.escape(entry_path)
        off_page = f"""
        <!doctype html><html lang='pt-br'><head>
        <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <link rel='stylesheet' href='{CSS_HREF}'><title>Modo Offline</title></head><body>
        <main class='wrap'>
          <section class='card carbon card-center' style='background-color: {html.escape(bg_hex)}'>
            <div class='topbar'>
              <a class='icon-btn top-left' href='{entry_href}' aria-label='Voltar' title='Voltar'>&larr;</a>
              <h1 class='page-title'>Modo Offline</h1>
            </div>
            {f"<img class='avatar avatar-small' src='{photo}' alt='foto'>" if photo else ""}
            <div class='card' style='background:transparent;border:1px solid #242427;border-radius:12px;padding:16px;margin-top:10px'>
              <p>Modo Offline permite salvar o seu contato diretamente na agenda do cliente, sem utilizar a internet. Basta acessar o QR Code com a camera do celular:</p>
              <div style='margin:12px 0'>
                <img src='{data_url}' alt='QR Offline' style='width:220px;height:220px;image-rendering:pixelated;background:#fff;padding:8px;border-radius:8px'>
              </div>
              <p class='muted' style='font-size:11px'>Dica: Tire um print dessa tela e marque-a como favorita em sua galeria de fotos, para facilitar o acesso em caso de falta de internet.</p>
              <p class='muted' style='font-size:11px'>Atencao: Nao e recomendado imprimir este codigo em placas ou cartao, pois ele nao sera atualizado online. Utilize o codigo QRCode Online para impressao.</p>
            </div>
          </section>
        </main>
        </body></html>
        """
        footer_action_html, footer_token = _footer_action_context(
            request,
            is_owner=is_owner,
            slug=slug,
        )
        response = HTMLResponse(_apply_brand_footer(off_page, footer_action_html))
        if footer_token:
            csrf.set_csrf_cookie(response, footer_token)
        return response
    if who == owner and not card.get("vanity"):
        return RedirectResponse(f"/slug/select/{html.escape(uid)}", status_code=302)
    pix_mode = request.query_params.get("pix", "")
    if pix_mode:
        pix_key = (prof.get("pix_key", "") or "").strip()
        if not pix_key:
            return RedirectResponse(entry_path, status_code=302)
        photo = html.escape(prof.get("photo_url", "")) if prof else ""
        theme_base = (prof.get("theme_color", "#000000") or "#000000") if prof else "#000000"
        if not re.fullmatch(r"#([0-9a-fA-F]{6})", theme_base or ""):
            theme_base = "#000000"
        bg_hex = theme_base + "30"
        entry_href = html.escape(entry_path)
        if pix_mode in ("amount", "1"):
            amt_page = f"""
            <!doctype html><html lang='pt-br'><head>
            <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
            <link rel='stylesheet' href='{CSS_HREF}'><title>Pagamento Pix</title></head><body>
            <main class='wrap'>
              <section class='card carbon card-center' style='background-color: {html.escape(bg_hex)}'>
                <div class='topbar'>
                  <a class='icon-btn top-left' href='{entry_href}' aria-label='Voltar' title='Voltar'>&larr;</a>
                  <h1 class='page-title'>Pagamento Pix</h1>
                </div>
                {f"<img class='avatar avatar-small' src='{photo}' alt='foto'>" if photo else ""}
                <div class='card' style='background:transparent;border:1px solid #242427;border-radius:12px;padding:16px;margin-top:10px'>
                  <h3 style='margin:0 0 8px'>Digite o valor do seu Pix</h3>
                  <p class='muted' style='margin:0 0 12px;font-size:12px'>Deixe 0,00 para gerar um QRCode com valor em aberto</p>
                  <div style='display:flex;gap:8px;justify-content:center'>
                    <input id='pixAmount' type='tel' inputmode='numeric' pattern='[0-9,]*' placeholder='0,00' autocomplete='off' style='max-width:160px'>
                    <a id='genPix' class='btn' href='#'>Gerar Pix <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='14' height='14'><path d='M9 18l6-6-6-6' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/></svg></a>
                  </div>
                </div>
              </section>
            </main>
            <script>
            (function(){{
              var btn = document.getElementById('genPix');
              var amt = document.getElementById('pixAmount');
              function maskMoney(){{
                if (!amt) return;
                var s = (amt.value||'').replace(/\\D/g,'');
                if (s.length === 0) {{ amt.value = ''; return; }}
                while (s.length < 3) s = '0' + s;
                var intp = s.slice(0, -2).replace(/^0+/, '') || '0';
                var decp = s.slice(-2);
                amt.value = intp + ',' + decp;
              }}
              if (amt) {{
                amt.addEventListener('input', maskMoney);
                amt.addEventListener('blur', maskMoney);
              }}
              if (btn && amt){{
                btn.addEventListener('click', function(e){{
                  e.preventDefault();
                  var s = (amt.value||'').replace(/\\D/g,'');
                  if (!s) s = '0';
                  var cents = parseInt(s, 10) || 0;
                  var v = (cents/100).toFixed(2).replace('.', ',');
                  window.location.href = '{entry_href}?pix=qr&v=' + encodeURIComponent(v);
                }});
              }}
            }})();
            </script>
            """
            footer_action_html, footer_token = _footer_action_context(
                request,
                is_owner=is_owner,
                slug=slug,
            )
            response = HTMLResponse(_apply_brand_footer(amt_page, footer_action_html))
            if footer_token:
                csrf.set_csrf_cookie(response, footer_token)
            return response
        elif pix_mode == "qr":
            raw_v = (request.query_params.get("v", "0") or "0").replace(",", ".")
            try:
                amount = max(0.0, float(raw_v))
            except Exception:
                amount = 0.0
            name = (prof.get("full_name", "") if prof else "") or slug
            city = (prof.get("city", "") if prof else "") or "BRASILIA"
            payload = build_pix_emv(pix_key, amount if amount > 0 else None, name, city, txid="***")
            data_url = ""
            try:
                qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
                qr.add_data(payload)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)
                data_url = "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")
            except Exception:
                try:
                    import qrcode.image.svg as qsvg  # type: ignore
                    buf = io.BytesIO()
                    qrcode.make(payload, image_factory=qsvg.SvgImage).save(buf)
                    data_url = "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
                except Exception:
                    return HTMLResponse("<h1>Falha ao gerar QR Pix</h1>", status_code=500)
            page = f"""
            <!doctype html><html lang='pt-br'><head>
            <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
            <link rel='stylesheet' href='{CSS_HREF}'><title>QRCode Pix</title></head><body>
            <main class='wrap'>
              <section class='card carbon card-center' style='background-color: {html.escape(bg_hex)}'>
                <div class='topbar'>
                  <a class='icon-btn top-left' href='{entry_href}' aria-label='Voltar' title='Voltar'>&larr;</a>
                  <h1 class='page-title'>QRCode Pix</h1>
                </div>
                {f"<img class='avatar avatar-small' src='{photo}' alt='foto'>" if photo else ""}
                <div class='card' style='background:transparent;border:1px solid #242427;border-radius:12px;padding:16px;margin-top:10px'>
                  <img src='{data_url}' alt='QR Pix' style='width:240px;height:240px;image-rendering:pixelated;background:#fff;padding:8px;border-radius:8px'>
                  <p class='muted' style='font-size:12px;margin-top:10px'>Escaneie o código no app do banco ou copie o código Pix.</p>
                  <a class='btn full' href='#' id='copyPix'>Copiar código Pix</a>
                </div>
              </section>
            </main>
            <script>
            (function(){{
              var code = {json.dumps(payload)};
              var b = document.getElementById('copyPix');
              function legacyCopy(txt){{
                try {{
                  var ta = document.createElement('textarea');
                  ta.value = txt;
                  ta.setAttribute('readonly','');
                  ta.style.position='absolute'; ta.style.left='-9999px';
                  document.body.appendChild(ta);
                  ta.focus(); ta.select(); ta.setSelectionRange(0, ta.value.length);
                  var ok = document.execCommand('copy');
                  document.body.removeChild(ta);
                  return ok;
                }} catch(_e) {{ return false; }}
              }}
              function copyViaEvent(t){{
                var ok = false;
                function oncopy(e){{
                  try {{ e.clipboardData.setData('text/plain', t); e.preventDefault(); ok = true; }}
                  catch(_e) {{ ok = false; }}
                }}
                document.addEventListener('copy', oncopy);
                try {{ ok = document.execCommand('copy'); }} catch(_e) {{ ok = false; }}
                document.removeEventListener('copy', oncopy);
                return ok;
              }}
              function handler(e){{
                e.preventDefault();
                if (navigator.clipboard && window.isSecureContext){{
                  navigator.clipboard.writeText(code).then(function(){{
                    b.textContent='Copiado'; setTimeout(function(){{ b.textContent='Copiar Codigo Pix'; }},1500);
                  }}).catch(function(){{
                    if (legacyCopy(code) || copyViaEvent(code)){{ b.textContent='Copiado'; setTimeout(function(){{ b.textContent='Copiar Codigo Pix'; }},1500); }}
                  }});
                }} else {{
                  if (legacyCopy(code) || copyViaEvent(code)){{ b.textContent='Copiado'; setTimeout(function(){{ b.textContent='Copiar Codigo Pix'; }},1500); }}
                }}
              }}
              if (b){{
                b.addEventListener('click', handler, {{ passive: false }});
                b.addEventListener('touchend', handler, {{ passive: false }});
              }}
            }})();
            </script>
            </body></html>
            """
            footer_action_html, footer_token = _footer_action_context(
                request,
                is_owner=is_owner,
                slug=slug,
            )
            response = HTMLResponse(_apply_brand_footer(page, footer_action_html))
            if footer_token:
                csrf.set_csrf_cookie(response, footer_token)
            return response
    if is_owner:
        view_count = get_card_view_count(uid)
    else:
        view_count = increment_card_view(uid) if should_track_view(request, slug) else get_card_view_count(uid)
    if not is_owner:
        owner_name = (prof.get("full_name") or "").strip() if isinstance(prof, dict) else ""
        status = (card.get("status") or "").lower()
        owner_email = card.get("user", "")
        owner_user = _sql_repo.get_user(owner_email) if owner_email else None
        is_unverified_owner = owner_user is not None and not owner_user.email_verified_at
        needs_blocked_view = (
            (not profile_complete(prof))
            or (not owner_email)
            or status == "pending"
            or is_unverified_owner
        )
        if needs_blocked_view:
            if (not owner_email) or status == "pending" or is_unverified_owner:
                cta_url = f"/onboard/{uid}/pin"
                cta_label = "Sou o dono? Finalizar ativacao"
            else:
                cta_url = f"/login?uid={uid}"
                cta_label = "Sou o dono? Entrar"
            return templates.TemplateResponse(
                "card_under_construction.html",
                {"request": request, "slug": slug, "owner_name": owner_name, "uid": uid, "cta_url": cta_url, "cta_label": cta_label},
            )
        return visitor_public_card(prof, slug, False, view_count, card=card, request=request)
    return visitor_public_card(prof, slug, True, view_count, card=card, request=request)
@router.get("/", response_class=HTMLResponse)
def custom_domain_root(request: Request):
    host = _request_host(request)
    _, uid, card = find_card_by_custom_domain(host)
    base_host = (PUBLIC_BASE_HOST or "").strip().lower()
    fallback_public_host = ""
    if not base_host and PUBLIC_BASE:
        try:
            fallback_public_host = (urlparse.urlparse(PUBLIC_BASE).hostname or "").strip().lower()
        except ValueError:
            fallback_public_host = ""
    host_value = (host or "").strip().lower()
    host_noport = host_value.split(":", 1)[0] if ":" in host_value else host_value
    default_hosts = set(DEFAULT_LOCAL_ROOTS)
    if base_host:
        default_hosts.add(base_host)
    if fallback_public_host:
        default_hosts.add(fallback_public_host)
    is_default_host = (not host_value) or (host_value in default_hosts) or (host_noport in default_hosts)
    if not card:
        if is_default_host:
            user = current_user_email(request)
            if user:
                pending_uid = None
                has_blocked = False
                for entity in _sql_repo.get_cards_by_owner(user):
                    status = (entity.status or "").lower()
                    if status == "active":
                        dest = entity.vanity or entity.uid
                        return RedirectResponse(f"/{dest}", status_code=303)
                    if status == "blocked":
                        has_blocked = True
                    if status in ("", "pending") and not pending_uid:
                        pending_uid = entity.uid
                if pending_uid:
                    return RedirectResponse(f"/onboard/{pending_uid}", status_code=303)
                if has_blocked:
                    return RedirectResponse("/blocked", status_code=303)
            return RedirectResponse("/login", status_code=303)
        return HTMLResponse("Pagina nao encontrada", status_code=404)
    slug = card.get("vanity") or uid
    # O lookup já foi feito; passamos uid/card para evitar nova consulta.
    return _serve_slug(slug, request, ({}, uid, card))
@router.get("/{slug}", response_class=HTMLResponse)
def root_slug(slug: str, request: Request):
    return _serve_slug(slug, request)
@router.get("/blocked", response_class=HTMLResponse)
def blocked(request: Request):
    return _templates(request).TemplateResponse("blocked.html", {"request": request})
