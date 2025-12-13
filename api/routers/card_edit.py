from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
import urllib.parse as urlparse

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from api.core import csrf
from api.core.config import get_settings
from api.core.security import hash_password, verify_password
from api.services.card_service import find_card_by_slug
from api.services.card_display import (
    FEATURED_DEFAULT_COLOR,
    normalize_external_url,
    profile_complete,
    resolve_photo,
    sanitize_phone,
    _normalize_hex_color,
)
from api.services.domain_service import (
    CUSTOM_DOMAIN_STATUS_ACTIVE,
    CUSTOM_DOMAIN_STATUS_DISABLED,
    CUSTOM_DOMAIN_STATUS_PENDING,
    CUSTOM_DOMAIN_STATUS_REJECTED,
)
from api.services.session_service import current_user_email
from api.repositories.sql_repository import SQLRepository

router = APIRouter(prefix="/edit", tags=["edit"])

CSS_HREF = "/static/card.css"
BRAND_FOOTER = lambda html_doc: html_doc
SETTINGS = None
PUBLIC_BASE = ""
PUBLIC_BASE_HOST = ""
UPLOADS_DIR = ""

DEFAULT_AVATAR = "/static/img/user01.png"

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
JPEG_MAGIC = b"\xFF\xD8\xFF"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_FOOTER_SLOT = "<span id='footerActionSlot' class='footer-auth-slot'></span>"
_FOOTER_PLACEHOLDER = "{footer_action_html}"

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


def _get_settings():
    return SETTINGS or get_settings()


def _templates(request: Request):
    tpl = getattr(getattr(request.app, "state", None), "templates", None)
    if tpl:
        return tpl
    raise RuntimeError("Templates nao configurados para /edit")


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


def _owner_logout_form(slug: str, csrf_token: str) -> str:
    slug_safe = html.escape(slug)
    token_html = html.escape(csrf_token)
    return (
        "<form method='post' action='/auth/logout' class='logout-inline' data-skip-global-loading='true'>"
        f"<input type='hidden' name='csrf_token' value='{token_html}'>"
        f"<input type='hidden' name='next' value='/{slug_safe}'>"
        "<button type='submit' class='muted link-btn' style='background:none;border:0;padding:0;margin:0;cursor:pointer'>Sair</button>"
        "</form>"
    )


def _has_valid_signature(data: bytes, content_type: str) -> bool:
    if content_type in {"image/jpeg", "image/jpg", "image/pjpeg"}:
        return data.startswith(JPEG_MAGIC)
    if content_type == "image/png":
        return data.startswith(PNG_MAGIC)
    return False


def _save_resized_image(data: bytes, filename: str, max_size: tuple[int, int]) -> str:
    try:
        from PIL import Image, ImageOps  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(500, "Dependencia Pillow ausente para processar imagens.") from exc
    try:
        image = Image.open(io.BytesIO(data))
    except Exception as exc:  # pragma: no cover
        raise HTTPException(400, "Arquivo de imagem invalido.") from exc
    try:
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass
    image = image.convert("RGB")
    image.thumbnail(max_size, Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85, optimize=True)
    payload = buffer.getvalue()
    dest_dir = UPLOADS_DIR or ""
    dest_path = os.path.join(dest_dir, filename)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(payload)
    etag = hashlib.md5(payload).hexdigest()[:8]
    return f"/static/uploads/{filename}?v={etag}"

@router.get("/{slug}", response_class=HTMLResponse)
def edit_card(slug: str, request: Request, saved: str = "", error: str = "", pwd: str = ""):
    db, uid, card = find_card_by_slug(slug)
    if not card:
        raise HTTPException(404, "Cartao nao encontrado")
    if uid and card:
        _sql_repo.sync_card_from_json(uid, card)
    owner = card.get("user", "")
    who = current_user_email(request)
    if who != owner:
        return RedirectResponse(f"/{slug}", status_code=303)
    prof = _sql_repo.get_profile(owner) or {}
    saved_cookie = request.cookies.get("flash_edit_saved")
    pwd_cookie = request.cookies.get("flash_edit_pwd")
    saved_flag = bool(saved_cookie or str(saved) == "1")
    pwd_flag = bool(pwd_cookie or str(pwd) == "1")
    show_grev = bool(prof.get("google_review_show", True))
    featured_enabled = bool(prof.get("featured_enabled", True))
    # Cor do tema do cartão (hex #RRGGBB)
    theme_base = prof.get("theme_color", "#000000") or "#000000"
    if not re.fullmatch(r"#([0-9a-fA-F]{6})", theme_base or ""):
        theme_base = "#000000"
    bg_hex = theme_base + "30"
    links = prof.get("links", [])
    photo_url = resolve_photo(prof.get("photo_url"))
    cover_url = (prof.get("cover_url") or "").strip()
    portfolio_enabled = bool(prof.get("portfolio_enabled", False))
    portfolio_images = prof.get("portfolio_images") or []
    if not isinstance(portfolio_images, list):
        portfolio_images = []
    portfolio_images = [(p or "").strip() for p in portfolio_images if p][:5]
    while len(portfolio_images) < 5:
        portfolio_images.append("")
    custom_domains_enabled = _get_settings().custom_domains_enabled
    custom_meta = card.get("custom_domain") or {} if custom_domains_enabled else {}
    active_domain = (custom_meta.get("active_host") or "").strip()
    pending_domain = (custom_meta.get("requested_host") or "").strip()
    custom_status = (custom_meta.get("status") or "").lower()
    if not custom_status and active_domain:
        custom_status = CUSTOM_DOMAIN_STATUS_ACTIVE
    admin_note = (custom_meta.get("admin_note") or "").strip()
    domain_status_labels = {
        CUSTOM_DOMAIN_STATUS_PENDING: "Aguardando aprovação",
        CUSTOM_DOMAIN_STATUS_ACTIVE: "Ativo",
        CUSTOM_DOMAIN_STATUS_REJECTED: "Reprovado",
        CUSTOM_DOMAIN_STATUS_DISABLED: "Desativado",
    }
    custom_status_label = domain_status_labels.get(custom_status, "Sem solicitação")
    custom_info_parts: list[str] = []
    if active_domain:
        custom_info_parts.append(f"Ativo: https://{html.escape(active_domain)}")
    if custom_status == CUSTOM_DOMAIN_STATUS_PENDING and pending_domain and pending_domain != active_domain:
        custom_info_parts.append(f"Pendente: {html.escape(pending_domain)}")
    if custom_status == CUSTOM_DOMAIN_STATUS_REJECTED and pending_domain:
        custom_info_parts.append("Último pedido reprovado.")
    if admin_note:
        custom_info_parts.append(f"Obs: {html.escape(admin_note)}")
    if not custom_info_parts:
        custom_info_parts.append("Nenhuma URL personalizada configurada.")
    custom_domain_info = "<br>".join(custom_info_parts)
    custom_domain_target = PUBLIC_BASE_HOST or (urlparse.urlparse(PUBLIC_BASE).hostname or "nfc.seudominio.com.br")
    if not custom_domains_enabled:
        custom_status_label = "Desativado"
        custom_domain_info = "URLs personalizadas ainda não estão habilitadas neste ambiente."
    if custom_domains_enabled:
        custom_domain_desc_html = "<p>Mapeie seu cartão para um domínio próprio (ex.: nome.seusite.com).</p>"
        custom_domain_panel_html = f"""
                <div class='panel-actions'>
                  <a href='#' id='manageCustomDomain' class='btn'>Gerenciar URL</a>
                  <span id='customDomainStatus' class='muted' style='font-size:12px'>
                    Status: <strong>{html.escape(custom_status_label)}</strong><br>
                    {custom_domain_info}
                  </span>
                </div>
                <p class='muted hint'>Crie um registro CNAME apontando para <code>{html.escape(custom_domain_target)}</code> e solicite a revisão.</p>
                <div id='customDomainData'
                     data-status='{html.escape(custom_status or "")}'
                     data-active='{html.escape(active_domain)}'
                     data-requested='{html.escape(pending_domain)}'
                     data-note='{html.escape(admin_note)}'></div>
        """
    else:
        custom_domain_desc_html = "<p>Mapeie seu cartão para um domínio próprio.</p>"
        custom_domain_panel_html = "<p class='muted hint'>Recurso temporariamente desativado. Em breve você poderá solicitar uma URL customizada por aqui.</p>"
    while len(links) < 4:
        links.append({"label": "", "href": ""})
    banners: list[str] = []
    if error:
        banners.append(f"<div class='banner bad'>{html.escape(error)}</div>")
    if saved_flag:
        if not profile_complete(prof):
            banners.append("<div class='banner'>Alteracoes salvas. Para publicar seu cartao, adicione ao menos um meio de contato (WhatsApp, e-mail publico ou um link).</div>")
        else:
            banners.append("<div class='banner ok'>Alteracoes salvas.</div>")
    if pwd_flag:
        banners.append("<div class='banner ok'>Senha atualizada com sucesso.</div>")
    notice = "".join(banners)
    csrf_token_value = csrf.ensure_csrf_token(request)
    csrf_token_html = html.escape(csrf_token_value)
    csrf_token_js = json.dumps(csrf_token_value)
    footer_action_html = _owner_logout_form(slug, csrf_token_value)
    portfolio_slots = []
    for idx, src in enumerate(portfolio_images, start=1):
        safe_src = html.escape(src)
        is_empty = not bool(src)
        thumb = f"<img src='{safe_src}' alt='Foto {idx}'>" if src else f"<span class='placeholder'>Foto {idx}</span>"
        portfolio_slots.append(
            f"""
              <div class='portfolio-slot' data-slot='{idx}'>
                <div class='portfolio-thumb{' is-empty' if is_empty else ''}' id='pfThumb{idx}'>
                  {thumb}
                  <div class='thumb-glow'></div>
                </div>
                <div class='portfolio-actions'>
                  <a href='#' class='photo-change' data-trigger='{idx}'>Trocar</a>
                  <span class='muted'>|</span>
                  <a href='#' class='photo-change muted' data-remove='{idx}'>Remover</a>
                </div>
                <input type='file' name='portfolio{idx}' id='portfolioInput{idx}' accept='image/jpeg,image/png' style='display:none'>
                <input type='hidden' name='portfolio_remove{idx}' id='portfolioRemove{idx}' value='0'>
              </div>
            """
        )
    portfolio_slots_html = "\n".join(portfolio_slots)
    portfolio_script_block = """
      <script>
      (function(){
        var MAX_SLOTS = 5;
        var MAX_UPLOAD = """ + str(MAX_UPLOAD_BYTES) + """;
        var toggle = document.getElementById('portfolioEnabled');
        var toggleLabel = document.getElementById('portfolioToggleLabel');
        var toggleUi = toggle ? toggle.nextElementSibling : null;
        var toggleKnob = toggleUi ? toggleUi.querySelector('.knob') : null;
        function syncToggle(state){
          if (toggleLabel){
            toggleLabel.textContent = state ? 'Exibindo' : 'Oculto';
          }
          if (toggleKnob){
            toggleKnob.style.left = state ? '22px' : '3px';
          }
          if (toggleUi){
            toggleUi.style.background = state ? '#3dd68c55' : '#2a2a2a';
          }
        }
        function buildEmptyContent(idx){
          return '<span class="placeholder">Foto ' + idx + '</span><div class="thumb-glow"></div>';
        }
        function wireSlot(idx){
          var input = document.getElementById('portfolioInput' + idx);
          var thumb = document.getElementById('pfThumb' + idx);
          var removeFlag = document.getElementById('portfolioRemove' + idx);
          var trigger = document.querySelector('[data-trigger=\"' + idx + '\"]');
          var remover = document.querySelector('[data-remove=\"' + idx + '\"]');
          function clearSlot(){
            if (thumb){
              thumb.classList.add('is-empty');
              thumb.innerHTML = buildEmptyContent(idx);
            }
            if (removeFlag){ removeFlag.value = '1'; }
            if (input){ input.value = ''; }
          }
          if (trigger && input){
            trigger.addEventListener('click', function(ev){
              ev.preventDefault();
              input.click();
            });
          }
          if (remover){
            remover.addEventListener('click', function(ev){
              ev.preventDefault();
              clearSlot();
            });
          }
          if (input){
            input.addEventListener('change', function(){
              var f = (input.files && input.files[0]) || null;
              if (!f){ return; }
              var ok = /^(image\\/jpeg|image\\/png)$/i.test(f.type || '');
              if (!ok){
                alert('Formato de imagem nao suportado (use JPEG ou PNG).');
                input.value = '';
                return;
              }
              if (f.size > MAX_UPLOAD){
                alert('Imagem excede 2MB. Escolha uma foto menor.');
                input.value = '';
                return;
              }
              var reader = new FileReader();
              reader.onload = function(evt){
                if (!thumb){ return; }
                var src = (evt && evt.target && evt.target.result) ? evt.target.result : '';
                thumb.classList.remove('is-empty');
                thumb.innerHTML = '<img src=\"' + src + '\" alt=\"Foto ' + idx + '\"><div class=\"thumb-glow\"></div>';
                if (removeFlag){ removeFlag.value = '0'; }
              };
              reader.readAsDataURL(f);
            });
          }
        }
        for (var i = 1; i <= MAX_SLOTS; i += 1){
          wireSlot(i);
        }
        if (toggle){
          syncToggle(!!toggle.checked);
          toggle.addEventListener('change', function(){
            syncToggle(!!toggle.checked);
          });
        }
      })();
      </script>
    """
    html_form = f"""
    <!doctype html><html lang='pt-br'><head>
    <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <link rel='stylesheet' href='{CSS_HREF}'><title>Soomei - Editar</title>
    <style>
      .modal-backdrop{{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;z-index:1000}}
      .modal-backdrop.show{{display:flex}}
      .modal{{background:#111114;border:1px solid #242427;border-radius:12px;max-width:840px;width:92%;max-height:85vh;overflow:auto;padding:12px}}
      .modal header{{display:flex;justify-content:space-between;align-items:center;margin:4px 6px 10px}}
      .modal header h2{{margin:0;font-size:18px;color:#eaeaea}}
      .modal .close{{background:#ffffff;color:#0b0b0c;border:1px solid #e5e7eb;border-radius:999px;width:30px;height:30px;display:inline-flex;align-items:center;justify-content:center;cursor:pointer}}
      .modal iframe{{width:100%;height:70vh;border:0;background:#0b0b0c}}
      .banner.bad{{background:#3a1717;border-color:#5c2323;color:#f5b8b8}}
      .edit-sections{{display:flex;flex-direction:column;gap:18px;margin-top:12px}}
      .edit-section{{background:#111114;border:1px solid #242427;border-radius:16px;padding:18px}}
      .collapsible-head{{display:flex;flex-direction:row;align-items:flex-start;justify-content:space-between;gap:12px}}
      .collapsible-head > div{{flex:1}}
      .section-kicker{{text-transform:uppercase;font-size:11px;letter-spacing:.3px;color:#9aa0a6;margin:0 0 6px}}
      .section-head{{display:flex;flex-direction:column;gap:4px;margin-bottom:14px}}
      .section-title{{margin:0;font-size:20px}}
      .section-desc{{margin:0;font-size:13px;color:#9aa0a6}}
      .collapsible-body{{margin-top:12px}}
      .collapsible-body.is-collapsed{{display:none}}
      .collapse-btn{{background:transparent;border:1px solid #2a2a2a;color:#eaeaea;padding:6px 12px;border-radius:999px;font-size:12px;font-weight:600;display:inline-flex;align-items:center;gap:6px;cursor:pointer}}
      .collapse-btn .collapse-icon{{transition:transform .2s}}
      .collapse-btn.is-collapsed .collapse-icon{{transform:rotate(-90deg)}}
      .section-grid{{display:grid;gap:14px}}
      .section-grid.two-col{{grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}}
      .form-control{{display:flex;flex-direction:column;gap:6px}}
      .form-control label{{font-weight:600;font-size:13px;color:#eaeaea}}
      .form-control input{{border:1px solid #2a2a2a;background:#0b0b0c;color:#eaeaea;padding:10px;border-radius:10px}}
      .required-pill{{display:inline-flex;align-items:center;background:#2f1c1c;color:#f8baba;font-size:10px;text-transform:uppercase;letter-spacing:.3px;padding:1px 6px;border-radius:999px;margin-left:8px}}
      .primary-required-hint{{font-size:12px;color:#9aa0a6;margin-top:6px}}
      .primary-required-hint.is-error{{color:#f8baba}}
      .btn[disabled]{{opacity:.55;cursor:not-allowed}}
      .form-control.full{{grid-column:1 / -1}}
      .visual-grid{{display:grid;gap:18px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}}
      .cover-preview{{width:100%;height:150px;border:1px dashed #2a2a2a;border-radius:12px;background:#0f0f10;display:flex;align-items:center;justify-content:center;overflow:hidden}}
      .cover-preview img{{width:100%;height:100%;object-fit:cover;display:block}}
      .cover-placeholder{{color:#9aa0a6;font-size:13px;text-align:center}}
      .cover-actions{{display:flex;align-items:center;gap:12px;margin-top:8px;flex-wrap:wrap}}
      .cta-row{{display:flex;flex-direction:column;gap:16px}}
      .cta-card{{border:1px solid #242427;border-radius:12px;padding:16px;background:#0f0f10}}
      .cta-card h4{{margin:0 0 6px;font-size:15px}}
      .cta-card p{{margin:0 0 10px;font-size:13px;color:#9aa0a6}}
      .panel-actions{{display:flex;align-items:center;gap:10px;flex-wrap:wrap}}
      .links-grid-edit{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
      .portfolio-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}}
      .portfolio-slot{{border:1px solid #242427;border-radius:12px;padding:12px;background:#0f0f10;position:relative;overflow:hidden}}
      .portfolio-thumb{{position:relative;border:1px dashed #2a2a2a;border-radius:10px;height:160px;display:flex;align-items:center;justify-content:center;background:radial-gradient(circle at 20% 20%,rgba(255,255,255,.05),transparent 45%),radial-gradient(circle at 80% 0%,rgba(255,255,255,.04),transparent 30%),#0c0c0f;transition:transform .2s ease,box-shadow .2s ease,border-color .2s ease}}
      .portfolio-thumb.is-empty{{border-style:dashed}}
      .portfolio-thumb img{{width:100%;height:100%;object-fit:cover;display:block}}
      .portfolio-thumb .placeholder{{color:#7f8591;font-size:13px}}
      .portfolio-thumb:hover{{transform:translateY(-3px);box-shadow:0 10px 26px rgba(0,0,0,.3);border-color:#2f77ff40}}
      .thumb-glow{{position:absolute;inset:0;border-radius:10px;background:linear-gradient(120deg,rgba(255,255,255,.08),rgba(0,0,0,.02));mix-blend-mode:screen;pointer-events:none}}
      .portfolio-actions{{display:flex;align-items:center;gap:8px;margin-top:8px;font-size:13px}}
      .portfolio-header{{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px;flex-wrap:wrap}}
      .portfolio-hint{{margin:0;color:#9aa0a6;font-size:12px}}
      .portfolio-toggle-label{{display:inline-flex;align-items:center;gap:8px;cursor:pointer;font-size:12px;color:#9aa0a6}}
      .password-fields{{margin-top:14px;display:grid;gap:12px}}
      .password-fields.is-hidden{{display:none}}
      .btn.ghost{{background:transparent;border:1px solid #2a2a2a;color:#eaeaea}}
      .btn.primary{{background:#eaeaea;color:#0b0b0c;font-weight:600}}
      .edit-actions{{position:sticky;bottom:0;padding:0;margin-top:24px;background:linear-gradient(180deg,rgba(11,11,12,0) 0%,rgba(11,11,12,.85) 30%,rgba(11,11,12,1) 70%)}}
      .edit-actions-inner{{display:flex;gap:12px;padding:12px 0;border-top:1px solid #1f1f1f}}
      .edit-actions-inner .btn{{flex:1;text-align:center}}
      .loading-overlay{{position:fixed;inset:0;background:rgba(11,11,12,.8);display:none;align-items:center;justify-content:center;z-index:2000}}
      .loading-overlay.show{{display:flex}}
      .loading-spinner{{width:96px;height:96px;object-fit:contain;pointer-events:none}}
    </style>
    </head>
    <body>
      <div class='loading-overlay' id='formLoading' aria-hidden='true' role='status' aria-live='polite' aria-label='Processando'>
        <img src='/static/img/soomei_loading.gif' alt='Processando...' class='loading-spinner'>
      </div>
      <script>
        window.soomeiLoader = (function(){{
          var el = null;
          function getEl(){{
            if (!el){{
              el = document.getElementById('formLoading');
            }}
            return el;
          }}
          function setState(open){{
            var target = getEl();
            if (!target) return;
            if (open){{
              target.classList.add('show');
              target.setAttribute('aria-hidden','false');
            }} else {{
              target.classList.remove('show');
              target.setAttribute('aria-hidden','true');
            }}
          }}
          function requestShow(){{
            if (window.requestAnimationFrame){{
              window.requestAnimationFrame(function(){{ setState(true); }});
            }} else {{
              setState(true);
            }}
          }}
          return {{
            show: requestShow,
            hide: function(){{ setState(false); }}
          }};
        }})();
        window.soomeiRequestTracker = (function(){{
          var pending = 0;
          var visibleSince = 0;
          var hideTimer = null;
          var MIN_VISIBLE_MS = 300;
          function cancelHide(){{
            if (hideTimer){{
              clearTimeout(hideTimer);
              hideTimer = null;
            }}
          }}
          function ensureShown(){{
            cancelHide();
            visibleSince = Date.now();
            if (window.soomeiLoader && window.soomeiLoader.show){{
              window.soomeiLoader.show();
            }}
          }}
          function ensureHidden(){{
            cancelHide();
            var elapsed = Date.now() - visibleSince;
            var delay = Math.max(0, MIN_VISIBLE_MS - elapsed);
            hideTimer = setTimeout(function(){{
              hideTimer = null;
              if (pending === 0 && window.soomeiLoader && window.soomeiLoader.hide){{
                window.soomeiLoader.hide();
              }}
            }}, delay);
          }}
          function start(){{
            pending += 1;
            if (pending === 1){{
              ensureShown();
            }}
          }}
          function end(){{
            if (pending === 0){{
              return;
            }}
            pending -= 1;
            if (pending === 0){{
              ensureHidden();
            }}
          }}
          return {{
            start: start,
            end: end
          }};
        }})();
        (function(){{
          var tracker = window.soomeiRequestTracker;
          if (!tracker) return;
          if (window.fetch && !window._soomeiFetchWrapped){{
            var originalFetch = window.fetch;
            window.fetch = function(){{
              tracker.start();
              var response;
              try {{
                response = originalFetch.apply(this, arguments);
              }} catch (err) {{
                tracker.end();
                throw err;
              }}
              if (response && typeof response.then === 'function'){{
                return response.then(function(res){{
                  tracker.end();
                  return res;
                }}, function(err){{
                  tracker.end();
                  throw err;
                }});
              }}
              tracker.end();
              return response;
            }};
            window._soomeiFetchWrapped = true;
          }}
          if (window.XMLHttpRequest && !window._soomeiXHRWrapped){{
            var origSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.send = function(){{
              var ended = false;
              var finish = function(){{
                if (ended) return;
                ended = true;
                tracker.end();
              }};
              tracker.start();
              this.addEventListener('loadend', finish);
              try{{
                return origSend.apply(this, arguments);
              }} catch (err){{
                finish();
                throw err;
              }}
            }};
            window._soomeiXHRWrapped = true;
          }}
        }})();
        (function(){{
          function globalShow(){{
            if (window.soomeiLoader && window.soomeiLoader.show){{
              window.soomeiLoader.show();
            }}
          }}
          document.addEventListener('submit', function(ev){{
            var target = ev.target;
            if (!target || target.hasAttribute('data-skip-global-loading')) return;
            setTimeout(function(){{
              if (ev.defaultPrevented) return;
              globalShow();
            }}, 0);
          }}, true);
          window.addEventListener('beforeunload', function(){{
            globalShow();
          }});
        }})();
      </script>
      <main class='wrap'>
      {notice}
      <form id='editForm' method='post' action='/edit/{html.escape(slug)}' enctype='multipart/form-data'>
        <input type='hidden' name='csrf_token' value='{csrf_token_html}'>
        <div class='topbar'>
          <h1 class='page-title'>Editar Perfil</h1>
        </div>
        <div class='edit-sections'>
          <section class='edit-section'>
            <p class='section-kicker'>Identidade</p>
            <div class='section-head'>
              <h2 class='section-title'>Foto e cores</h2>
              <p class='section-desc'>Atualize sua imagem principal e mantenha o cartão alinhado à sua marca.</p>
            </div>
            <div class='visual-grid'>
              <div class='form-control full'>
                <label for='coverInput'>Capa do cartão</label>
                <div class='cover-preview' id='coverPreview'>
                  {(
                    f"<img id='coverImg' src='{html.escape(cover_url)}' alt='capa do cartão'>"
                  ) if cover_url else "<img id='coverImg' src='' alt='capa do cartão' style='display:none'>"}
                  <span id='coverPlaceholder' class='cover-placeholder' style='display:{'none' if cover_url else 'block'}'>Nenhuma capa selecionada</span>
                </div>
                <div class='cover-actions'>
                  <a href='#' id='coverTrigger' class='photo-change' onclick="document.getElementById('coverInput').click(); return false;">Alterar imagem de capa</a>
                  <span> | </span>
                  <a href='#' id='coverRemove' class='photo-change muted' onclick="document.getElementById('coverRemoveFlag').value='1';document.getElementById('coverPreview').classList.add('is-empty');document.getElementById('coverPlaceholder').style.display='block';var img=document.getElementById('coverImg'); if(img){{img.src='';img.style.display='none';}} return false;">Remover capa</a>
                  <span class='muted hint'>Sugerimos 1200x630px. Otimizamos automaticamente após o envio.</span>
                </div>
                <input type='file' id='coverInput' name='cover' accept='image/jpeg,image/png' style='display:none'>
                <input type='hidden' id='coverRemoveFlag' name='cover_remove' value='0'>
              </div>
              <div>
                <div class='avatar-preview-wrap'>
                  <div id='colorPreview' class='preview-carbon carbon' style='background-color: {html.escape(bg_hex)}'></div>
                  <div style='text-align:center;margin:0'>
                    <img id='avatarImg'
                        class='avatar'
                        src='{html.escape(photo_url)}'
                        alt='foto'
                        onerror="this.onerror=null;this.src='{DEFAULT_AVATAR}'">
                    <div><a href='#' id='photoTrigger' class='photo-change'
                            onclick="document.getElementById('photoInput').click(); return false;">
                            Alterar foto do perfil
                    </a></div>
                    <div class='muted' style='font-size:12px;margin-top:4px'>Aceitamos JPG/PNG; otimizamos automaticamente após o envio.</div>
                  </div>
                </div>
                <input type='file' id='photoInput' name='photo' accept='image/jpeg,image/png' style='display:none'>
              </div>
              <div class='form-control'>
                <label for='themeColor'>Cor do cartão</label>
                <input type='color' id='themeColor' name='theme_color' value='{html.escape(theme_base)}' style='height: 48px' aria-label='Cor do cartao'>
                <span class='muted hint'>Essa cor é usada em botões e cartões auxiliares.</span>
              </div>
            </div>
          </section>
          <section class='edit-section'>
            <p class='section-kicker'>Informações principais</p>
            <div class='section-head'>
              <h2 class='section-title'>Contato e apresentação</h2>
              <p class='section-desc'>Esses dados aparecem no topo do seu cartão.</p>
            </div>
            <div class='section-grid two-col'>
              <div class='form-control'>
                <label for='fullName'>Nome <span class='required-pill'>Obrigatorio</span></label>
                <input id='fullName' name='full_name' value='{html.escape(prof.get('full_name',''))}' placeholder='Nome completo' required aria-label='Nome completo' autocomplete='name'>
              </div>
              <div class='form-control'>
                <label for='titleInput'>Cargo | Empresa <span class='required-pill'>Obrigatorio</span></label>
                <input id='titleInput' name='title' value='{html.escape(prof.get('title',''))}' placeholder='Ex.: Diretor | Soomei' required aria-label='Cargo e empresa' autocomplete='organization-title'>
              </div>
              <div class='form-control'>
                <label for='whatsapp'>WhatsApp <span class='required-pill'>Obrigatorio</span></label>
                <input name='whatsapp' id='whatsapp' inputmode='numeric' autocomplete='tel' placeholder='+55 (00) 00000-0000' value='{html.escape(prof.get('whatsapp',''))}' maxlength='19' aria-label='WhatsApp'>
              </div>
              <div class='form-control'>
                <label for='emailPublic'>Email público <span class='required-pill'>Obrigatorio</span></label>
                <input id='emailPublic' name='email_public' type='email' value='{html.escape(prof.get('email_public',''))}' placeholder='contato@exemplo.com' aria-label='Email publico' autocomplete='email'>
              </div>
              <div class='form-control'>
                <label for='siteUrl'>Site</label>
                <input id='siteUrl' name='site_url' type='url' placeholder='https://seusite.com' value='{html.escape(prof.get('site_url',''))}' aria-label='Site' autocomplete='url'>
              </div>
              <div class='form-control full'>
                <label for='addressInput'>Endereço</label>
                <input id='addressInput' name='address' value='{html.escape(prof.get('address',''))}' placeholder='Rua, número - Cidade/UF' aria-label='Endereco' autocomplete='street-address'>
              </div>
            </div>
            <div id='primaryInfoHint' class='primary-required-hint' role='status' aria-live='polite' tabindex='-1'>
              Para salvar, informe nome, cargo e pelo menos um contato (WhatsApp ou email).
            </div>
          </section>
          <section class='edit-section' data-collapsible="1" data-collapsed="1">
            <p class='section-kicker'>Integrações</p>
            <div class='section-head'>
              <h2 class='section-title'>Pix, slug e avaliações</h2>
              <p class='section-desc'>Configure como as pessoas chegam até você e como pagam por seus serviços.</p>
            </div>
            <div class='cta-row'>
              <div class='cta-card'>
                <h4>Chave Pix</h4>
                <p>Guarde uma chave Pix para gerar QR Codes e receber pagamentos.</p>
                <div class='panel-actions'>
                  <input type='hidden' id='pixKey' name='pix_key' value='{html.escape(prof.get('pix_key',''))}'>
                  <a href='#' id='addPix' class='btn'>Definir chave Pix</a>
                  <span id='pixInfo' class='muted' style='font-size:12px'>{("Chave atual: " + html.escape(prof.get('pix_key','')) + " <a href='#' id='pixDel' class='muted' title='Remover' aria-label='Remover' style='margin-left:6px'>&#10005;</a>") if prof.get('pix_key') else ''}</span>
                </div>
              </div>
              <div class='cta-card'>
                <h4>Slug público</h4>
                <p>Escolha o endereço curto do seu cartão (ex.: /seu-nome).</p>
                <div class='panel-actions'>
                  <input type='hidden' id='slugKey' value='{html.escape(card.get('vanity', uid))}'>
                  <a href='#' id='addSlug' class='btn ghost'>Alterar slug</a>
                  <span id='slugInfo' class='muted' style='font-size:12px'>URL atual: /{html.escape(card.get('vanity', uid))}</span>
                </div>
              </div>
              <div class='cta-card'>
                <h4>URL personalizada</h4>
                {custom_domain_desc_html}
                {custom_domain_panel_html}
              <div class='cta-card highlight-config'>
                <div style='display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:10px'>
                  <div>
                    <h4 style='margin:0'>Botão destaque</h4>
                    <p style='margin:4px 0 0;font-size:13px;color:#9aa0a6'>Defina a principal ação que você quer que os visitantes realizem ao abrir seu cartão.</p>
                  </div>
                  <label class='switch' for='featuredEnabled' style='display:inline-flex;align-items:center;gap:8px;cursor:pointer'>
                    <input type='checkbox' id='featuredEnabled' name='featured_enabled' value='1' {'checked' if featured_enabled else ''} style='display:none'>
                    <span class='switch-ui' aria-hidden='true' style='width:42px;height:24px;border-radius:999px;background:#2a2a2a;position:relative;display:inline-block;transition:.2s'>
                      <span class='knob' style='position:absolute;top:3px;left:{'22px' if featured_enabled else '3px'};width:18px;height:18px;border-radius:50%;background:#eaeaea;transition:left .2s'></span>
                    </span>
                    <span class='muted' style='font-size:12px'>{'Exibindo' if featured_enabled else 'Oculto'}</span>
                  </label>
                </div>
                <div class='form-control'>
                <label for='featuredLabel'>Título do botão</label>
                  <input id='featuredLabel' name='featured_label' maxlength='48' value='{html.escape(prof.get('featured_label',''))}' placeholder='Agendar experiência' aria-label='Titulo do botao destaque'>
                </div>
                <div class='form-control'>
                  <label for='featuredUrl'>Link (URL)</label>
                  <input id='featuredUrl' name='featured_url' type='url' inputmode='url' placeholder='https://seusite.com/agendar' value='{html.escape(prof.get('featured_url',''))}' aria-label='Link do botao destaque'>
                </div>
                <div class='form-control'>
                  <label for='featuredColor'>Cor principal</label>
                  <div style='display:flex;align-items:center;gap:10px'>
                    <input id='featuredColor' data-default-color='{FEATURED_DEFAULT_COLOR}' name='featured_color' type='color' value='{html.escape(prof.get('featured_color', FEATURED_DEFAULT_COLOR) or FEATURED_DEFAULT_COLOR)}' style='height:42px;padding:0 8px;border-radius:12px;flex:0 0 120px' aria-label='Cor do botao destaque'>
                    <button type='button' class='btn ghost' id='featuredColorReset' style='flex:1'>Resetar cor</button>
                  </div>
                  <p class='muted hint'>Define o gradiente e o brilho do botão.</p>
                </div>
                <p class='muted hint'>Deixe em branco para ocultar o botão destaque.</p>
              </div>
            </div>
        <div class='cta-card' style='margin-top:12px'>
          <div style='display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:8px'>
            <div style='display:flex;align-items:center;gap:8px;margin:0'>
              <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48' width='18' height='18' class='g-icon'>
                <path fill='#fff' d='M24 9.5c3.94 0 7.06 1.7 9.18 3.12l6.77-6.77C36.26 2.52 30.62 0 24 0 14.5 0 6.36 5.4 2.4 13.22l7.9 6.14C12.12 13.32 17.63 9.5 24 9.5z'/>
                <path fill='#fff' d='M46.5 24.5c0-1.6-.14-3.1-.4-4.5H24v9h12.7c-.6 3.2-2.4 5.9-5.1 7.7l7.9 6.1C43.8 38.8 46.5 32.1 46.5 24.5z'/>
                <path fill='#fff' d='M10.3 28.36A14.5 14.5 0 0 1 9.5 24c0-1.53.26-3.02.74-4.36l-7.9-6.14A23.74 23.74 0 0 0 0 24c0 3.83.93 7.46 2.54 10.64l7.76-6.28z'/>
                <path fill='#fff' d='M24 48c6.48 0 11.92-2.13 15.89-5.8l-7.9-6.14C29.8 37.75 27.06 38.5 24 38.5c-6.37 0-11.88-3.82-14.7-9.86l-7.9 6.14C6.36 42.6 14.5 48 24 48z'/>
              </svg>
              <span>Link de avaliação do Google</span>
            </div>
            <!-- TOGGLE ON/OFF -->
            <label class='switch' for='googleReviewShow' style='display:inline-flex;align-items:center;gap:8px;cursor:pointer'>
              <input type='checkbox' id='googleReviewShow' name='google_review_show' value='1' {'checked' if show_grev else ''} style='display:none'>
              <span class='switch-ui' aria-hidden='true' style='width:42px;height:24px;border-radius:999px;background:#2a2a2a;position:relative;display:inline-block;transition:.2s'>
                <span class='knob' style='position:absolute;top:3px;left:{'22px' if show_grev else '3px'};width:18px;height:18px;border-radius:50%;background:#eaeaea;transition:left .2s'></span>
              </span>
              <span class='muted' style='font-size:12px'>{'Exibindo' if show_grev else 'Oculto'}</span>
            </label>
          </div>
          <input id='googleReviewUrl' name='google_review_url' aria-label='Link de avaliacao do Google' type='url' placeholder='https://search.google.com/local/writereview?...'
                value='{html.escape(prof.get("google_review_url", ""))}'>
          <a class="link-google-review" target='_blank' rel='noopener'
            href='https://support.google.com/business/answer/3474122?hl=pt-BR#:~:text=Para%20encontrar%20o%20link%20da,Pesquisa%20Google%2C%20selecione%20Solicitar%20avalia%C3%A7%C3%B5es'>
            Toque aqui para ver como encontrar o link de Avaliação do Google Meu Negócio
          </a>
        </div>
          </section>
          <section class='edit-section' data-collapsible="1" data-collapsed="1">
            <p class='section-kicker'>Presença digital</p>
            <div class='section-head'>
              <h2 class='section-title'>Links em destaque</h2>
              <p class='section-desc'>Adicione até quatro links personalizados.</p>
            </div>
            <div class='links-grid-edit'>
              <div class='form-control'>
                <label for='label1'>Título do botão 1</label>
                <input id='label1' name='label1' value='{html.escape(links[0].get('label',''))}' aria-label='Titulo do botao 1'>
              </div>
              <div class='form-control'>
                <label for='href1'>Link (URL)</label>
                <input id='href1' name='href1' value='{html.escape(links[0].get('href',''))}' aria-label='Link do botao 1'>
              </div>
              <div class='form-control'>
                <label for='label2'>Título do botão 2</label>
                <input id='label2' name='label2' value='{html.escape(links[1].get('label',''))}' aria-label='Titulo do botao 2'>
              </div>
              <div class='form-control'>
                <label for='href2'>Link (URL)</label>
                <input id='href2' name='href2' value='{html.escape(links[1].get('href',''))}' aria-label='Link do botao 2'>
              </div>
              <div class='form-control'>
                <label for='label3'>Título do botão 3</label>
                <input id='label3' name='label3' value='{html.escape(links[2].get('label',''))}' aria-label='Titulo do botao 3'>
              </div>
              <div class='form-control'>
                <label for='href3'>Link (URL)</label>
                <input id='href3' name='href3' value='{html.escape(links[2].get('href',''))}' aria-label='Link do botao 3'>
              </div>
              <div class='form-control'>
                <label for='label4'>Título do botão 4</label>
                <input id='label4' name='label4' value='{html.escape(links[3].get('label',''))}' aria-label='Titulo do botao 4'>
              </div>
              <div class='form-control'>
                <label for='href4'>Link (URL)</label>
                <input id='href4' name='href4' value='{html.escape(links[3].get('href',''))}' aria-label='Link do botao 4'>
              </div>
            </div>
          </section>
          <section class='edit-section' data-collapsible="1" data-collapsed="1">
            <p class='section-kicker'>Portfólio</p>
            <div class='section-head'>
              <h2 class='section-title'>Fotos para o carrossel 3D</h2>
              <p class='section-desc'>Adicione até 5 imagens. Elas serão otimizadas e exibidas em um carrossel 3D logo abaixo dos links extras.</p>
            </div>
            <div class='portfolio-header'>
              <p class='portfolio-hint'>Deixe desativado para esconder o portfólio no cartão.</p>
              <label class='switch portfolio-toggle-label' for='portfolioEnabled'>
                <input type='checkbox' id='portfolioEnabled' name='portfolio_enabled' value='1' {'checked' if portfolio_enabled else ''} style='display:none'>
                <span class='switch-ui' aria-hidden='true' style='width:42px;height:24px;border-radius:999px;background:#2a2a2a;position:relative;display:inline-block;transition:.2s'>
                  <span class='knob' style='position:absolute;top:3px;left:{'22px' if portfolio_enabled else '3px'};width:18px;height:18px;border-radius:50%;background:#eaeaea;transition:left .2s'></span>
                </span>
                <span class='muted' id='portfolioToggleLabel' style='font-size:12px'>{'Exibindo' if portfolio_enabled else 'Oculto'}</span>
              </label>
            </div>
            <div class='portfolio-grid'>
              {portfolio_slots_html}
            </div>
            <p class='portfolio-hint'>Limitamos a 5 fotos (2MB) e salvamos os arquivos em uma pasta interna do seu usuário.</p>
          </section>
          <section class='edit-section' data-collapsible="1" data-collapsed="1">
            <p class='section-kicker'>Segurança</p>
            <div class='section-head'>
              <h2 class='section-title'>Senha e acesso</h2>
              <p class='section-desc'>Troque sua senha sempre que identificar atividade suspeita.</p>
            </div>
            <button type='button' class='btn ghost' id='togglePassword' aria-expanded='false'>Alterar senha</button>
            <div id='passwordFields' class='password-fields is-hidden'>
              <input type='hidden' name='password_mode' id='passwordMode' value='0'>
              <div class='form-control'>
                <label for='currentPassword'>Senha atual</label>
                <input type='password' id='currentPassword' name='current_password' autocomplete='current-password' placeholder='Digite sua senha atual' aria-label='Senha atual'>
              </div>
              <div class='form-control'>
                <label for='newPassword'>Nova senha</label>
                <input type='password' id='newPassword' name='new_password' autocomplete='new-password' minlength='8' placeholder='Mínimo de 8 caracteres' aria-label='Nova senha'>
              </div>
              <div class='form-control'>
                <label for='confirmPassword'>Confirmar nova senha</label>
                <input type='password' id='confirmPassword' name='confirm_password' autocomplete='new-password' minlength='8' placeholder='Repita a nova senha' aria-label='Confirmar nova senha'>
              </div>
              <p class='muted hint'>Sua sessão permanecerá ativa após a troca.</p>
            </div>
          </section>
        </div>
          <div class='edit-actions'>
          <div class='edit-actions-inner'>
            <button type='button' class='btn ghost' id='backToCard'>Voltar</button>
            <button type='submit' class='btn primary' id='saveBtn'>Salvar alterações</button>
          </div>
        </div>
        <script>
        (function(){{
          var backBtn = document.getElementById('backToCard');
          if (backBtn){{
            backBtn.addEventListener('click', function(e){{
              e.preventDefault();
              window.location.href='/{html.escape(slug)}';
            }});
          }}
          var collapseIndex = 0;
          var collapseSections = document.querySelectorAll(".edit-section[data-collapsible='1']");
          collapseSections.forEach(function(section){{
            var head = section.querySelector('.section-head');
            if (!head) return;
            collapseIndex += 1;
            section.classList.add('collapsible');
            head.classList.add('collapsible-head');
            var kicker = section.querySelector('.section-kicker');
            if (kicker && kicker.parentElement === section && !head.contains(kicker)){{
              head.insertBefore(kicker, head.firstChild);
            }}
            var targetId = 'collapse-section-' + collapseIndex;
            var body = document.createElement('div');
            body.className = 'collapsible-body';
            body.id = targetId;
            while (head.nextSibling){{
              body.appendChild(head.nextSibling);
            }}
            section.appendChild(body);
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'collapse-btn';
            btn.setAttribute('aria-expanded','true');
            btn.setAttribute('data-target', targetId);
            btn.innerHTML = "<span class='collapse-label'>Ocultar</span><span class='collapse-icon' aria-hidden='true'>&#9662;</span>";
            head.appendChild(btn);
            function setState(open){{
              if (open){{
                body.classList.remove('is-collapsed');
                btn.classList.remove('is-collapsed');
                btn.setAttribute('aria-expanded','true');
                var label = btn.querySelector('.collapse-label');
                if (label) label.textContent = 'Ocultar';
              }} else {{
                body.classList.add('is-collapsed');
                btn.classList.add('is-collapsed');
                btn.setAttribute('aria-expanded','false');
                var label = btn.querySelector('.collapse-label');
                if (label) label.textContent = 'Expandir';
              }}
            }}
            btn.addEventListener('click', function(ev){{
              ev.preventDefault();
              var open = btn.getAttribute('aria-expanded') !== 'true';
              setState(open);
            }});
            var startCollapsed = section.getAttribute('data-collapsed') === '1';
            setState(!startCollapsed);
          }});
          var form = document.getElementById('editForm');
          var saveBtn = document.getElementById('saveBtn');
          var primaryHint = document.getElementById('primaryInfoHint');
          var requiredName = document.querySelector("input[id='fullName' name='full_name']");
          var requiredTitle = document.querySelector("input[id='titleInput' name='title']");
          var whatsappInput = document.getElementById('whatsapp');
          var emailInput = document.querySelector("input[id='emailPublic' name='email_public']");
          function hasValue(el){{
            return !!(el && typeof el.value === 'string' && el.value.trim());
          }}
          function hasWhatsapp(){{
            if (!whatsappInput) return false;
            return (whatsappInput.value || '').replace(/\D/g,'').length > 0;
          }}
          function hasEmail(){{
            if (!emailInput) return false;
            return !!(emailInput.value || '').trim();
          }}
          function updatePrimaryState(){{
            var ok = hasValue(requiredName) && hasValue(requiredTitle) && (hasWhatsapp() || hasEmail());
            if (saveBtn){{
              if (!ok){{
                saveBtn.disabled = true;
                saveBtn.setAttribute('aria-disabled','true');
                saveBtn.setAttribute('data-primary-lock','1');
              }} else if (saveBtn.getAttribute('data-primary-lock') === '1'){{
                saveBtn.disabled = false;
                saveBtn.removeAttribute('aria-disabled');
                saveBtn.removeAttribute('data-primary-lock');
              }}
            }}
            if (primaryHint){{
              primaryHint.classList.toggle('is-error', !ok);
            }}
            return ok;
          }}
          [requiredName, requiredTitle, whatsappInput, emailInput].forEach(function(input){{
            if (!input) return;
            input.addEventListener('input', updatePrimaryState);
            input.addEventListener('blur', updatePrimaryState);
          }});
          updatePrimaryState();
          if (form){{
            form.addEventListener('submit', function(e){{
              if (!updatePrimaryState()){{
                e.preventDefault();
                e.stopPropagation();
                if (primaryHint){{
                  try{{ primaryHint.focus(); }}catch(_e){{}}
                  try{{ primaryHint.scrollIntoView({{behavior:'smooth', block:'center'}}); }}catch(_e){{}}
                }}
              }}
            }});
          }}
          var togglePwd = document.getElementById('togglePassword');
          var pwdFields = document.getElementById('passwordFields');
          var pwdMode = document.getElementById('passwordMode');
          if (togglePwd && pwdFields){{
            function setState(open){{
              if (open){{
                pwdFields.classList.remove('is-hidden');
                togglePwd.textContent = 'Cancelar alteracao de senha';
                togglePwd.setAttribute('aria-expanded','true');
                if (pwdMode){{ pwdMode.value = '1'; }}
              }} else {{
                pwdFields.classList.add('is-hidden');
                togglePwd.textContent = 'Alterar senha';
                togglePwd.setAttribute('aria-expanded','false');
                var inputs = pwdFields.querySelectorAll('input');
                Array.prototype.forEach.call(inputs, function(inp){{ inp.value = ''; }});
                if (pwdMode){{ pwdMode.value = '0'; }}
              }}
            }}
            togglePwd.addEventListener('click', function(e){{
              e.preventDefault();
              var open = pwdFields.classList.contains('is-hidden');
              setState(open);
            }});
            setState(false);
          }}
        }})();
        </script>
        <script>
        (function(){{
          // Deixa os knobs dos switches animados mesmo sem CSS externo
          function hydrateSwitch(selector){{
            var sw = document.querySelector(selector);
            if (!sw) return;
            var ui = sw.parentElement && sw.parentElement.querySelector('.switch-ui');
            var knob = ui && ui.querySelector('.knob');
            function paint(){{
              if (!ui || !knob) return;
              if (sw.checked){{
                ui.style.background = '#4caf50';
                knob.style.left = '22px';
              }} else {{
                ui.style.background = '#2a2a2a';
                knob.style.left = '3px';
              }}
              var label = sw.parentElement && sw.parentElement.querySelector('.muted');
              if (label) label.textContent = sw.checked ? 'Exibindo' : 'Oculto';
            }}
            sw.addEventListener('change', paint);
            paint();
          }}
          hydrateSwitch("input[id='googleReviewShow' name='google_review_show']");
          hydrateSwitch("input[id='featuredEnabled' name='featured_enabled']");
          var featuredColor = document.getElementById('featuredColor');
          var featuredReset = document.getElementById('featuredColorReset');
          if (featuredColor && featuredReset){{
            var defaultColor = featuredColor.getAttribute('data-default-color') || "{FEATURED_DEFAULT_COLOR}";
            featuredReset.addEventListener('click', function(ev){{
              ev.preventDefault();
              featuredColor.value = defaultColor;
            }});
          }}
          const UID = "{html.escape(uid)}";
          var csrfValue = {csrf_token_js};
          window.soomeiCsrfToken = csrfValue;
          var el = document.getElementById('whatsapp');
          var form = document.getElementById('editForm');
          if (!el) return;
          function formatBR(v){{
            // só dígitos
            var d = (v||'').replace(/\D/g,'');
            // força DDI 55 no campo exibido
            if (!d.startsWith('55')) d = '55' + d;
            // 55 + 2 DDD + 9 número = 13 dígitos
            d = d.slice(0, 13);
            var cc  = d.slice(0,2);   // 55
            var ddd = d.slice(2,4);   // DD
            var num = d.slice(4);     // 9 dígitos
            var p1 = num.slice(0,5);  // 90000
            var p2 = num.slice(5,9);  // 0000
            var out = '+' + cc;
            if (ddd) out += ' (' + ddd + ')';
            if (p1)  out += ' ' + p1;
            if (p2)  out += '-' + p2;
            return out;
          }}
          function onInput(){{
            var before = el.value;
            var start = el.selectionStart || before.length;
            el.value = formatBR(before);
            // ajuste simples do cursor
            var diff = el.value.length - before.length;
            var pos = start + (diff > 0 ? diff : 0);
            try {{ el.setSelectionRange(pos, pos); }} catch(_e){{}}
          }}
          // formata ao focar/digitar e na carga inicial (se já vier número cru)
          el.addEventListener('focus', onInput);
          el.addEventListener('input', onInput);
          if (/^\+?\d{{11,13}}$/.test((el.value||'').replace(/\s|[()\-]/g,''))) {{
            el.value = formatBR(el.value);
          }}
          // no submit, envia apenas dígitos (ex.: 5534999999999)
          if (form) {{
            form.addEventListener('submit', function(){{
              el.value = el.value.replace(/\D/g,'').slice(0,13);
            }}, true);
          }}
          var style = document.createElement('style');
          style.textContent = '.hint{{font-size:12px;margin-top:4px}}.ok{{color:#7bd88f}}.bad{{color:#f88}}'
            + '#slugInput.is-ok{{border-color:#43a047;background:rgba(67,160,71,0.09)}}'
            + '#slugInput.is-bad{{border-color:#e53935;background:rgba(229,57,53,0.08)}}'
            + '.tooltip-err{{display:inline-block;background:#2a2211;border:1px solid #4d3b12;color:#e5c17a;padding:6px 8px;border-radius:8px;margin-top:4px}}'
            + '.slug-input-row{{position:relative;display:flex;align-items:center;gap:8px}}'
            + '.icon-btn.icon-sm{{width:26px;height:26px;font-size:14px}}'
            + '.info-tip{{position:absolute;right:0;top:100%;margin-top:6px;display:none;max-width:320px;background:#111114;border:1px solid #242427;border-radius:8px;padding:8px 10px;color:#eaeaea;box-shadow:0 2px 8px rgba(0,0,0,.45);z-index:1000}}'
            + '.info-tip.show{{display:block}}';
            style.textContent +=
              '.modal input{{width:100%;padding:10px;border-radius:10px;' +
              'border:1px solid #2a2a2a;background:#0b0b0c;color:#eaeaea}}';
          document.head.appendChild(style);
          var btn = document.getElementById('addSlug');
          if (!btn) return;
          var loaderCtl = window.soomeiLoader || null;
          var CURRENT = (document.getElementById('slugKey')?.value || '').trim();
          function mountModal(){{
            if (document.getElementById('slugBackdrop')) return;
            var html = ''+
            '<div class="modal-backdrop" id="slugBackdrop" role="dialog" aria-modal="true" aria-labelledby="slugTitle" style="display:none">'+
            '  <div class="modal" id="slugModal">'+
            '    <header><h2 id="slugTitle">Alterar slug</h2><button class="close" id="slugClose" aria-label="Fechar">×</button></header>'+
            '    <div>'+
            '      <label for="slugInput">Novo slug</label>'+
            '      <div class="slug-input-row">'+
            '        <input id="slugInput" placeholder="seu-nome" pattern="[a-z0-9-]{{3,30}}" inputmode="url" autocomplete="off" style="text-transform:lowercase;background:#0b0b0c;color:#eaeaea;border:1px solid #2a2a2a;border-radius:10px;padding:10px;width:100%">'+
            '        <button type="button" class="icon-btn icon-sm" id="slugInfoBtn" aria-label="O que é um slug?" title="O que é um slug?">i</button>'+
            '        <div id="slugInfoTip" class="info-tip" role="tooltip" aria-hidden="true">Slug é o endereço curto da sua URL pública. Use 3-30 caracteres minúsculos, números ou hífen. Ex.: seu-nome</div>'+
            '      </div>'+
            '      <div id="slugMsg" class="hint"></div>'+
            '      <div style="display:flex;gap:6px;align-items:center;margin-top:6px"><span class="muted">Prévia:</span> <code id="slugPreview">/'+(CURRENT||'')+'</code></div>'+
            '      <div style="text-align:right;margin-top:12px"><button class="btn" id="slugSave">Salvar</button></div>'+
            '    </div>'+
            '  </div>'+
            '</div>';
            var tmp = document.createElement('div'); tmp.innerHTML = html; document.body.appendChild(tmp.firstElementChild);
            // fechos
            document.getElementById('slugClose').addEventListener('click', closeModal);
            document.getElementById('slugBackdrop').addEventListener('click', function(e){{
              if (e.target.id === 'slugBackdrop') closeModal();
            }});
            document.addEventListener('keydown', function esc(e){{
              if (e.key === 'Escape') closeModal();
            }});
            document.getElementById('slugInfoBtn').addEventListener('click', function(e){{
              e.preventDefault(); e.stopPropagation();
              var tip = document.getElementById('slugInfoTip');
              tip.classList.toggle('show');
              tip.setAttribute('aria-hidden', tip.classList.contains('show') ? 'false' : 'true');
            }});
            document.addEventListener('click', function(){{
              var tip = document.getElementById('slugInfoTip');
              if (tip){{ tip.classList.remove('show'); tip.setAttribute('aria-hidden','true'); }}
            }});
          }}
          function openModal(){{
            mountModal();
            var bd = document.getElementById('slugBackdrop');
            bd.classList.add('show'); bd.style.display = 'flex';
            var input = document.getElementById('slugInput');
            input.value = CURRENT || '';
            input.focus(); input.select();
            updatePreview();
            // roda uma checagem inicial se já veio preenchido
            if (input.value) debounceCheck(input.value);
          }}
          function closeModal(){{
            var bd = document.getElementById('slugBackdrop');
            if (bd){{ bd.classList.remove('show'); bd.style.display = 'none'; }}
          }}
          function updatePreview(){{
            var input = document.getElementById('slugInput');
            var prev = document.getElementById('slugPreview');
            prev.textContent = '/' + (input.value||'').trim().toLowerCase();
          }}
          var tCheck;
          function debounceCheck(v){{
            clearTimeout(tCheck);
            tCheck = setTimeout(function(){{ checkAvailability(v); }}, 220);
          }}
          async function checkAvailability(v){{
            var el = document.getElementById('slugInput');
            var msg = document.getElementById('slugMsg');
            v = (v||'').trim().toLowerCase();
            if (!v){{ msg.textContent=''; el.classList.remove('is-ok','is-bad'); return; }}
            if (!/^[a-z0-9-]{{3,30}}$/.test(v)){{
              msg.innerHTML = '<span class="bad">Use 3-30 minúsculos/números/hífen.</span>';
              el.classList.remove('is-ok'); el.classList.add('is-bad'); return;
            }}
            try{{
              var r = await fetch('/slug/check?value='+encodeURIComponent(v));
              var j = await r.json();
              if (j && j.available){{
                msg.innerHTML = '<span class="ok">Disponível</span>';
                el.classList.add('is-ok'); el.classList.remove('is-bad');
              }} else {{
                msg.innerHTML = '<span class="bad">Indisponível</span>';
                el.classList.add('is-bad'); el.classList.remove('is-ok');
              }}
            }}catch(_e){{
              msg.textContent=''; el.classList.remove('is-ok','is-bad');
            }}
          }}
          // input listeners
          document.addEventListener('input', function(e){{
            if (e.target && e.target.id === 'slugInput'){{
              var el = e.target;
              var vv = (el.value||''); var ll = vv.toLowerCase(); if (vv !== ll) el.value = ll;
              updatePreview();
              debounceCheck(el.value);
            }}
          }}, true);
          // enter para salvar
          document.addEventListener('keydown', function(e){{
            if (e.key === 'Enter' && document.getElementById('slugBackdrop')?.classList.contains('show')){{
              e.preventDefault();
              document.getElementById('slugSave').click();
            }}
          }}, true);
          // salvar -> POST /slug/select/{{uid}} ; depois volta à edição com o novo slug
          document.addEventListener('click', async function(e){{
            if (e.target && e.target.id === 'slugSave'){{
              e.preventDefault();
              var el = document.getElementById('slugInput');
              var v = (el.value||'').trim().toLowerCase();
              var msg = document.getElementById('slugMsg');
              if (!el.classList.contains('is-ok')){{
                msg.innerHTML = '<span class="bad tooltip-err">Escolha um slug disponível.</span>';
                try{{ el.focus(); }}catch(_e){{}}
                return;
              }}
              if (loaderCtl && loaderCtl.show){{ loaderCtl.show(); }}
              try{{
                var resp = await fetch('/slug/select/'+encodeURIComponent(UID), {{
                  method: 'POST',
                  headers: {{'Content-Type':'application/x-www-form-urlencoded','X-CSRF-Token': csrfValue}},
                  body: 'value='+encodeURIComponent(v)+'&csrf_token='+encodeURIComponent(csrfValue)
                }});
                if (resp.ok){{
                  window.location.href = '/edit/'+encodeURIComponent(v);
                  return;
                }} else if (resp.status === 409){{
                  msg.innerHTML = '<span class="bad">Indisponível, tente outro.</span>';
                }} else {{
                  msg.innerHTML = '<span class="bad">Erro ao salvar. Tente novamente.</span>';
                }}
                if (loaderCtl && loaderCtl.hide){{ loaderCtl.hide(); }}
              }}catch(_e){{
                if (loaderCtl && loaderCtl.hide){{ loaderCtl.hide(); }}
                msg.innerHTML = '<span class="bad">Erro de rede. Tente novamente.</span>';
              }}
            }}
          }}, true);
          // abre modal
          btn.addEventListener('click', function(e){{ e.preventDefault(); openModal(); }});
          async function salvarSlug(novo) {{
            return fetch("/slug/select/" + encodeURIComponent(UID), {{
              method: "POST",
              headers: {{ "Content-Type": "application/x-www-form-urlencoded", "X-CSRF-Token": csrfValue }},
              body: "value=" + encodeURIComponent(novo) + "&csrf_token=" + encodeURIComponent(csrfValue)
            }});
          }}
        }})();
        </script>
      </form>
      <script>
      (function(){{
        var MAX_UPLOAD = {MAX_UPLOAD_BYTES};
        var input = document.getElementById('photoInput');
        if (input) {{
          var img = document.getElementById('avatarImg');
          input.addEventListener('change', function(){{
            var f = input.files && input.files[0];
            if (!f) return;
            var ok = /^(image\/jpeg|image\/png)$/i.test((f.type || ''));
            if (!ok) {{ alert('Formato de imagem nao suportado (use JPEG ou PNG)'); input.value=''; return; }}
            if (f.size > MAX_UPLOAD) {{ alert('Imagem excede 2MB. Escolha uma foto menor.'); input.value=''; return; }}
            var url = URL.createObjectURL(f);
            if (img) {{
              img.src = url;
            }}
          }});
        }}
        var coverInput = document.getElementById('coverInput');
        if (coverInput) {{
          var coverImg = document.getElementById('coverImg');
          var coverPlaceholder = document.getElementById('coverPlaceholder');
          var coverRemoveFlag = document.getElementById('coverRemoveFlag');
          coverInput.addEventListener('change', function(){{
            var f = coverInput.files && coverInput.files[0];
            if (!f) return;
            var ok = /^(image\/jpeg|image\/png)$/i.test((f.type || ''));
            if (!ok) {{ alert('Formato de imagem nao suportado (use JPEG ou PNG)'); coverInput.value=''; return; }}
            if (f.size > MAX_UPLOAD) {{ alert('Imagem excede 2MB. Escolha uma foto menor.'); coverInput.value=''; return; }}
            var reader = new FileReader();
            reader.onload = function(evt){{
              if (coverImg) {{
                coverImg.src = (evt && evt.target && evt.target.result) ? evt.target.result : '';
                coverImg.style.display = coverImg.src ? 'block' : 'none';
              }}
              if (coverPlaceholder && coverImg && coverImg.src) {{
                coverPlaceholder.style.display = 'none';
              }}
              if (coverRemoveFlag) coverRemoveFlag.value = '0';
            }};
            reader.readAsDataURL(f);
          }});
          var coverRemove = document.getElementById('coverRemove');
          if (coverRemove) {{
            coverRemove.addEventListener('click', function(ev){{
              ev.preventDefault();
              if (coverImg) {{ coverImg.src = ''; coverImg.style.display = 'none'; }}
              if (coverPlaceholder) {{ coverPlaceholder.style.display = 'block'; }}
              if (coverRemoveFlag) {{ coverRemoveFlag.value = '1'; }}
              if (coverInput) coverInput.value = '';
            }});
          }}
        }}
        // Atualiza preview de cor imediatamente ao selecionar
        var colorEl = document.getElementById('themeColor');
        var prev = document.getElementById('colorPreview');
        function updateColor(){{
          if (!colorEl || !prev) return;
          var c = (colorEl.value||'').trim();
          if (/^#[0-9a-fA-F]{6}$/.test(c)) {{ prev.style.backgroundColor = c + '30'; }}
        }}
       if (colorEl && prev) {{
          colorEl.addEventListener('input', updateColor);
          colorEl.addEventListener('change', updateColor);
          // Inicializa preview na primeira carga
          updateColor();
        }}
       }})();
       // Modal de URL personalizada
      (function(){{
        var trigger = document.getElementById('manageCustomDomain');
        var dataEl = document.getElementById('customDomainData');
        var statusEl = document.getElementById('customDomainStatus');
        if (!trigger || !dataEl || !statusEl) return;
        var slugId = "{html.escape(slug)}";
        function esc(str){{
          return (str || '').replace(/[&<>"']/g, function(ch){{
            return {{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }}[ch] || ch;
          }});
        }}
        var modal = document.createElement('div');
        modal.className = 'modal-backdrop';
        modal.setAttribute('role','dialog');
        modal.setAttribute('aria-modal','true');
        modal.setAttribute('aria-hidden','true');
        modal.innerHTML = `
        <div class='modal'>
          <header>
            <h2>URL personalizada</h2>
            <button class='close' id='customDomainClose' aria-label='Fechar' title='Fechar'>&#10005;</button>
          </header>
          <div>
            <p><strong>Como funciona:</strong></p>
            <ol style='padding-left:18px;font-size:13px;color:#9aa0a6'>
              <li>Crie um subdomínio exclusivo do seu site (ex.: nome.suaempresa.com).</li>
              <li>Adicione um registro <strong>CNAME</strong> apontando para <code>{html.escape(custom_domain_target)}</code>.</li>
              <li>Envie o pedido para a Soomei aprovar e liberar o certificado SSL.</li>
            </ol>
            <label for='customDomainInput'>Domínio solicitado</label>
            <input id='customDomainInput' placeholder='ex.: nome.suaempresa.com.br' style='width:100%;margin:8px 0;padding:10px;border-radius:10px;border:1px solid #2a2a2a;background:#0b0b0c;color:#eaeaea'>
            <div class='panel-actions' style='margin-top:8px'>
              <button id='customDomainSubmit' class='btn'>Enviar pedido</button>
              <button id='customDomainCancelBtn' class='btn ghost'>Cancelar solicitação</button>
              <button id='customDomainRemoveBtn' class='btn ghost' style='display:none'>Remover URL ativa</button>
            </div>
            <div id='customDomainFeedback' class='banner' style='display:none;margin-top:10px'></div>
            <p class='muted hint'>Dica: mantenha o registro CNAME enquanto aguarda a validação para que o SSL seja emitido automaticamente.</p>
          </div>
        </div>`;
        document.body.appendChild(modal);
        var closeBtn = modal.querySelector('#customDomainClose');
        var submitBtn = modal.querySelector('#customDomainSubmit');
        var cancelBtn = modal.querySelector('#customDomainCancelBtn');
        var removeBtn = modal.querySelector('#customDomainRemoveBtn');
        var input = modal.querySelector('#customDomainInput');
        var feedback = modal.querySelector('#customDomainFeedback');
        function currentState(){{
          return {{
            status: (dataEl.getAttribute('data-status')||'').toLowerCase(),
            active: dataEl.getAttribute('data-active')||'',
            requested: dataEl.getAttribute('data-requested')||''
          }};
        }}
        function statusLabel(code){{
          var labels = {{
            pending: 'Aguardando aprovação',
            active: 'Ativo',
            rejected: 'Reprovado',
            disabled: 'Desativado'
          }};
          return labels[code] || 'Sem solicitação';
        }}
        function updateStatus(){{
          var state = currentState();
          var parts = [];
          if (state.active) parts.push('Ativo: https://' + esc(state.active));
          if (state.status === 'pending' && state.requested) parts.push('Pendente: ' + esc(state.requested));
          if (!parts.length) parts.push('Nenhuma URL personalizada configurada.');
          statusEl.innerHTML = 'Status: <strong>' + esc(statusLabel(state.status)) + '</strong><br>' + parts.join('<br>');
        }}
        function setState(partial){{
          if (typeof partial.status !== 'undefined') dataEl.setAttribute('data-status', partial.status || '');
          if (typeof partial.active !== 'undefined') dataEl.setAttribute('data-active', partial.active || '');
          if (typeof partial.requested !== 'undefined') dataEl.setAttribute('data-requested', partial.requested || '');
          updateStatus();
          updateButtons();
        }}
        function updateButtons(){{
          var state = currentState();
          if (state.status === 'pending' && state.requested){{
            input.value = state.requested;
          }} else if (state.active){{
            input.value = state.active;
          }} else {{
            input.value = '';
          }}
          cancelBtn.style.display = (state.status === 'pending' && !!state.requested) ? '' : 'none';
          removeBtn.style.display = state.active ? '' : 'none';
        }}
        function setFeedback(msg, ok){{
          if (!feedback) return;
          feedback.textContent = msg;
          feedback.style.display = msg ? 'block' : 'none';
          feedback.className = 'banner ' + (ok ? 'ok' : 'bad');
        }}
        function hideModal(){{
          modal.classList.remove('show');
          modal.setAttribute('aria-hidden','true');
        }}
        function showModal(){{
          modal.classList.add('show');
          modal.setAttribute('aria-hidden','false');
          updateButtons();
          setFeedback('', true);
        }}
        function post(url, payload){{
          setFeedback('Enviando...', true);
          var fd = new FormData();
          if (payload && payload.host) fd.append('host', payload.host);
          fetch(url, {{ method: 'POST', body: fd }})
            .then(function(resp){{
              return resp.json().then(function(data){{ return {{ ok: resp.ok, data: data }}; }});
            }})
            .then(function(res){{
              if (!res.ok){{
                var msg = (res.data && res.data.message) ? res.data.message : 'Falha ao processar solicitação.';
                setFeedback(msg, false);
                return;
              }}
              if (typeof res.data.status !== 'undefined'){{
                dataEl.setAttribute('data-status', (res.data.status || '').toLowerCase());
              }}
              if (typeof res.data.active_host !== 'undefined'){{
                dataEl.setAttribute('data-active', res.data.active_host || '');
              }}
              if (typeof res.data.requested_host !== 'undefined'){{
                dataEl.setAttribute('data-requested', res.data.requested_host || '');
              }} else if (!res.data.requested_host){{
                dataEl.setAttribute('data-requested', '');
              }}
              updateStatus();
              updateButtons();
              setFeedback('Tudo certo! Atualizamos sua solicitação.', true);
            }}).catch(function(){{
              setFeedback('Não foi possível concluir a solicitação.', false);
            }});
        }}
        trigger.addEventListener('click', function(ev){{
          ev.preventDefault();
          showModal();
        }});
        modal.addEventListener('click', function(ev){{
          if (ev.target === modal) hideModal();
        }});
        if (closeBtn) closeBtn.addEventListener('click', function(){{
          hideModal();
        }});
        if (submitBtn) submitBtn.addEventListener('click', function(ev){{
          ev.preventDefault();
          var v = (input.value || '').trim();
          if (!v){{
            setFeedback('Informe o domínio que deseja usar.', false);
            return;
          }}
          post('/custom-domain/request/' + slugId, {{ host: v }});
        }});
        if (cancelBtn) cancelBtn.addEventListener('click', function(ev){{
          ev.preventDefault();
          post('/custom-domain/withdraw/' + slugId);
        }});
        if (removeBtn) removeBtn.addEventListener('click', function(ev){{
          ev.preventDefault();
          if (!confirm('Remover a URL personalizada ativa? Isso desativa o domínio imediatamente.')) return;
          post('/custom-domain/remove/' + slugId);
        }});
        updateStatus();
        updateButtons();
      }})();
       // Modal para escolher tipo e valor da chave Pix
      (function(){{
        var ap = document.getElementById('addPix');
        if (!ap) return;
        var modal = document.createElement('div');
        modal.className = 'modal-backdrop';
        modal.setAttribute('role','dialog');
        modal.setAttribute('aria-modal','true');
        modal.setAttribute('aria-hidden','true');
        modal.innerHTML = `
        <div class='modal'>
          <header>
            <h2>Adicionar chave Pix</h2>
            <button class='close' id='pixClose' aria-label='Fechar' title='Fechar'>&#10005;</button>
          </header>
          <div>
            <label for='pixType'>Tipo da chave</label>
            <select id='pixType' style='width:100%;margin:8px 0;padding:10px;border-radius:10px;border:1px solid #2a2a2a;background:#0b0b0c;color:#eaeaea'>
              <option value='aleatoria'>Aleatória</option>
              <option value='email'>E-mail</option>
              <option value='telefone'>Telefone</option>
              <option value='cpf'>CPF</option>
              <option value='cnpj'>CNPJ</option>
            </select>
            <label for='pixValue'>Valor da chave</label>
            <input id='pixValue' placeholder='sua-chave' style='width:100%;margin:8px 0;padding:10px;border-radius:10px;border:1px solid #2a2a2a;background:#0b0b0c;color:#eaeaea'>
            <div style='text-align:right'><a href='#' id='pixSave' class='btn'>Salvar</a></div>
          </div>
        </div>
        `;
        document.body.appendChild(modal);
        function showM(){{ modal.classList.add('show'); modal.setAttribute('aria-hidden','false'); }}
        function hideM(){{ modal.classList.remove('show'); modal.setAttribute('aria-hidden','true'); }}
        ap.addEventListener('click', function(e){{ e.preventDefault(); showM(); }});
        modal.addEventListener('click', function(e){{ if(e.target===modal) hideM(); }});
        var close = modal.querySelector('#pixClose'); if (close) close.addEventListener('click', function(){{ hideM(); }});
        var save = modal.querySelector('#pixSave');
        var val = modal.querySelector('#pixValue');
        var type = modal.querySelector('#pixType');
        var hidden = document.getElementById('pixKey');
        if (save && val && hidden){{
          save.addEventListener('click', function(e){{ e.preventDefault();
            var v = (val.value||'').trim(); if (!v) return;
            hidden.value = v; hideM();
            ap.textContent = 'Chave Pix definida';
          }});
        }}
        // Remover chave Pix existente
        var del = document.getElementById('pixDel');
        var info = document.getElementById('pixInfo');
        if (del && info && hidden){{
          del.addEventListener('click', function(e){{
            e.preventDefault();
            hidden.value = '';
            info.textContent = 'Chave Pix removida';
          }});
        }}
      }})();
      </script>
      {portfolio_script_block}
    </main></body></html>
    """
    response = HTMLResponse(_apply_brand_footer(html_form, footer_action_html))
    csrf.set_csrf_cookie(response, csrf_token_value)
    if saved_cookie:
        response.delete_cookie("flash_edit_saved", path="/edit")
    if pwd_cookie:
        response.delete_cookie("flash_edit_pwd", path="/edit")
    return response
@router.post("/{slug}")
async def save_edit(slug: str, request: Request, full_name: str = Form(""), title: str = Form(""),
                whatsapp: str = Form(""), email_public: str = Form(""), site_url: str = Form(""), address: str = Form(""),
                google_review_url: str = Form(""),
                google_review_show: str = Form(""),
                featured_label: str = Form(""),
                featured_url: str = Form(""),
               featured_color: str = Form("#FFB473"),
               featured_enabled: str = Form(""),
               label1: str = Form(""), href1: str = Form(""),
               label2: str = Form(""), href2: str = Form(""),
               label3: str = Form(""), href3: str = Form(""),
               label4: str = Form(""), href4: str = Form(""),
               theme_color: str = Form(""),
               pix_key: str = Form(""),
               current_password: str = Form(""),
               new_password: str = Form(""),
               confirm_password: str = Form(""),
               password_mode: str = Form("0"),
                cover_remove: str = Form("0"),
                portfolio_enabled: str = Form(""),
                portfolio_remove1: str = Form("0"),
                portfolio_remove2: str = Form("0"),
                portfolio_remove3: str = Form("0"),
                portfolio_remove4: str = Form("0"),
                portfolio_remove5: str = Form("0"),
                photo: UploadFile | None = File(None),
                cover: UploadFile | None = File(None),
                portfolio1: UploadFile | None = File(None),
                portfolio2: UploadFile | None = File(None),
                portfolio3: UploadFile | None = File(None),
                portfolio4: UploadFile | None = File(None),
                portfolio5: UploadFile | None = File(None),
                csrf_token: str = Form("")):
    db, uid, card = find_card_by_slug(slug)
    if not card:
        raise HTTPException(404, "Cartao nao encontrado")
    if uid and card:
        _sql_repo.sync_card_from_json(uid, card)
    owner = card.get("user", "")
    who = current_user_email(request)
    if who != owner:
        return RedirectResponse(f"/{slug}", status_code=303)
    csrf.validate_csrf(request, csrf_token)
    def redirect_error(msg: str):
        return RedirectResponse(f"/edit/{slug}?error={urlparse.quote_plus(msg)}", status_code=303)
    prof = _sql_repo.get_profile(owner) or {}
    prof.update({
        "full_name": full_name.strip(),
        "title": title.strip(),
        "whatsapp": sanitize_phone(whatsapp),
        "email_public": email_public.strip(),
        "site_url": (site_url or "").strip(),
        "address": (address or "").strip(),
        "google_review_url": (google_review_url or "").strip(),
        "google_review_show": bool(google_review_show),
        "featured_color": _normalize_hex_color(featured_color, "#FFB473"),
    })
    feat_label_value = (featured_label or "").strip()
    feat_url_value = (featured_url or "").strip()
    feat_enabled_flag = bool(featured_enabled)
    if feat_label_value and feat_url_value:
        prof["featured_label"] = feat_label_value[:60]
        prof["featured_url"] = normalize_external_url(feat_url_value)
    else:
        prof["featured_label"] = ""
        prof["featured_url"] = ""
    prof["featured_enabled"] = feat_enabled_flag
    prof["featured_color"] = _normalize_hex_color(featured_color, FEATURED_DEFAULT_COLOR)
    # Atualiza chave Pix (pode ser vazia para limpar)
    prof["pix_key"] = (pix_key or "").strip()
    # Salva cor do tema (hex #RRGGBB)
    tc = (theme_color or "").strip()
    if not re.fullmatch(r"#([0-9a-fA-F]{6})", tc or ""):
        tc = "#000000"
    prof["theme_color"] = tc
    links = []
    for (lbl, href) in [(label1, href1), (label2, href2), (label3, href3), (label4, href4)]:
        if lbl.strip() and href.strip():
            links.append({"label": lbl.strip(), "href": href.strip()})
    prof["links"] = links
    portfolio_enabled_flag = bool(portfolio_enabled)
    existing_portfolio = prof.get("portfolio_images") or []
    if not isinstance(existing_portfolio, list):
        existing_portfolio = []
    portfolio_slots = [(p or "").strip() for p in existing_portfolio[:5]]
    while len(portfolio_slots) < 5:
        portfolio_slots.append("")
    allowed_types = {"image/jpeg", "image/png", "image/jpg", "image/pjpeg"}
    remove_flags = [
        (portfolio_remove1 or "").strip(),
        (portfolio_remove2 or "").strip(),
        (portfolio_remove3 or "").strip(),
        (portfolio_remove4 or "").strip(),
        (portfolio_remove5 or "").strip(),
    ]
    for idx, flag in enumerate(remove_flags):
        if flag == "1":
            portfolio_slots[idx] = ""
    portfolio_files = [portfolio1, portfolio2, portfolio3, portfolio4, portfolio5]
    safe_uid = re.sub(r"[^A-Za-z0-9_-]+", "", uid)
    uid_dir = safe_uid or uid
    for idx, file_obj in enumerate(portfolio_files):
        if file_obj and file_obj.filename:
            ct = (file_obj.content_type or "").lower()
            if ct not in allowed_types:
                return redirect_error("Formato de imagem nao suportado (use JPEG ou PNG).")
            data = await file_obj.read()
            if not data:
                return redirect_error("Imagem vazia.")
            if len(data) > MAX_UPLOAD_BYTES:
                return redirect_error("Imagem excede 2MB.")
            if not _has_valid_signature(data, ct):
                return redirect_error("Arquivo de imagem invalido.")
            portfolio_slots[idx] = _save_resized_image(data, f"{uid_dir}/portfolio_{idx+1}.jpg", (1600, 900))
    portfolio_clean = [p for p in portfolio_slots if p]
    prof["portfolio_images"] = portfolio_clean
    prof["portfolio_enabled"] = portfolio_enabled_flag and bool(portfolio_clean)
    pwd_changed = False
    current_password = (current_password or "").strip()
    new_password = (new_password or "").strip()
    confirm_password = (confirm_password or "").strip()
    password_mode = (password_mode or "").strip()
    wants_pwd = password_mode == "1"
    if wants_pwd:
        if not (current_password and new_password and confirm_password):
            return redirect_error("Preencha todos os campos de senha.")
        if len(new_password) < 8:
            return redirect_error("Nova senha deve ter no minimo 8 caracteres.")
        if new_password != confirm_password:
            return redirect_error("As senhas nao conferem.")
        user = _sql_repo.get_user(owner)
        if not user or not verify_password(current_password, user.password_hash):
            return redirect_error("Senha atual incorreta.")
        _sql_repo.update_user_password(owner, hash_password(new_password))
        pwd_changed = True
    if photo and photo.filename:
        ct = (photo.content_type or "").lower()
        if ct not in allowed_types:
            return redirect_error("Formato de imagem nao suportado (use JPEG ou PNG).")
        data = await photo.read()
        if not data:
            return redirect_error("Imagem vazia.")
        if len(data) > MAX_UPLOAD_BYTES:
            return redirect_error("Imagem excede 2MB.")
        if not _has_valid_signature(data, ct):
            return redirect_error("Arquivo de imagem invalido.")
        prof["photo_url"] = _save_resized_image(data, f"{uid}.jpg", (800, 800))
    if (cover_remove or "").strip() == "1":
        prof["cover_url"] = ""
    elif cover and cover.filename:
        ct = (cover.content_type or "").lower()
        if ct not in allowed_types:
            return redirect_error("Formato de imagem nao suportado (use JPEG ou PNG).")
        data = await cover.read()
        if not data:
            return redirect_error("Imagem vazia.")
        if len(data) > MAX_UPLOAD_BYTES:
            return redirect_error("Imagem excede 2MB.")
        if not _has_valid_signature(data, ct):
            return redirect_error("Arquivo de imagem invalido.")
        prof["cover_url"] = _save_resized_image(data, f"{uid}_cover.jpg", (1600, 900))
    _sql_repo.upsert_profile(owner, prof)
    # Redireciona sempre para a página pública após salvar
    return RedirectResponse(f"/{slug}", status_code=303)
