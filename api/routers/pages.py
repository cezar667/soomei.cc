from __future__ import annotations

import html
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from api.repositories.json_storage import db_defaults, load

router = APIRouter(prefix="", tags=["pages"])

CSS_HREF = "/static/card.css"
BRAND_FOOTER = lambda content: content
LEGAL_TERMS_PATH = ""


def configure_pages(*, css_href: str, brand_footer, legal_terms_path: str) -> None:
    """Configure shared assets for onboarding/legal routes."""
    global CSS_HREF, BRAND_FOOTER, LEGAL_TERMS_PATH
    CSS_HREF = css_href or "/static/card.css"
    BRAND_FOOTER = brand_footer or (lambda content: content)
    LEGAL_TERMS_PATH = legal_terms_path or ""


def _templates(request: Request):
    tpl = getattr(getattr(request.app, "state", None), "templates", None)
    if tpl:
        return tpl
    raise RuntimeError("Templates nao configurados")


def _apply_brand_footer(content: str) -> str:
    return BRAND_FOOTER(content) if BRAND_FOOTER else content


@router.get("/onboard/{uid}", response_class=HTMLResponse)
def onboard(uid: str, email: str = "", vanity: str = "", error: str = ""):
    db = db_defaults(load())
    uid_exists = uid in db.get("cards", {})
    if not uid_exists:
        return RedirectResponse("/invalid", status_code=302)
    card_meta = db.get("cards", {}).get(uid, {})
    status = (card_meta.get("status") or "").lower()
    if status == "active":
        dest = card_meta.get("vanity", uid)
        return RedirectResponse(f"/{html.escape(dest)}", status_code=302)
    if status == "blocked":
        return RedirectResponse("/blocked", status_code=302)
    show_welcome = True
    welcome_class = "modal-backdrop show" if show_welcome else "modal-backdrop"
    welcome_aria = "false" if show_welcome else "true"
    html_doc = f"""
    <!doctype html><html lang='pt-br'><head>
    <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <link rel='stylesheet' href='{CSS_HREF}'><title>Ativar cartao</title>
    <style>
      .terms-row{{display:flex;align-items:center;gap:8px;margin:10px 0 0}}
      .terms-label{{display:flex;align-items:center;gap:10px}}
      .terms-row input[type=checkbox]{{margin:0}}
      .terms-link{{color:#8ab4f8;text-decoration:none;font-weight:600}}
      .terms-text{{line-height:1.2}}
      .onboard-cta{{margin-top:18px}}
      .hint{{font-size:12px;margin:4px 0 0}}
      .ok{{color:#7bd88f}}
      .bad{{color:#f88}}
      .nice-check{{appearance:none;-webkit-appearance:none;width:18px;height:18px;border:2px solid #8ab4f8;border-radius:4px;background:#0b0b0c;display:inline-block;position:relative;cursor:pointer}}
      .nice-check:focus-visible{{outline:2px solid #8ab4f8;outline-offset:2px}}
      .nice-check:checked{{background:#8ab4f8;border-color:#8ab4f8}}
      .nice-check:checked::after{{content:'';position:absolute;left:4px;top:1px;width:5px;height:10px;border:2px solid #0b0b0c;border-top:0;border-left:0;transform:rotate(45deg)}}
      .modal-backdrop{{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;z-index:1000}}
      .modal-backdrop.show{{display:flex}}
      .modal{{background:#111114;border:1px solid #242427;border-radius:12px;max-width:840px;width:92%;max-height:85vh;overflow:auto;padding:12px}}
      .modal header{{display:flex;justify-content:space-between;align-items:center;margin:4px 6px 10px}}
      .modal header h2{{margin:0;font-size:18px;color:#eaeaea}}
      .modal .close{{background:#ffffff;color:#0b0b0c;border:1px solid #e5e7eb;border-radius:999px;width:30px;height:30px;display:inline-flex;align-items:center;justify-content:center;cursor:pointer}}
      .modal iframe{{width:100%;height:70vh;border:0;background:#0b0b0c}}
    </style>
    </head>
    <body><main class='wrap'>
      <h1>Ativar cartao</h1>
      <p>UID: <b>{html.escape(uid)}</b></p>
      <div id='formAlert' class='banner' role='alert' aria-live='polite' style='display:{'block' if (error or '').strip() else 'none'}'>{html.escape(error)}</div>
      <form id='onbForm' method='post' action='/auth/register'>
        <input type='hidden' name='uid' value='{html.escape(uid)}'>
        <label>Email</label><input id='emailInput' name='email' type='email' required value='{html.escape(email)}' autocapitalize='none' autocomplete='email' autocorrect='off'>
        <div id='emailMsg' class='hint'></div>
        <label>PIN do cartão</label><input name='pin' type='password' required inputmode='numeric' pattern='[0-9]*' autocomplete='one-time-code' placeholder='Somente números'>
        <div class='hint'>Dica: PIN possui apenas números.</div>
        <label>Senha</label><input name='password' type='password' minlength='8' placeholder='Mínimo 8 caracteres'>
        <label>Slug (opcional)</label><input id='vanityInput' name='vanity' placeholder='seu-nome' pattern='[a-z0-9-]{{3,30}}' value='{html.escape(vanity)}'>
        <div id='slugMsg' class='hint'>Use 3-30 caracteres, todos minusculos, sem caracteres especiais</div>
        <div class='terms-row'>
          <label class='terms-label'>
            <input class='nice-check' type='checkbox' name='lgpd' required>
            <span class='terms-text'>Concordo com os <a href='#' id='openTerms' class='terms-link'>Termos e Privacidade</a>.</span>
          </label>
        </div>
        <button class='btn onboard-cta'>Criar conta</button>
      </form>
      <p class='muted'><a href='/login'>Já tenho conta</a></p>
    </main>
    <div id='termsBackdrop' class='modal-backdrop' role='dialog' aria-modal='true' aria-hidden='true'>
      <div class='modal'>
        <header><h2>Termos e privacidade</h2><button id='closeTerms' class='close' aria-label='Fechar'>&#10005;</button></header>
        <iframe src='/legal/terms'></iframe>
      </div>
    </div>
    <div id='welcomeBackdrop' class='{welcome_class}' role='dialog' aria-modal='true' aria-hidden='{welcome_aria}'>
      <div class='modal'>
        <header><h2>Como funciona?</h2><button id='closeWelcome' class='close' aria-label='Fechar'>&#10005;</button></header>
        <div class='modal-content'>
          <ol>
            <li>Confirme seu e-mail com o link enviado.</li>
            <li>Criar senha forte (mín. 8 caracteres).</li>
            <li>Leia e concorde com os Termos e Privacidade.</li>
            <li>Clique em “Criar conta” e confirme seu e-mail.</li>
          </ol>
          <p class='muted'>Dica: mantenha seu PIN em sigilo. Se tiver dúvidas, fale com o suporte.</p>
          <div style='text-align:right;margin-top:10px'>
            <a href='#' class='btn' id='startWelcome'>Vamos começar</a>
          </div>
        </div>
      </div>
    </div>
    <script>
      (function(){{
        var open = document.getElementById('openTerms');
        var back = document.getElementById('termsBackdrop');
        var close = document.getElementById('closeTerms');
        if (open && back && close){{
          open.addEventListener('click', function(e){{ e.preventDefault(); back.classList.add('show'); back.setAttribute('aria-hidden','false'); }});
          close.addEventListener('click', function(){{ back.classList.remove('show'); back.setAttribute('aria-hidden','true'); }});
          back.addEventListener('click', function(e){{ if(e.target===back){{ back.classList.remove('show'); back.setAttribute('aria-hidden','true'); }} }});
        }}
        var wback = document.getElementById('welcomeBackdrop');
        var wclose = document.getElementById('closeWelcome');
        var wstart = document.getElementById('startWelcome');
        function hideWelcome(){{ if(!wback) return; wback.classList.remove('show'); wback.setAttribute('aria-hidden','true'); }}
        if (wclose){{ wclose.addEventListener('click', function(){{ hideWelcome(); }}); }}
        if (wstart){{ wstart.addEventListener('click', function(e){{ e.preventDefault(); hideWelcome(); }}); }}
        if (wback){{ wback.addEventListener('click', function(e){{ if(e.target===wback){{ hideWelcome(); }} }}); }}
        function setMsg(el, ok, text){{ if(!el)return; el.innerHTML = ok ? "<span class='ok'>"+text+"</span>" : "<span class='bad'>"+text+"</span>"; }}
        var emailEl = document.getElementById('emailInput');
        var emailMsg = document.getElementById('emailMsg');
        var slugEl = document.getElementById('vanityInput');
        var slugMsg = document.getElementById('slugMsg');
        var t2;
        (function(){{
          var form = document.getElementById('onbForm');
          if (!(form && emailEl && emailMsg)) return;
          form.addEventListener('submit', function(e){{
            var alertBox = document.getElementById('formAlert');
            function fail(msg, focusEl){{ try{{ e.preventDefault(); e.stopImmediatePropagation(); }}catch(_e){{}} if(alertBox){{ alertBox.textContent=msg; alertBox.style.display='block'; }} if(focusEl){{ try{{ focusEl.focus(); }}catch(_e){{}} }} }}
            var vEmail = (emailEl.value||'').trim();
            if (!vEmail) return;
            var pinEl = form.querySelector('input[name="pin"]');
            var pwdEl = form.querySelector('input[name="password"]');
            var lgpdEl = form.querySelector('input[name="lgpd"]');
            var vPin = (pinEl && pinEl.value||'').trim();
            var vPwd = (pwdEl && pwdEl.value||'').trim();
            if (lgpdEl && !lgpdEl.checked){{ return fail('É necessário aceitar os termos para continuar.', lgpdEl); }}
            if (vPwd && vPwd.length < 8){{ return fail('Senha muito curta. Use no mínimo 8 caracteres.', pwdEl); }}
            if (vPin && /[^0-9]/.test(vPin)){{ return fail('PIN deve conter apenas números.', pinEl); }}
            if (slugEl){{ slugEl.value = (slugEl.value||'').toLowerCase(); }}
            try{{ e.preventDefault(); e.stopImmediatePropagation(); }}catch(_e){{}}
            emailMsg.innerHTML = '';
            var btn = form.querySelector('button'); if (btn) btn.disabled = true;
            fetch('/auth/check_email?value='+encodeURIComponent(vEmail))
              .then(function(r){{ return r.json(); }})
              .then(function(j){{ if (j && j.available){{ form.submit(); }} else {{ setMsg(emailMsg, false, 'E-mail ja cadastrado'); if (btn) btn.disabled = false; emailEl.focus(); }} }})
              .catch(function(){{ if (btn) btn.disabled = false; form.submit(); }});
          }}, true);
        }})();
        if (slugEl && slugMsg){{
          slugEl.addEventListener('input', function(){{
            clearTimeout(t2);
            var v = (slugEl.value||'').trim();
            if (!v) {{ slugMsg.innerHTML=''; return; }}
            var lower = v.toLowerCase(); if (v !== lower){{ slugEl.value = lower; v = lower; }}
            if (!/^[a-z0-9-]{3,30}$/.test(v)) {{ setMsg(slugMsg, false, 'Use 3-30 caracteres, todos minusculos, sem caracteres especiais'); return; }}
            t2 = setTimeout(async function(){{
              try {{
                var r = await fetch('/slug/check?value='+encodeURIComponent(v));
                var j = await r.json();
                setMsg(slugMsg, j.available, j.available ? 'Disponivel' : 'Indisponivel');
              }} catch(_e) {{ slugMsg.innerHTML=''; }}
            }}, 250);
          }});
          slugEl.addEventListener('input', function(){{ var vv = slugEl.value||''; var ll = vv.toLowerCase(); if (vv!==ll) slugEl.value=ll; }});
        }}
        var colorEl = document.getElementById('themeColor');
        var prev = document.getElementById('colorPreview');
        if (colorEl && prev){{
          colorEl.addEventListener('input', function(){{
            var c = (colorEl.value||'').trim();
            if (/^#[0-9a-fA-F]{6}$/.test(c)){{ prev.style.backgroundColor = c + '30'; }}
          }});
        }}
      }})();
    </script>
    </body></html>
    """
    return HTMLResponse(_apply_brand_footer(html_doc))


@router.get("/login", response_class=HTMLResponse)
def login(request: Request, uid: str = "", error: str = ""):
    templates = _templates(request)
    return templates.TemplateResponse(
        "login.html", {"request": request, "uid": uid, "error": error}
    )


@router.get("/invalid", response_class=HTMLResponse)
def invalid(request: Request):
    templates = _templates(request)
    return templates.TemplateResponse("invalid.html", {"request": request})


@router.get("/legal/terms", response_class=HTMLResponse)
def legal_terms(request: Request):
    if not LEGAL_TERMS_PATH:
        return HTMLResponse("<h1>Termos indisponiveis</h1>", status_code=404)
    path = LEGAL_TERMS_PATH
    if not os.path.exists(path):
        return HTMLResponse("<h1>Termos indisponiveis</h1>", status_code=404)
    with open(path, "r", encoding="utf-8") as handle:
        txt = handle.read()
    safe = html.escape(txt).replace("\n", "<br>")
    templates = _templates(request)
    return templates.TemplateResponse(
        "legal_terms.html", {"request": request, "safe": safe}
    )


@router.post("/auth/register")
# Silencia requisições de debug do Chrome (evita 404 ruidoso em logs)
@router.get("/.well-known/appspecific/com.chrome.devtools.json")
def chrome_devtools_wellknown():
    return PlainTextResponse("", status_code=204)
