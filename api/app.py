from fastapi import FastAPI, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os, json, html, qrcode, io, hashlib, time, secrets, re
import urllib.parse as urlparse

app = FastAPI(title="Soomei Card API v2")

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "data.json")
WEB = os.path.join(BASE, "..", "web")
app.mount("/static", StaticFiles(directory=WEB), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE, "..", "templates"))

PUBLIC_BASE = os.getenv("PUBLIC_BASE_URL", "https://soomei.cc").rstrip("/")
UPLOADS = os.path.join(WEB, "uploads")
os.makedirs(UPLOADS, exist_ok=True)


def load():
    if os.path.exists(DATA):
        with open(DATA, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"users": {}, "cards": {}, "profiles": {}, "sessions": {}, "verify_tokens": {}}


def save(db):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def h(p: str) -> str:
    return hashlib.scrypt(p.encode(), salt=b"soomei", n=2**14, r=8, p=1).hex()


def db_defaults(db):
    db.setdefault("users", {})
    db.setdefault("cards", {})
    db.setdefault("profiles", {})
    db.setdefault("sessions", {})
    db.setdefault("verify_tokens", {})
    return db


def issue_session(db, email: str) -> str:
    token = secrets.token_urlsafe(32)
    db["sessions"][token] = {"email": email, "ts": int(time.time())}
    save(db)
    return token


def current_user_email(request: Request):
    token = request.cookies.get("session")
    if not token:
        return None
    db = db_defaults(load())
    s = db["sessions"].get(token)
    return s.get("email") if s else None


RESERVED_SLUGS = {"onboard", "login", "auth", "q", "v", "u", "static", "blocked", "edit", "hooks", "slug"}


def is_valid_slug(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9-]{3,30}", value or "")) and value not in RESERVED_SLUGS


def slug_in_use(db, value: str) -> bool:
    for _uid, c in db.get("cards", {}).items():
        if c.get("vanity") == value:
            return True
    return False


def sanitize_phone(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    keep_plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    return ("+" + digits) if keep_plus else digits


def find_card_by_slug(slug: str):
    db = db_defaults(load())
    for uid, c in db["cards"].items():
        if c.get("vanity") == slug:
            return db, uid, c
    if slug in db["cards"]:
        return db, slug, db["cards"][slug]
    return db, None, None


def profile_complete(prof: dict) -> bool:
    if not prof:
        return False
    name = (prof.get("full_name") or "").strip()
    has_name = bool(name and name != "Seu Nome")
    has_contact = bool(
        (prof.get("whatsapp") or "").strip()
        or (prof.get("email_public") or "").strip()
        or (isinstance(prof.get("links"), list) and len(prof.get("links") or []) > 0)
    )
    return has_name and has_contact


def _brand_footer_inject(html_doc: str) -> str:
    snippet = "\n    <footer class='muted' style='text-align:center'>&copy; 2025 Soomei</footer>\n  "
    return html_doc.replace("</body>", snippet + "</body>", 1) if "</body>" in html_doc else (html_doc + snippet)


@app.get("/onboard/{uid}", response_class=HTMLResponse)
def onboard(uid: str, email: str = "", vanity: str = "", error: str = ""):
    db = db_defaults(load())
    uid_exists = uid in db.get("cards", {})
    # Redirecionamentos iniciais conforme status/validade
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
    <link rel='stylesheet' href='/static/card.css?v=20251026'><title>Ativar cartao</title>
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
      <p style='color:#f88'>{html.escape(error)}</p>
      <form id='onbForm' method='post' action='/auth/register'>
        <input type='hidden' name='uid' value='{html.escape(uid)}'>
        <label>Email</label><input id='emailInput' name='email' type='email' required value='{html.escape(email)}'>
        <div id='emailMsg' class='hint'></div>
        <label>PIN da carta</label><input name='pin' type='password' required>
        <label>Nova senha</label><input name='password' type='password' required>
        <label>Slug (opcional)</label><input id='vanityInput' name='vanity' placeholder='seu-nome' pattern='[a-z0-9-]{3,30}' value='{html.escape(vanity)}'>
        <div id='slugMsg' class='hint'></div>
        <div class='terms-row'>
          <label class='terms-label'>
            <input class='nice-check' type='checkbox' name='lgpd' required>
            <span class='terms-text'>Concordo com os <a href='#' id='openTerms' class='terms-link'>Termos e Privacidade</a></span>
          </label>
        </div>
        <button class='btn onboard-cta' {'disabled' if not uid_exists else ''}>Criar conta</button>
      </form>
      <div id='termsBackdrop' class='modal-backdrop' role='dialog' aria-modal='true' aria-hidden='true'>
        <div class='modal'>
          <header>
            <h2>Termos de Uso e Privacidade</h2>
            <button class='close' id='closeTerms' aria-label='Fechar' title='Fechar'>&#10005;</button>
          </header>
          <iframe id='termsFrame' src='/legal/terms'></iframe>
        </div>
      </div>
      <div id='welcomeBackdrop' class='{welcome_class}' role='dialog' aria-modal='true' aria-hidden='{welcome_aria}'>
        <div class='modal'>
          <header>
            <h2>Bem-vindo(a) à Soomei</h2>
            <button class='close' id='closeWelcome' aria-label='Fechar' title='Fechar'>&#10005;</button>
          </header>
          <div class='legal'>
            <p>Estamos muito felizes em ter você aqui! Para ativar seu cartão digital com segurança, tenha em mãos sua carta/etiqueta com o <b>PIN de ativação</b>.</p>
            <ol>
              <li>Informe seu e-mail e PIN da carta.</li>
              <li>Crie uma senha forte (mínimo 8 caracteres).</li>
              <li>(Opcional) Escolha um slug personalizado para seu cartão.</li>
              <li>Leia e concorde com os Termos e Privacidade.</li>
              <li>Clique em “Criar conta” e confirme seu e-mail.</li>
            </ol>
            <p class='muted'>Dica: mantenha seu PIN em sigilo. Se tiver dúvidas, fale com o suporte.</p>
          </div>
          <div style='text-align:right;margin-top:10px'>
            <a href='#' class='btn' id='startWelcome'>Vamos começar</a>
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
        // Validação assíncrona de e-mail e slug (disponibilidade)
        function setMsg(el, ok, text){{ if(!el)return; el.innerHTML = ok ? "<span class='ok'>"+text+"</span>" : "<span class='bad'>"+text+"</span>"; }}
        var emailEl = document.getElementById('emailInput');
        var emailMsg = document.getElementById('emailMsg');
        var slugEl = document.getElementById('vanityInput');
        var slugMsg = document.getElementById('slugMsg');
        var t1, t2;
        // Validação de e-mail somente no envio do formulário
        var form = document.getElementById('onbForm');
        if (form && emailEl && emailMsg){{
          form.addEventListener('submit', function(e){{
            var v = (emailEl.value||'').trim();
            if (!v) return; // HTML required já trata
            e.preventDefault();
            emailMsg.innerHTML = '';
            var btn = form.querySelector('button');
            if (btn) btn.disabled = true;
            fetch('/auth/check_email?value='+encodeURIComponent(v))
              .then(function(r){{ return r.json(); }})
              .then(function(j){{
                if (j && j.available){{ form.submit(); }}
                else {{ setMsg(emailMsg, false, 'E-mail ja cadastrado'); if (btn) btn.disabled = false; emailEl.focus(); }}
              }})
              .catch(function(){{ if (btn) btn.disabled = false; form.submit(); }});
          }});
        }}
        if (slugEl && slugMsg){{
          slugEl.addEventListener('input', function(){{
            clearTimeout(t2);
            var v = (slugEl.value||'').trim();
            if (!v) {{ slugMsg.innerHTML=''; return; }}
            if (!/^[a-z0-9-]{3,30}$/.test(v)) {{ setMsg(slugMsg, false, 'Use 3-30 caracteres [a-z0-9-]'); return; }}
            t2 = setTimeout(async function(){{
              try {{
                var r = await fetch('/slug/check?value='+encodeURIComponent(v));
                var j = await r.json();
                setMsg(slugMsg, j.available, j.available ? 'Disponivel' : 'Indisponivel');
              }} catch(_e) {{ slugMsg.innerHTML=''; }}
            }}, 250);
          }});
        }}
        // Preview de cor do cartão
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
    </main></body></html>
    """
    return HTMLResponse(_brand_footer_inject(html_doc))


@app.get("/login", response_class=HTMLResponse)
def login(uid: str = "", error: str = ""):
    html_doc = f"""
    <!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>
    <link rel='stylesheet' href='/static/card.css?v=20251026'><title>Entrar</title></head>
    <body><main class='wrap'>
      <h1>Entrar</h1>
      <form method='post' action='/auth/login'>
        <input type='hidden' name='uid' value='{html.escape(uid)}'>
        <label>Email</label><input name='email' type='email' required>
        <label>Senha</label><input name='password' type='password' required>
        <button class='btn'>Entrar</button>
      </form>
      <p style='color:#f88'>{html.escape(error)}</p>
    </main></body></html>
    """
    return HTMLResponse(_brand_footer_inject(html_doc))


@app.get("/invalid", response_class=HTMLResponse)
def invalid():
    html_doc = """
    <!doctype html><html lang='pt-br'><head>
    <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <link rel='stylesheet' href='/static/card.css?v=20251026'><title>Cartão não encontrado</title></head>
    <body><main class='wrap'>
      <section class='card card-public carbon card-center'>
        <h1>Ops!</h1>
        <p>Não foi possível encontrar esse cartão em nossa base.</p>
        <p class='muted'>Procure um de nossos especialistas.</p>
        <p class='muted'>Equipe Soomei</p>
      </section>
    </main></body></html>
    """
    return HTMLResponse(_brand_footer_inject(html_doc))

@app.get("/legal/terms", response_class=HTMLResponse)
def legal_terms():
    path = os.path.join(BASE, "..", "legal", "terms_v1.md")
    if not os.path.exists(path):
        return HTMLResponse("<h1>Termos indisponiveis</h1>", status_code=404)
    with open(path, "r", encoding="utf-8") as f:
        txt = f.read()
    safe = html.escape(txt).replace("\n", "<br>")
    page = f"""
    <!doctype html><html lang='pt-br'><head>
    <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <link rel='stylesheet' href='/static/card.css?v=20251026'>
    <title>Termos de Uso e Privacidade</title>
    <style>
      body{{background:#0b0b0c;color:#eaeaea}}
      .wrap{{max-width:860px;margin:16px auto;padding:12px}}
      .legal{{white-space:pre-wrap;line-height:1.5}}
      .legal h1,.legal h2,.legal h3{{margin:10px 0}}
    </style>
    </head><body>
    <main class='wrap'><div class='legal'>{safe}</div></main>
    </body></html>
    """
    return HTMLResponse(page)

@app.post("/auth/register")
def register(uid: str = Form(...), email: str = Form(...), pin: str = Form(...), password: str = Form(...), vanity: str = Form(""), lgpd: str = Form(None)):
    db = db_defaults(load())
    def back(err: str):
        dest = f"/onboard/{uid}?error={urlparse.quote_plus(err)}&email={urlparse.quote_plus(email)}&vanity={urlparse.quote_plus(vanity or '')}"
        return RedirectResponse(dest, status_code=303)
    if not lgpd:
        return back("E necessario aceitar os termos")
    vanity = (vanity or "").strip()
    if vanity:
        if not is_valid_slug(vanity):
            return back("Slug invalido. Use 3-30 caracteres [a-z0-9-]")
        if slug_in_use(db, vanity):
            return back("Slug indisponivel, tente outro")
    card = db["cards"].get(uid)
    if not card:
        return back("Cartao nao encontrado para ativacao")
    if pin != card.get("pin", "123456"):
        return back("PIN incorreto. Verifique e tente novamente")
    if len(password or "") < 8:
        return back("Senha muito curta. Use no minimo 8 caracteres")
    if email in db["users"]:
        return RedirectResponse(f"/login?uid={uid}&error=Conta%20ja%20existe", status_code=303)
    db["users"][email] = {"email": email, "pwd": h(password), "email_verified_at": None}
    card.update({"status": "active", "billing_status": "ok", "user": email})
    if vanity:
        card["vanity"] = vanity
    db["cards"][uid] = card
    db["profiles"][email] = {"full_name": "Seu Nome", "title": "Cargo | Empresa", "links": [], "whatsapp": "", "pix_key": "", "email_public": ""}
    token = secrets.token_urlsafe(24)
    db["verify_tokens"][token] = {"email": email, "created_at": int(time.time())}
    save(db)
    verify_url = f"/auth/verify?token={html.escape(token)}"
    dest = card.get("vanity", uid)
    html_doc = f"""
    <!doctype html><html lang='pt-br'><head>
      <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
      <link rel='stylesheet' href='/static/card.css?v=20251026'><title>Confirme seu email</title>
    </head><body><main class='wrap'>
      <h1>Confirme seu email</h1>
      <p>Enviamos um link de verificacao para <b>{html.escape(email)}</b>.</p>
      <p class='muted'>Ambiente de desenvolvimento: voce pode clicar aqui para confirmar agora:</p>
      <p><a class='btn' href='{verify_url}'>Confirmar email</a></p>
      <p>Depois de confirmar, voce sera direcionado ao cartao <code>/{html.escape(dest)}</code>.</p>
    </main></body></html>
    """
    return HTMLResponse(_brand_footer_inject(html_doc))


@app.post("/auth/login")
def do_login(uid: str = Form(""), email: str = Form(...), password: str = Form(...)):
    db = db_defaults(load())
    u = db["users"].get(email)
    if not u or u["pwd"] != h(password):
        return RedirectResponse(f"/login?uid={uid}&error=Credenciais%20invalidas", status_code=303)
    if not u.get("email_verified_at"):
        token = None
        for t, meta in db.get("verify_tokens", {}).items():
            if meta.get("email") == email:
                token = t; break
        link = f"/auth/verify?token={token}" if token else f"/login?uid={uid}&error=Email%20nao%20verificado"
        return RedirectResponse(link, status_code=303)
    target = None
    if uid and uid in db["cards"]:
        target = db["cards"][uid].get("vanity", uid)
    else:
        for k, v in db["cards"].items():
            if v.get("user") == email:
                target = v.get("vanity", k); break
    token = issue_session(db, email)
    resp = RedirectResponse(f"/{target}", status_code=303)
    resp.set_cookie("session", token, httponly=True, samesite="lax")
    return resp


@app.get("/auth/verify")
def verify_email(token: str):
    db = db_defaults(load())
    meta = db["verify_tokens"].pop(token, None)
    if not meta:
        return HTMLResponse("Token invalido ou expirado.", status_code=400)
    email = meta.get("email")
    if email in db["users"]:
        db["users"][email]["email_verified_at"] = int(time.time())
        save(db)
        dest = None
        for k, v in db["cards"].items():
            if v.get("user") == email:
                dest = v.get("vanity", k); break
        token_sess = issue_session(db, email)
        resp = RedirectResponse(f"/{dest}", status_code=303)
        resp.set_cookie("session", token_sess, httponly=True, samesite="lax")
        return resp
    return HTMLResponse("Usuario nao encontrado para este token.", status_code=400)


@app.api_route("/auth/logout", methods=["GET", "POST"])
def logout(request: Request, next: str = "/"):
    db = db_defaults(load())
    token = request.cookies.get("session")
    if token and token in db["sessions"]:
        db["sessions"].pop(token, None); save(db)
    dest = next or request.headers.get("referer") or "/"
    if not isinstance(dest, str):
        dest = "/"
    if not dest.startswith("/"):
        dest = "/"
    resp = RedirectResponse(dest, status_code=303)
    resp.delete_cookie("session")
    return resp


def visitor_public_card(prof: dict, slug: str, is_owner: bool = False):
    photo = html.escape(prof.get("photo_url", "")) if prof else ""
    wa_raw = (prof.get("whatsapp", "") or "").strip()
    wa_digits = "".join([c for c in wa_raw if c.isdigit()])
    email_pub = (prof.get("email_public", "") or "").strip()
    pix_key = (prof.get("pix_key", "") or "").strip()
    links_list = prof.get("links", []) or []

    def platform(label: str, href: str) -> str:
        s = f"{(label or '').lower()} {(href or '').lower()}"
        if "instagram" in s: return "instagram"
        if "linkedin" in s: return "linkedin"
        if "facebook" in s or "fb.com" in s: return "facebook"
        if "youtube" in s or "youtu.be" in s: return "youtube"
        if "tiktok" in s: return "tiktok"
        if "twitter" in s or "x.com" in s: return "twitter"
        if "github" in s: return "github"
        if "behance" in s: return "behance"
        if "dribbble" in s: return "dribbble"
        if (href or "").startswith("tel:"): return "phone"
        if (href or "").startswith("mailto:"): return "email"
        if ("site" in s or "website" in s or "pagina" in s): return "site"
        return "site" if (href or "").startswith("http") else "link"

    site_link = None
    insta_link = None
    other_links = []
    for item in links_list:
        label = item.get("label", "")
        href = item.get("href", "")
        plat = platform(label, href)
        if plat == "instagram" and insta_link is None:
            insta_link = (label, href)
        elif plat == "site" and site_link is None:
            site_link = (label, href)
        else:
            other_links.append((label, href, plat))

    share_url = f"{PUBLIC_BASE}/{slug}"
    share_text = urlparse.quote_plus(f"Ola! Vim pelo seu cartao: {share_url}")

    actions = []
    if wa_digits:
        actions.append(f"<a class='btn action whatsapp' target='_blank' rel='noopener' href='https://wa.me/{wa_digits}?text={share_text}'>WhatsApp</a>")
    if site_link:
        _, href = site_link
        actions.append(f"<a class='btn action website' target='_blank' rel='noopener' href='{html.escape(href)}'>Site</a>")
    if insta_link:
        _, href = insta_link
        actions.append(f"<a class='btn action instagram' target='_blank' rel='noopener' href='{html.escape(href)}'>Instagram</a>")
    if email_pub:
        actions.append(f"<a class='btn action email' href='mailto:{html.escape(email_pub)}'>E-mail</a>")
    actions.append(f"<a class='btn action vcard' href='/v/{html.escape(slug)}.vcf'>Salvar contato</a>")
    actions.append("<a class='btn action share' id='shareBtn' href='#'>Compartilhar</a>")
    if pix_key:
        actions.append(f"<a class='btn action pix' id='pixBtn' data-key='{html.escape(pix_key)}' href='#'>Copiar PIX</a>")
    # Engrenagem de edição discreta no canto superior direito (somente dono)
    owner_gear = (
        f"<a class='edit-gear' href='/edit/{html.escape(slug)}' title='Editar' aria-label='Editar'>⚙</a>"
        if is_owner else ""
    )
    actions_html = "".join(actions)

    link_items = []
    for label, href, plat in other_links:
        cls = f"brand-{plat}"
        link_items.append(f"<li><a class='link {cls}' href='{html.escape(href)}' target='_blank' rel='noopener'>{html.escape(label or plat.title())}</a></li>")
    links_grid_html = "".join(link_items)

    scripts = """
    <script>
    (function(){
      var s = document.getElementById('shareBtn');
      if (s) {
        s.addEventListener('click', function(e){
          e.preventDefault();
          var url = window.location.href;
          if (navigator.share) {
            navigator.share({title: document.title, url: url}).catch(function(){});
          } else if (navigator.clipboard) {
            navigator.clipboard.writeText(url);
            s.textContent = 'Link copiado'; setTimeout(function(){ s.textContent = 'Compartilhar'; }, 1500);
          }
        });
      }
      var p = document.getElementById('pixBtn');
      if (p) {
        p.addEventListener('click', function(e){
          e.preventDefault();
          var k = p.getAttribute('data-key') || '';
          if (navigator.clipboard) {
            navigator.clipboard.writeText(k);
            p.textContent = 'PIX copiado'; setTimeout(function(){ p.textContent = 'Copiar PIX'; }, 1500);
          } else { alert('Chave PIX: '+k); }
        });
      }
    })();
    </script>
    """

    # Cor de fundo suavizada para o card público
    theme_base = (prof.get("theme_color", "#000000") or "#000000") if prof else "#000000"
    if not re.fullmatch(r"#([0-9a-fA-F]{6})", theme_base or ""):
        theme_base = "#000000"
    bg_hex = theme_base + "30"

    html_doc = f"""<!doctype html><html lang='pt-br'><head>
    <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <link rel='stylesheet' href='/static/card.css?v=20251026'><title>Soomei | {html.escape(prof.get('full_name',''))}</title></head><body>
    <main class='wrap'>
      <section class='card card-public carbon card-center' style='background-color: {html.escape(bg_hex)}'>
        {owner_gear}
        <header class='card-header'>
          {f"<img class='avatar avatar-small' src='{photo}' alt='foto'>" if photo else ""}
          <h1 class='name'>{html.escape(prof.get('full_name',''))}</h1>
          <p class='title'>{html.escape(prof.get('title',''))}</p>
        </header>
        <div class='actions-row'>{actions_html}</div>
        <ul class='contact'>
          {f"<li><a class='contact-link' href='https://wa.me/{wa_digits}' target='_blank' rel='noopener'>{wa_raw}</a></li>" if wa_digits else ""}
          {f"<li><a class='contact-link' href='mailto:{html.escape(email_pub)}'>{html.escape(email_pub)}</a></li>" if email_pub else ""}
        </ul>
        {f"<h3 class='section'>Links</h3>" if links_grid_html else ""}
        <ul class='links links-grid'>{links_grid_html}
        </ul>
      </section>
      {scripts}
      <script>
      (function(){{
        var isOwner = { 'true' if is_owner else 'false' };
        var slugId = "{html.escape(slug)}";
        var ft = document.querySelector('footer');
        if (ft) {{
          ft.innerHTML = isOwner ? ("<a href='/auth/logout?next=/" + slugId + "' class='muted'>Sair</a>") : "<a href='/login' class='muted'>Entrar</a>";
        }}
      }})();
      </script>
      <footer>{("<a href='/auth/logout?next=/" + html.escape(slug) + "' class='muted'>Sair</a>") if is_owner else ("<a href='/login' class='muted'>Entrar</a>")}</footer>
    </main></body></html>"""
    return HTMLResponse(_brand_footer_inject(html_doc))


@app.get("/u/{slug}", response_class=HTMLResponse)
def public_card(slug: str, request: Request):
    db, uid, card = find_card_by_slug(slug)
    if not card:
        raise HTTPException(404, "Cartao nao encontrado")
    prof = db["profiles"].get(card.get("user", ""), {})
    return visitor_public_card(prof, slug, False)


@app.get("/q/{slug}.png")
def qr(slug: str):
    img = qrcode.make(f"{PUBLIC_BASE}/{slug}")
    buf = io.BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@app.get("/v/{slug}.vcf")
def vcard(slug: str):
    db, uid, card = find_card_by_slug(slug)
    if not card:
        raise HTTPException(404, "Cartao nao encontrado")
    prof = db["profiles"].get(card.get("user", ""), {})
    name = prof.get("full_name", ""); tel = prof.get("whatsapp", ""); email = prof.get("email_public", "")
    url = f"{PUBLIC_BASE}/{slug}"
    vcf = (
        "BEGIN:VCARD\n" +
        "VERSION:3.0\n" +
        f"N:{name};;;;\n" +
        f"FN:{name}\n" +
        "ORG:Soomei\n" +
        f"TITLE:{prof.get('title','')}\n" +
        f"TEL;TYPE=CELL:{tel}\n" +
        f"EMAIL;TYPE=INTERNET:{email}\n" +
        f"URL:{url}\n" +
        "END:VCARD\n"
    )
    return PlainTextResponse(vcf, media_type="text/vcard")


@app.get("/slug/check")
def slug_check(value: str = ""):
    db = db_defaults(load())
    value = (value or "").strip()
    available = is_valid_slug(value) and (not slug_in_use(db, value))
    return {"available": available}


@app.get("/auth/check_email")
def check_email(value: str = ""):
    db = db_defaults(load())
    v = (value or "").strip().lower()
    # e-mails são únicos; consideramos indisponível se já existir
    # Não validamos formato avançado aqui; o front usa input type=email
    available = bool(v) and (v not in db.get("users", {}))
    return {"available": available}


@app.get("/slug/select/{id}", response_class=HTMLResponse)
def slug_select(id: str, request: Request):
    db, uid, card = find_card_by_slug(id)
    if not card:
        raise HTTPException(404, "Cartao nao encontrado")
    owner = card.get("user", "")
    who = current_user_email(request)
    if who != owner:
        return RedirectResponse(f"/{html.escape(card.get('vanity', uid))}", status_code=303)
    current = card.get("vanity", "") or ""
    back_slug = card.get('vanity', uid)
    return templates.TemplateResponse(
        "slug_select.html",
        {"request": request, "uid": uid, "current": current, "back_slug": back_slug}
    )


@app.post("/slug/select/{id}")
def slug_select_post(id: str, request: Request, value: str = Form("")):
    db, uid, card = find_card_by_slug(id)
    if not card:
        raise HTTPException(404, "Cartao nao encontrado")
    owner = card.get("user", "")
    who = current_user_email(request)
    if who != owner:
        return RedirectResponse(f"/{html.escape(card.get('vanity', uid))}", status_code=303)
    value = (value or "").strip()
    if not is_valid_slug(value):
        return HTMLResponse("Slug invalido. Use 3-30 caracteres [a-z0-9-]", status_code=400)
    if slug_in_use(db_defaults(load()), value):
        return HTMLResponse("Slug indisponivel, tente outro.", status_code=409)
    db2 = db_defaults(load())
    c = db2["cards"].get(uid, card)
    c["vanity"] = value
    db2["cards"][uid] = c
    save(db2)
    return RedirectResponse(f"/{html.escape(value)}", status_code=303)


@app.post("/hooks/themembers")
def hook(payload: dict):
    db = load()
    uid = payload.get("uid")
    if not uid or uid not in db["cards"]:
        return {"ok": True}
    st = payload.get("status", "ok")
    c = db["cards"][uid]
    c["billing_status"] = st
    if st in ("blocked", "delinquent"):
        c["status"] = "blocked"
    elif st == "ok":
        c["status"] = "active"
    db["cards"][uid] = c; save(db)
    return {"ok": True}


@app.get("/edit/{slug}", response_class=HTMLResponse)
def edit_card(slug: str, request: Request):
    db, uid, card = find_card_by_slug(slug)
    if not card:
        raise HTTPException(404, "Cartao nao encontrado")
    owner = card.get("user", "")
    who = current_user_email(request)
    if who != owner:
        return RedirectResponse(f"/{slug}", status_code=303)
    prof = load()["profiles"].get(owner, {})
    # Cor do tema do cartão (hex #RRGGBB)
    theme_base = prof.get("theme_color", "#000000") or "#000000"
    if not re.fullmatch(r"#([0-9a-fA-F]{6})", theme_base or ""):
        theme_base = "#000000"
    bg_hex = theme_base + "30"
    links = prof.get("links", [])
    while len(links) < 3:
        links.append({"label": "", "href": ""})
    html_form = f"""
    <!doctype html><html lang='pt-br'><head>
    <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <link rel='stylesheet' href='/static/card.css?v=20251026'><title>Soomei - Editar</title></head>
    <body><main class='wrap'>
      <form id='editForm' method='post' action='/edit/{html.escape(slug)}' enctype='multipart/form-data'>
        <div class='topbar'>
          <button class='icon-btn top-left' type='button' aria-label='Voltar' title='Voltar' onclick="location.href='/{html.escape(slug)}'">
            <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true'>
              <path d='M15 18l-6-6 6-6' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/>
            </svg>
          </button>
          <h1 class='page-title'>Editar Perfil</h1>
          <button class='icon-btn top-right' type='submit' aria-label='Salvar' title='Salvar'>
            <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true'>
              <path d='M5 13l4 4L19 7' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/>
            </svg>
          </button>
        </div>
        <div class='avatar-preview-wrap'>
          <div id='colorPreview' class='preview-carbon carbon' style='background-color: {html.escape(bg_hex)}'></div>
        <div style='text-align:center;margin:0'>
          {f"<img id='avatarImg' class='avatar' src='{html.escape(prof.get('photo_url',''))}' alt='foto'>" if prof.get('photo_url') else "<div id='avatarHolder' class='avatar' style='background:#0b0b0c;border:2px dashed #2a2a2a'></div>"}
          <div><a href='#' id='photoTrigger' class='photo-change' onclick=\"document.getElementById('photoInput').click(); return false;\">Alterar foto do perfil</a></div>
          <div class='muted' style='font-size:12px;margin-top:4px'>Tamanho máximo: 2 MB (JPEG/PNG)</div>
        </div>
        </div>
        <input type='file' id='photoInput' name='photo' accept='image/jpeg,image/png' style='display:none'>
        <label>Cor do cartão</label><input type='color' id='themeColor' name='theme_color' value='{html.escape(theme_base)}' style='height: 35px;'>
        <label>Nome completo</label><input name='full_name' value='{html.escape(prof.get('full_name',''))}' required>
        <label>Cargo | Empresa</label><input name='title' value='{html.escape(prof.get('title',''))}'>
        <label>WhatsApp</label><input name='whatsapp' id='whatsapp' inputmode='tel' placeholder='(00) 00000-0000' value='{html.escape(prof.get('whatsapp',''))}'>
        <label>Email publico</label><input name='email_public' type='email' value='{html.escape(prof.get('email_public',''))}'>
        <h3>Links</h3>
        <label>Rotulo 1</label><input name='label1' value='{html.escape(links[0].get('label',''))}'>
        <label>URL 1</label><input name='href1' value='{html.escape(links[0].get('href',''))}'>
        <label>Rotulo 2</label><input name='label2' value='{html.escape(links[1].get('label',''))}'>
        <label>URL 2</label><input name='href2' value='{html.escape(links[1].get('href',''))}'>
        <label>Rotulo 3</label><input name='label3' value='{html.escape(links[2].get('label',''))}'>
        <label>URL 3</label><input name='href3' value='{html.escape(links[2].get('href',''))}'>
        
      </form>
      <script>
      (function(){{
        var input = document.getElementById('photoInput');
        if (input) {{
          var img = document.getElementById('avatarImg');
          var holder = document.getElementById('avatarHolder');
          input.addEventListener('change', function(){{
            var f = input.files && input.files[0];
            if (!f) return;
            var MAX = 2 * 1024 * 1024;
            if (f.size > MAX) {{ alert('Arquivo muito grande (maximo 2 MB).'); input.value=''; return; }}
            var ok = /^(image\/jpeg|image\/png)$/.test(f.type);
            if (!ok) {{ alert('Formato de imagem nao suportado (use JPEG ou PNG)'); input.value=''; return; }}
            var url = URL.createObjectURL(f);
            if (img) {{
              img.src = url;
            }} else {{
              img = document.createElement('img');
              img.id = 'avatarImg';
              img.className = 'avatar';
              img.alt = 'foto';
              img.src = url;
              if (holder) holder.replaceWith(img);
            }}
          }});
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
      </script>
      <p><a class='muted' href='/auth/logout?next=/{html.escape(slug)}'>Sair</a></p>
    </main></body></html>
    """
    return HTMLResponse(_brand_footer_inject(html_form))


@app.post("/edit/{slug}")
async def save_edit(slug: str, request: Request, full_name: str = Form(""), title: str = Form(""),
               whatsapp: str = Form(""), email_public: str = Form(""),
               label1: str = Form(""), href1: str = Form(""),
               label2: str = Form(""), href2: str = Form(""),
               label3: str = Form(""), href3: str = Form(""),
               theme_color: str = Form(""),
               photo: UploadFile | None = File(None)):
    db, uid, card = find_card_by_slug(slug)
    if not card:
        raise HTTPException(404, "Cartao nao encontrado")
    owner = card.get("user", "")
    who = current_user_email(request)
    if who != owner:
        return RedirectResponse(f"/{slug}", status_code=303)
    db2 = db_defaults(load())
    prof = db2["profiles"].get(owner, {})
    prof.update({
        "full_name": full_name.strip(),
        "title": title.strip(),
        "whatsapp": sanitize_phone(whatsapp),
        "email_public": email_public.strip(),
    })
    # Salva cor do tema (hex #RRGGBB)
    tc = (theme_color or "").strip()
    if not re.fullmatch(r"#([0-9a-fA-F]{6})", tc or ""):
        tc = "#000000"
    prof["theme_color"] = tc
    links = []
    for (lbl, href) in [(label1, href1), (label2, href2), (label3, href3)]:
        if lbl.strip() and href.strip():
            links.append({"label": lbl.strip(), "href": href.strip()})
    prof["links"] = links
    if photo and photo.filename:
        ct = (photo.content_type or "").lower()
        if ct not in ("image/jpeg", "image/png"):
            return HTMLResponse("Formato de imagem nao suportado (use JPEG ou PNG)", status_code=400)
        ext = ".jpg" if ct == "image/jpeg" else ".png"
        filename = f"{uid}{ext}"
        dest_path = os.path.join(UPLOADS, filename)
        data = await photo.read()
        if len(data) > 2 * 1024 * 1024:
            return HTMLResponse("Imagem muito grande (máximo 2 MB).", status_code=400)
        with open(dest_path, "wb") as f:
            f.write(data)
        prof["photo_url"] = f"/static/uploads/{filename}"
    db2["profiles"][owner] = prof
    save(db2)
    return RedirectResponse(f"/{slug}", status_code=303)


@app.get("/blocked", response_class=HTMLResponse)
def blocked():
    html_doc = """
    <!doctype html><html lang='pt-br'><head>
    <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <link rel='stylesheet' href='/static/card.css?v=20251026'><title>Cartao bloqueado</title></head>
    <body><main class='wrap'>
      <h1>Cartao bloqueado</h1>
      <p>Entre em contato com o suporte para mais informacoes.</p>
    </main></body></html>
    """
    return HTMLResponse(_brand_footer_inject(html_doc))


@app.get("/{slug}", response_class=HTMLResponse)
def root_slug(slug: str, request: Request):
    db, uid, card = find_card_by_slug(slug)
    if card and card.get("vanity") and slug != card.get("vanity"):
        return RedirectResponse(f"/{html.escape(card.get('vanity'))}", status_code=302)
    if not card:
        return HTMLResponse("<h1>Cartao nao encontrado</h1>", status_code=404)
    if not card.get("status") or card.get("status") == "pending":
        return RedirectResponse(f"/onboard/{html.escape(uid)}", status_code=302)
    if card.get("status") == "blocked":
        return RedirectResponse("/blocked", status_code=302)
    owner = card.get("user", "")
    prof = load()["profiles"].get(owner, {})
    who = current_user_email(request)
    if who == owner and not card.get("vanity"):
        return RedirectResponse(f"/slug/select/{html.escape(uid)}", status_code=302)
    if who == owner and not profile_complete(prof):
        return RedirectResponse(f"/edit/{html.escape(slug)}", status_code=302)
    if who != owner:
        if not profile_complete(prof):
            return HTMLResponse(_brand_footer_inject("""
            <!doctype html><html lang='pt-br'><head>
            <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
            <link rel='stylesheet' href='/static/card.css?v=20251026'><title>Em construcao</title></head>
            <body><main class='wrap'>
              <h1>Cartao digital em construcao</h1>
              <p>O proprietario ainda nao finalizou o preenchimento deste cartao.</p>
              <p class='muted'><a href='/login'>Sou o dono? Entrar</a></p>
            </main></body></html>
            """))
        return visitor_public_card(prof, slug, False)
    return visitor_public_card(prof, slug, True)


