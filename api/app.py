from fastapi import FastAPI, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os, json, html, qrcode, io, hashlib, time, secrets, re, base64, unicodedata
import urllib.parse as urlparse
import smtplib, ssl
import pathlib, shutil
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

app = FastAPI(title="Soomei Card API v2")

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "data.json")
WEB = os.path.join(BASE, "..", "web")
PHONE_RE = re.compile(r"^\+?\d{10,15}$")
CPF_RE   = re.compile(r"^\d{11}$")
CNPJ_RE  = re.compile(r"^\d{14}$")
UUID_RE  = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
DEFAULT_AVATAR = "/static/img/user01.png"

class CachedStaticFiles(StaticFiles):
    def set_headers(self, scope, resp, path, stat_result):
        # Cache forte para assets versionados por fingerprint
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"


app.mount("/static", CachedStaticFiles(directory=WEB), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE, "..", "templates"))

PUBLIC_BASE = os.getenv("PUBLIC_BASE_URL", "https://soomei.cc").rstrip("/")
PUBLIC_VERSION = os.getenv("PUBLIC_VERSION")
UPLOADS = os.path.join(WEB, "uploads")
os.makedirs(UPLOADS, exist_ok=True)

ASSETS = {}

def _fingerprint_asset(rel_path: str) -> str:
    """
    Gera cópia com hash curto no nome: "card.css" -> "card.<hash8>.css".
    Retorna o nome do arquivo versionado (sem /static).
    """
    src = pathlib.Path(WEB) / rel_path
    if not src.exists():
        # Fallback: retorna o próprio nome sem hash
        return rel_path.replace("\\", "/")
    data = src.read_bytes()
    h = hashlib.sha1(data).hexdigest()[:8]
    stem = src.stem
    suffix = src.suffix  # ex: ".css"
    dst_name = f"{stem}.{h}{suffix}"
    dst = src.with_name(dst_name)
    if not dst.exists():
        shutil.copy2(src, dst)
    return dst_name

# Prepara href do CSS principal
try:
    _css_fp = _fingerprint_asset("card.css")
except Exception:
    _css_fp = "card.css"
CSS_HREF = f"/static/{_css_fp}"
ASSETS["card.css"] = _css_fp

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

# --- PIX (BR Code) helpers ---
def _tlv(_id: str, value: str) -> str:
    l = f"{len(value):02d}"
    return f"{_id}{l}{value}"


def _crc16_ccitt(data: str) -> str:
    # CRC16/CCITT-FALSE (poly 0x1021, init 0xFFFF)
    crc = 0xFFFF
    for ch in data:
        crc ^= (ord(ch) << 8) & 0xFFFF
        for _ in range(8):
            if (crc & 0x8000):
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def _norm_text(s: str, maxlen: int) -> str:
    t = unicodedata.normalize("NFKD", (s or "")).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^A-Za-z0-9 \-\.]+", "", t).strip() or "NA"
    return t[:maxlen].upper()

# --- Validadores simples de CPF/CNPJ (checagem de dígitos verificadores) ---
def _is_valid_cpf(cpf: str) -> bool:
    cpf = re.sub(r"\D", "", cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    def dv(nums, mult):
        s = sum(int(d) * m for d, m in zip(nums, mult))
        r = (s * 10) % 11
        return 0 if r == 10 else r
    d1 = dv(cpf[:9], range(10, 1, -1))
    d2 = dv(cpf[:9] + str(d1), range(11, 1, -1))
    return cpf[-2:] == f"{d1}{d2}"

def _is_valid_cnpj(cnpj: str) -> bool:
    cnpj = re.sub(r"\D", "", cnpj)
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    def calc_dv(nums, pesos):
        s = sum(int(n) * p for n, p in zip(nums, pesos))
        r = s % 11
        return 0 if r < 2 else 11 - r
    p1 = [5,4,3,2,9,8,7,6,5,4,3,2]
    p2 = [6] + p1
    d1 = calc_dv(cnpj[:12], p1)
    d2 = calc_dv(cnpj[:12] + str(d1), p2)
    return cnpj[-2:] == f"{d1}{d2}"

def _normalize_pix_key(pix_key: str) -> str:
    key = (pix_key or "").strip()

    # E-mail
    if "@" in key:
        k = key.lower()
        if len(k) > 77:
            raise ValueError("E-mail da chave Pix excede 77 caracteres.")
        return k

    # EVP (aleatória) comum como UUID
    if UUID_RE.match(key):
        return key

    # Já está em E.164?
    if key.startswith("+") and re.fullmatch(r"\+\d{11,15}", key):
        return key

    digits = re.sub(r"\D", "", key)

    # CPF/CNPJ válidos: retornar exatamente os dígitos
    if _is_valid_cpf(digits):
        return digits
    if _is_valid_cnpj(digits):
        return digits

    # Telefone (heurística): 10–13 dígitos → normalizar para E.164 assumindo Brasil (+55) quando faltar DDI
    if 10 <= len(digits) <= 13:
        if digits.startswith("55"):
            phone = "+" + digits
        else:
            phone = "+55" + digits
        if not re.fullmatch(r"\+\d{11,15}", phone):
            raise ValueError("Telefone fora do padrão E.164 após normalização.")
        return phone

    # Caso restante: pode ser outra EVP não-UUID; devolver como veio (máx. 77 chars)
    if len(key) <= 77:
        return key
    raise ValueError("Chave Pix inválida ou muito longa (máx. 77 caracteres).")

def _sanitize_txid(txid: str) -> str:
    return (txid or "***").strip()[:25]

# --- SUA FUNÇÃO (atualizada) ---
def build_pix_emv(pix_key: str, amount: Optional[float], merchant_name: str, merchant_city: str, txid: str = "***") -> str:
    # 00: Payload Format Indicator
    payload = _tlv("00", "01")

    # 01: Point of Initiation Method — 12 (dinâmico) quando tem valor; 11 (estático) sem valor
    poi = "12" if (amount or 0) > 0 else "11"
    payload += _tlv("01", poi)

    # 26: Merchant Account Information (GUI + chave normalizada)
    normalized_key = _normalize_pix_key(pix_key)
    mai = _tlv("00", "br.gov.bcb.pix") + _tlv("01", normalized_key)
    payload += _tlv("26", mai)

    # 52: MCC (0000), 53: Moeda (986)
    payload += _tlv("52", "0000")
    payload += _tlv("53", "986")

    # 54: Valor (opcional)
    if (amount or 0) > 0:
        payload += _tlv("54", f"{amount:.2f}")

    # 58: País, 59: Nome, 60: Cidade
    payload += _tlv("58", "BR")
    payload += _tlv("59", _norm_text(merchant_name, 25))
    payload += _tlv("60", _norm_text(merchant_city, 15))

    # 62: Dados Adicionais (05: txid)
    add = _tlv("05", _sanitize_txid(txid))
    payload += _tlv("62", add)

    # 63: CRC16
    to_crc = payload + "6304"
    crc = _crc16_ccitt(to_crc)
    return to_crc + crc

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


def _int_or_zero(value, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def get_card_view_count(uid: str) -> int:
    db = db_defaults(load())
    card = db.get("cards", {}).get(uid) or {}
    metrics = card.get("metrics") or {}
    return _int_or_zero(metrics.get("views"), 0)


def increment_card_view(uid: str) -> int:
    db = db_defaults(load())
    card = db.get("cards", {}).get(uid)
    if not card:
        return 0
    metrics = card.setdefault("metrics", {})
    current = _int_or_zero(metrics.get("views"), 0) + 1
    metrics["views"] = current
    try:
        save(db)
    except Exception:
        pass
    return current


def should_track_view(request: Request, slug: str) -> bool:
    if request.method.upper() != "GET":
        return False
    referer = request.headers.get("referer", "")
    if not referer:
        return True
    try:
        parsed = urlparse.urlparse(referer)
    except ValueError:
        return True
    ref_host = (parsed.hostname or "").lower()
    current_host = (request.headers.get("host") or "").split(":", 1)[0].lower()
    if ref_host and current_host and ref_host != current_host:
        return True
    path = parsed.path or ""
    if path.startswith("/auth/logout"):
        return False
    normalized = {f"/{slug}", f"/u/{slug}"}
    if path in normalized:
        q_keys = {k.lower() for k in urlparse.parse_qs(parsed.query or "").keys()}
        if q_keys & {"pix", "offline"}:
            return False
    return True


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
    snippet = "\n    <footer class='muted' style='text-align:center'>&copy; 2025 Soomei"+ ((" - " + PUBLIC_VERSION) if PUBLIC_VERSION else "") + "</footer>\n  "
    return html_doc.replace("</body>", snippet + "</body>", 1) if "</body>" in html_doc else (html_doc + snippet)

def resolve_photo(photo: str | None) -> str:
    if photo and str(photo).strip():
        return photo
    return DEFAULT_AVATAR

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
        <label>PIN da carta</label><input name='pin' type='password' required inputmode='numeric' pattern='[0-9]*' autocomplete='one-time-code' placeholder='Somente números'>\n        <div class='hint'>Dica: PIN possui apenas números.</div>
        <label>Nova senha</label><input name='password' type='password' required minlength='8' autocomplete='new-password' placeholder='Mínimo de 8 caracteres'>
        <label>Slug (opcional)</label><input id='vanityInput' name='vanity' placeholder='seu-nome' pattern='[a-z0-9-]{3,30}' value='{html.escape(vanity)}' autocapitalize='none' autocorrect='off' inputmode='url' style='text-transform:lowercase'>
        <div id='slugMsg' class='hint'>Use 3-30 caracteres, todos minusculos, sem caracteres especiais</div>
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
        // Submissão com validações amigáveis (fase de captura) para evitar página em branco
        (function(){{
          var form = document.getElementById('onbForm');
          if (!(form && emailEl && emailMsg)) return;
          form.addEventListener('submit', function(e){{
            var alertBox = document.getElementById('formAlert');
            function fail(msg, focusEl){{ try{{ e.preventDefault(); e.stopImmediatePropagation(); }}catch(_e){{}} if(alertBox){{ alertBox.textContent=msg; alertBox.style.display='block'; }} if(focusEl){{ try{{ focusEl.focus(); }}catch(_e){{}} }} }}
            var vEmail = (emailEl.value||'').trim();
            if (!vEmail) return; // HTML required trata
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
            // força minúsculas
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
          // listener simples para forçar minúsculas
          slugEl.addEventListener('input', function(){{ var vv = slugEl.value||''; var ll = vv.toLowerCase(); if (vv!==ll) slugEl.value=ll; }});
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
def login(request: Request, uid: str = "", error: str = ""):
    return templates.TemplateResponse(
        "login.html", {"request": request, "uid": uid, "error": error}
    )


@app.get("/invalid", response_class=HTMLResponse)
def invalid(request: Request):
    return templates.TemplateResponse("invalid.html", {"request": request})

@app.get("/legal/terms", response_class=HTMLResponse)
def legal_terms(request: Request):
    path = os.path.join(BASE, "..", "legal", "terms_v1.md")
    if not os.path.exists(path):
        return HTMLResponse("<h1>Termos indisponiveis</h1>", status_code=404)
    with open(path, "r", encoding="utf-8") as f:
        txt = f.read()
    safe = html.escape(txt).replace("\n", "<br>")
    return templates.TemplateResponse(
        "legal_terms.html", {"request": request, "safe": safe}
    )

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
    db["profiles"][email] = {"full_name": "", "title": "", "links": [], "whatsapp": "", "pix_key": "", "email_public": "", "site_url": ""}
    token = secrets.token_urlsafe(24)
    db["verify_tokens"][token] = {"email": email, "created_at": int(time.time())}
    save(db)
    verify_url = f"/auth/verify?token={html.escape(token)}"
    dest = card.get("vanity", uid)
    html_doc = f"""
    <!doctype html><html lang='pt-br'><head>
      <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
      <link rel='stylesheet' href='{CSS_HREF}'><title>Confirme seu email</title>
    </head><body><main class='wrap'>
      <h1>Confirme seu email</h1>
      <p>Enviamos um link de verificacao para <b>{html.escape(email)}</b>.</p>
      {("<p class='muted'>Ambiente de desenvolvimento: voce pode clicar aqui para confirmar agora:</p><p><a class='btn' href='" + verify_url + "'>Confirmar email</a></p>") if os.getenv('APP_ENV','dev').lower().strip() != 'prod' else ("<p class='muted'>Verifique sua caixa de entrada para concluir a confirmacao.</p>")}
      <p>Depois de confirmar, voce sera direcionado ao cartao <code>/{html.escape(dest)}</code>.</p>
    </main></body></html>
    """
    return HTMLResponse(_brand_footer_inject(html_doc))


# Silencia requisições de debug do Chrome (evita 404 ruidoso em logs)
@app.get("/.well-known/appspecific/com.chrome.devtools.json")
def chrome_devtools_wellknown():
    return PlainTextResponse("", status_code=204)


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


def visitor_public_card(prof: dict, slug: str, is_owner: bool = False, view_count: int = 0):
    raw_photo = (prof.get("photo_url", "") or "") if prof else ""
    photo = html.escape(raw_photo) if raw_photo else ""
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

    share_url = f"{PUBLIC_BASE}/{slug}"
    share_text = urlparse.quote_plus(f"Ola! Vim pelo seu cartao da Soomei.")
    share_base_message = "Este é o meu Cartão de Visita Digital"
    share_base_message_js = json.dumps(share_base_message)

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
            navigator.share({title: document.title, url: data.url}).catch(function(){});
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
            navigator.share({title: document.title, text: shareData.text, url: shareData.url}).catch(function(){
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
        off_share_url = f"{PUBLIC_BASE}/{slug}"
        # PHOTO inline (base64, downscaled for QR). Em offline, omite em caso de falha.
        photo_line_off = ""
        off_photo_url = (prof.get("photo_url", "") or "").strip() if prof else ""
        if off_photo_url:
            try:
                fname = os.path.basename(off_photo_url.split("?", 1)[0])
                local_path = os.path.join(UPLOADS, fname)
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
    site_href = (prof.get("site_url", "") or "").strip()
    if site_href and not (site_href.startswith("http://") or site_href.startswith("https://") or site_href.startswith("mailto:") or site_href.startswith("tel:")):
        site_href = "https://" + site_href.lstrip("/")
    # Endereço (opcional) para link do Maps
    address_text = (prof.get("address", "") or "").strip() if prof else ""
    if address_text:
        maps_q = urlparse.quote(address_text, safe="")
        maps_href = f"https://www.google.com/maps/search/?api=1&query={maps_q}"
    else:
        maps_href = ""

    og_title = f"{prof.get('full_name','')} — Soomei Card".strip(" —") if prof else "Soomei Card"
    og_desc = prof.get("title") if prof and prof.get("title") else "Clique para me chamar no WhatsApp e salvar meu contato."
    og_image_url = raw_photo or DEFAULT_AVATAR
    if og_image_url.startswith("/"):
        og_image_url = f"{PUBLIC_BASE}{og_image_url}"
    elif not og_image_url.startswith("http"):
        og_image_url = f"{PUBLIC_BASE}/{og_image_url.lstrip('/')}"
    og_image_url = html.escape(og_image_url)

    html_doc = f"""<!doctype html><html lang='pt-br'><head>
    <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <link rel='stylesheet' href='{CSS_HREF}'><title>Soomei | {html.escape(prof.get('full_name',''))}</title>
    <meta property='og:type' content='website'>
    <meta property='og:url' content='{html.escape(share_url)}'>
    <meta property='og:title' content='{html.escape(og_title)}'>
    <meta property='og:description' content='{html.escape(og_desc)}'>
    <meta property='og:image' content='{og_image_url}'>
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
              <div class='icon-btn disabled' title='WhatsApp indisponvel' aria-disabled='true'>
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
            <button type='button' class='icon-btn brand-share' id='shareCardBtn' title='Enviar cartão' aria-label='Enviar cartão'>
              <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='18' height='18' focusable='false'>
                <path d='M5 12h12' stroke='currentColor' stroke-width='2' stroke-linecap='round' fill='none'/>
                <path d='M13 6l6 6-6 6' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round' fill='none'/>
              </svg>
            </button>
            <div class='qa-label'>Enviar cartão</div>
          </div>
        </div>
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
        <div class='fixed-actions'>
          {(
            f"<a class='btn fixed website' target='_blank' rel='noopener' href='{html.escape(site_href)}'>"
            f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='16' height='16'>"
            f"<circle cx='12' cy='12' r='10' stroke='currentColor' stroke-width='2' fill='none'/><path d='M2 12h20M12 2c3 3 3 19 0 20M12 2c-3 3-3 19 0 20' stroke='currentColor' stroke-width='2' fill='none'/></svg> "
            f"Site</a>"
          ) if site_href else (
            "<a class='btn fixed website disabled' href='#' aria-disabled='true'>"
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='16' height='16'><circle cx='12' cy='12' r='10' stroke='currentColor' stroke-width='2' fill='none'/><path d='M2 12h20M12 2c3 3 3 19 0 20M12 2c-3 3-3 19 0 20' stroke='currentColor' stroke-width='2' fill='none'/></svg> Site</a>"
          )}
          {(
            f"<a class='btn fixed email' href='mailto:{html.escape(email_pub)}'>"
            f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='16' height='16'><path fill='currentColor' d='M4 6h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1zm8 6 9-6H3l9 6zm0 2L3 8v9h18V8l-9 6z'/></svg> "
            f"E-mail</a>"
          ) if email_pub else (
            "<a class='btn fixed email disabled' href='#' aria-disabled='true'>"
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='16' height='16'><path fill='currentColor' d='M4 6h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1zm8 6 9-6H3l9 6zm0 2L3 8v9h18V8l-9 6z'/></svg> E-mail</a>"
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
            "<a class='btn fixed pix disabled' href='#' aria-disabled='true'>"
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' aria-hidden='true' width='16' height='16'><path fill='currentColor' d='M3 3h6v6H3V3zm2 2v2h2V5H5zm10-2h6v6h-6V3zm2 2v2h2V5h-2zM3 15h6v6H3v-6zm2 2v2h2v-2H5zm10 0h2v2h2v2h-4v-4zm0-4h2v2h-2v-2zm4 0h2v2h-2v-2z'/></svg> Pagamento Pix</a>"
          )}
        </div>
        
      </section>
      {scripts}
      <script>
      (function(){{
        var isOwner = { 'true' if is_owner else 'false' };
        var slugId = "{html.escape(slug)}";
        var ft = document.querySelector('footer');
        if (ft) {{
          ft.innerHTML = isOwner ? ("<a href='/auth/logout?next=/" + slugId + "' class='muted'>Sair</a>") : "<a href='/login' class='muted'>Entrar</a>";        // Ajusta link do Maps conforme dispositivo
          (function(){{
            var mapsBtn = document.getElementById('mapsBtn');
            var mapsAddr = "{html.escape(address_text)}";
            if (mapsBtn && mapsAddr){{
              mapsBtn.addEventListener('click', function(e){{
                try {{
                  var ua = navigator.userAgent || '';
                  var href = '';
                  if (/iPhone|iPad|iPod/i.test(ua)){{
                    href = 'http://maps.apple.com/?q=' + encodeURIComponent(mapsAddr);
                  }} else if (/Android/i.test(ua)){{
                    href = 'geo:0,0?q=' + encodeURIComponent(mapsAddr);
                  }} else {{
                    href = 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(mapsAddr);
                  }}
                  mapsBtn.setAttribute('href', href);
                }} catch(_e){{}}
              }}, {{ passive: true }});
            }}
          }})();
        }}
        // Toggle subseção Modo Offline sem navegar (versão robusta)
        (function(){{
          var off = document.getElementById('offlineBtn');
          var sec = document.getElementById('offlineSection');
          if (!off || !sec) return;

          off.setAttribute('aria-controls', 'offlineSection');
          off.setAttribute('aria-expanded', 'false');

          function isVisible(el){{
            // visibilidade real, não apenas style inline
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
    owner = card.get("user", "")
    prof = db["profiles"].get(owner, {})
    who = current_user_email(request)
    is_owner = bool(owner and who == owner)
    if is_owner:
        view_count = get_card_view_count(uid)
    else:
        view_count = increment_card_view(uid) if should_track_view(request, slug) else get_card_view_count(uid)
    return visitor_public_card(prof, slug, is_owner, view_count)


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
    # Photo handling: embed as base64 (preferred), fallback to URI, with line folding
    photo_line = None
    photo_url = (prof.get("photo_url", "") or "").strip()
    if photo_url:
        try:
            fname = os.path.basename(photo_url.split("?", 1)[0])
            local_path = os.path.join(UPLOADS, fname)
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
            # fallback to absolute URL
            abs_url = photo_url
            if abs_url.startswith("/"):
                abs_url = f"{PUBLIC_BASE}{photo_url}"
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
def edit_card(slug: str, request: Request, saved: str = "", error: str = "", pwd: str = ""):
    db, uid, card = find_card_by_slug(slug)
    if not card:
        raise HTTPException(404, "Cartao nao encontrado")
    owner = card.get("user", "")
    who = current_user_email(request)
    if who != owner:
        return RedirectResponse(f"/{slug}", status_code=303)
    prof = load()["profiles"].get(owner, {})
    show_grev = bool(prof.get("google_review_show", True))
    # Cor do tema do cartão (hex #RRGGBB)
    theme_base = prof.get("theme_color", "#000000") or "#000000"
    if not re.fullmatch(r"#([0-9a-fA-F]{6})", theme_base or ""):
        theme_base = "#000000"
    bg_hex = theme_base + "30"
    links = prof.get("links", [])
    photo_url = resolve_photo(prof.get("photo_url"))
    while len(links) < 4:
        links.append({"label": "", "href": ""})
    banners: list[str] = []
    if error:
        banners.append(f"<div class='banner bad'>{html.escape(error)}</div>")
    if str(saved) == "1":
        if not profile_complete(prof):
            banners.append("<div class='banner'>Alteracoes salvas. Para publicar seu cartao, adicione ao menos um meio de contato (WhatsApp, e-mail publico ou um link).</div>")
        else:
            banners.append("<div class='banner ok'>Alteracoes salvas.</div>")
    if str(pwd) == "1":
        banners.append("<div class='banner ok'>Senha atualizada com sucesso.</div>")
    notice = "".join(banners)
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
      .section-kicker{{text-transform:uppercase;font-size:11px;letter-spacing:.3px;color:#9aa0a6;margin:0 0 6px}}
      .section-head{{display:flex;flex-direction:column;gap:4px;margin-bottom:14px}}
      .section-title{{margin:0;font-size:20px}}
      .section-desc{{margin:0;font-size:13px;color:#9aa0a6}}
      .section-grid{{display:grid;gap:14px}}
      .section-grid.two-col{{grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}}
      .form-control{{display:flex;flex-direction:column;gap:6px}}
      .form-control label{{font-weight:600;font-size:13px;color:#eaeaea}}
      .form-control input{{border:1px solid #2a2a2a;background:#0b0b0c;color:#eaeaea;padding:10px;border-radius:10px}}
      .form-control.full{{grid-column:1 / -1}}
      .visual-grid{{display:grid;gap:18px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}}
      .cta-row{{display:flex;flex-direction:column;gap:16px}}
      .cta-card{{border:1px solid #242427;border-radius:12px;padding:16px;background:#0f0f10}}
      .cta-card h4{{margin:0 0 6px;font-size:15px}}
      .cta-card p{{margin:0 0 10px;font-size:13px;color:#9aa0a6}}
      .panel-actions{{display:flex;align-items:center;gap:10px;flex-wrap:wrap}}
      .links-grid-edit{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
      .password-fields{{margin-top:14px;display:grid;gap:12px}}
      .password-fields.is-hidden{{display:none}}
      .btn.ghost{{background:transparent;border:1px solid #2a2a2a;color:#eaeaea}}
      .btn.primary{{background:#eaeaea;color:#0b0b0c;font-weight:600}}
      .edit-actions{{position:sticky;bottom:0;padding:0;margin-top:24px;background:linear-gradient(180deg,rgba(11,11,12,0) 0%,rgba(11,11,12,.85) 30%,rgba(11,11,12,1) 70%)}}
      .edit-actions-inner{{display:flex;gap:12px;padding:12px 0;border-top:1px solid #1f1f1f}}
      .edit-actions-inner .btn{{flex:1;text-align:center}}
    </style>
    </head>
    <body><main class='wrap'>
      {notice}
      <form id='editForm' method='post' action='/edit/{html.escape(slug)}' enctype='multipart/form-data'>
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
                    <div class='muted' style='font-size:12px;margin-top:4px'>Tamanho máximo: 2 MB (JPEG/PNG)</div>
                  </div>
                </div>
                <input type='file' id='photoInput' name='photo' accept='image/jpeg,image/png' style='display:none'>
              </div>
              <div class='form-control'>
                <label>Cor do cartão</label>
                <input type='color' id='themeColor' name='theme_color' value='{html.escape(theme_base)}' style='height: 48px'>
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
                <label>Nome</label>
                <input name='full_name' value='{html.escape(prof.get('full_name',''))}' placeholder='Nome completo' required>
              </div>
              <div class='form-control'>
                <label>Cargo | Empresa</label>
                <input name='title' value='{html.escape(prof.get('title',''))}' placeholder='Ex.: Diretor | Soomei'>
              </div>
              <div class='form-control'>
                <label>WhatsApp</label>
                <input name='whatsapp' id='whatsapp' inputmode='numeric' autocomplete='tel' placeholder='+55 (00) 00000-0000' value='{html.escape(prof.get('whatsapp',''))}' maxlength='19'>
              </div>
              <div class='form-control'>
                <label>Email público</label>
                <input name='email_public' type='email' value='{html.escape(prof.get('email_public',''))}' placeholder='contato@exemplo.com'>
              </div>
              <div class='form-control'>
                <label>Site</label>
                <input name='site_url' type='url' placeholder='https://seusite.com' value='{html.escape(prof.get('site_url',''))}'>
              </div>
              <div class='form-control full'>
                <label>Endereço</label>
                <input name='address' value='{html.escape(prof.get('address',''))}' placeholder='Rua, número - Cidade/UF'>
              </div>
            </div>
          </section>

          <section class='edit-section'>
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
            </div>

        <div class='cta-card' style='margin-top:12px'>
          <div style='display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:8px'>
            <label style='display:flex;align-items:center;gap:8px;margin:0'>
              <svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48' width='18' height='18' class='g-icon'>
                <path fill='#fff' d='M24 9.5c3.94 0 7.06 1.7 9.18 3.12l6.77-6.77C36.26 2.52 30.62 0 24 0 14.5 0 6.36 5.4 2.4 13.22l7.9 6.14C12.12 13.32 17.63 9.5 24 9.5z'/>
                <path fill='#fff' d='M46.5 24.5c0-1.6-.14-3.1-.4-4.5H24v9h12.7c-.6 3.2-2.4 5.9-5.1 7.7l7.9 6.1C43.8 38.8 46.5 32.1 46.5 24.5z'/>
                <path fill='#fff' d='M10.3 28.36A14.5 14.5 0 0 1 9.5 24c0-1.53.26-3.02.74-4.36l-7.9-6.14A23.74 23.74 0 0 0 0 24c0 3.83.93 7.46 2.54 10.64l7.76-6.28z'/>
                <path fill='#fff' d='M24 48c6.48 0 11.92-2.13 15.89-5.8l-7.9-6.14C29.8 37.75 27.06 38.5 24 38.5c-6.37 0-11.88-3.82-14.7-9.86l-7.9 6.14C6.36 42.6 14.5 48 24 48z'/>
              </svg>
              <span>Link de avaliação do Google</span>
            </label>

            <!-- TOGGLE ON/OFF -->
            <label class='switch' style='display:inline-flex;align-items:center;gap:8px;cursor:pointer'>
              <input type='checkbox' name='google_review_show' value='1' {'checked' if show_grev else ''} style='display:none'>
              <span class='switch-ui' aria-hidden='true' style='width:42px;height:24px;border-radius:999px;background:#2a2a2a;position:relative;display:inline-block;transition:.2s'>
                <span class='knob' style='position:absolute;top:3px;left:{'22px' if show_grev else '3px'};width:18px;height:18px;border-radius:50%;background:#eaeaea;transition:left .2s'></span>
              </span>
              <span class='muted' style='font-size:12px'>{'Exibindo' if show_grev else 'Oculto'}</span>
            </label>
          </div>

          <input name='google_review_url' type='url' placeholder='https://search.google.com/local/writereview?...'
                value='{html.escape(prof.get("google_review_url", ""))}'>

          <a class="link-google-review" target='_blank' rel='noopener'
            href='https://support.google.com/business/answer/3474122?hl=pt-BR#:~:text=Para%20encontrar%20o%20link%20da,Pesquisa%20Google%2C%20selecione%20Solicitar%20avalia%C3%A7%C3%B5es'>
            Toque aqui para ver como encontrar o link de Avaliação do Google Meu Negócio
          </a>
        </div>

          </section>

          <section class='edit-section'>
            <p class='section-kicker'>Presença digital</p>
            <div class='section-head'>
              <h2 class='section-title'>Links em destaque</h2>
              <p class='section-desc'>Adicione até quatro links personalizados.</p>
            </div>
            <div class='links-grid-edit'>
              <div class='form-control'>
                <label>Rótulo 1</label>
                <input name='label1' value='{html.escape(links[0].get('label',''))}'>
              </div>
              <div class='form-control'>
                <label>URL 1</label>
                <input name='href1' value='{html.escape(links[0].get('href',''))}'>
              </div>
              <div class='form-control'>
                <label>Rótulo 2</label>
                <input name='label2' value='{html.escape(links[1].get('label',''))}'>
              </div>
              <div class='form-control'>
                <label>URL 2</label>
                <input name='href2' value='{html.escape(links[1].get('href',''))}'>
              </div>
              <div class='form-control'>
                <label>Rótulo 3</label>
                <input name='label3' value='{html.escape(links[2].get('label',''))}'>
              </div>
              <div class='form-control'>
                <label>URL 3</label>
                <input name='href3' value='{html.escape(links[2].get('href',''))}'>
              </div>
              <div class='form-control'>
                <label>Rótulo 4</label>
                <input name='label4' value='{html.escape(links[3].get('label',''))}'>
              </div>
              <div class='form-control'>
                <label>URL 4</label>
                <input name='href4' value='{html.escape(links[3].get('href',''))}'>
              </div>
            </div>
          </section>

          <section class='edit-section'>
            <p class='section-kicker'>Segurança</p>
            <div class='section-head'>
              <h2 class='section-title'>Senha e acesso</h2>
              <p class='section-desc'>Troque sua senha sempre que identificar atividade suspeita.</p>
            </div>
            <button type='button' class='btn ghost' id='togglePassword' aria-expanded='false'>Alterar senha</button>
            <div id='passwordFields' class='password-fields is-hidden'>
              <div class='form-control'>
                <label>Senha atual</label>
                <input type='password' name='current_password' autocomplete='current-password' placeholder='Digite sua senha atual'>
              </div>
              <div class='form-control'>
                <label>Nova senha</label>
                <input type='password' name='new_password' autocomplete='new-password' minlength='8' placeholder='Mínimo de 8 caracteres'>
              </div>
              <div class='form-control'>
                <label>Confirmar nova senha</label>
                <input type='password' name='confirm_password' autocomplete='new-password' minlength='8' placeholder='Repita a nova senha'>
              </div>
              <p class='muted hint'>Sua sessão permanecerá ativa após a troca.</p>
            </div>
          </section>
        </div>

        <div class='edit-actions'>
          <div class='edit-actions-inner'>
            <button type='button' class='btn ghost' id='backToCard'>Voltar</button>
            <button type='submit' class='btn primary'>Salvar alterações</button>
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
          var togglePwd = document.getElementById('togglePassword');
          var pwdFields = document.getElementById('passwordFields');
          if (togglePwd && pwdFields){{
            function setState(open){{
              if (open){{
                pwdFields.classList.remove('is-hidden');
                togglePwd.textContent = 'Cancelar alteração de senha';
                togglePwd.setAttribute('aria-expanded','true');
              }} else {{
                pwdFields.classList.add('is-hidden');
                togglePwd.textContent = 'Alterar senha';
                togglePwd.setAttribute('aria-expanded','false');
                var inputs = pwdFields.querySelectorAll('input');
                Array.prototype.forEach.call(inputs, function(inp){{ inp.value = ''; }});
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
          // Deixa o knob do switch animado mesmo sem CSS externo
          var sw = document.querySelector("input[name='google_review_show']");
          if (!sw) return;
          var ui = sw.parentElement && sw.parentElement.querySelector('.switch-ui');
          var knob = ui && ui.querySelector('.knob');
          const UID = "{html.escape(uid)}";
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
            '        <div id="slugInfoTip" class="info-tip" role="tooltip" aria-hidden="true">Slug é o endereço curto da sua URL pública. Use 3–30 caracteres minúsculos, números ou hífen. Ex.: seu-nome</div>'+
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
              msg.innerHTML = '<span class="bad">Use 3–30 minúsculos/números/hífen.</span>';
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
              try{{
                var resp = await fetch('/slug/select/'+encodeURIComponent(UID), {{
                  method: 'POST',
                  headers: {{'Content-Type':'application/x-www-form-urlencoded'}},
                  body: 'value='+encodeURIComponent(v)
                }});
                if (resp.ok){{
                  window.location.href = '/edit/'+encodeURIComponent(v);
                }} else if (resp.status === 409){{
                  msg.innerHTML = '<span class="bad">Indisponível, tente outro.</span>';
                }} else {{
                  msg.innerHTML = '<span class="bad">Erro ao salvar. Tente novamente.</span>';
                }}
              }}catch(_e){{
                msg.innerHTML = '<span class="bad">Erro de rede. Tente novamente.</span>';
              }}
            }}
          }}, true);

          // abre modal
          btn.addEventListener('click', function(e){{ e.preventDefault(); openModal(); }});
          async function salvarSlug(novo) {{
            return fetch("/slug/select/" + encodeURIComponent(UID), {{
              method: "POST",
              headers: {{ "Content-Type": "application/x-www-form-urlencoded" }},
              body: "value=" + encodeURIComponent(novo)
            }});
          }}
        }})();
        </script>
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
            <label>Tipo da chave</label>
            <select id='pixType' style='width:100%;margin:8px 0;padding:10px;border-radius:10px;border:1px solid #2a2a2a;background:#0b0b0c;color:#eaeaea'>
              <option value='aleatoria'>Aleatória</option>
              <option value='email'>E-mail</option>
              <option value='telefone'>Telefone</option>
              <option value='cpf'>CPF</option>
              <option value='cnpj'>CNPJ</option>
            </select>
            <label>Valor da chave</label>
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
      <p><a class='muted' href='/auth/logout?next=/{html.escape(slug)}'>Sair</a></p>
    </main></body></html>
    """
    return HTMLResponse(_brand_footer_inject(html_form))


@app.post("/edit/{slug}")
async def save_edit(slug: str, request: Request, full_name: str = Form(""), title: str = Form(""),
               whatsapp: str = Form(""), email_public: str = Form(""), site_url: str = Form(""), address: str = Form(""),
               google_review_url: str = Form(""),
               google_review_show: str = Form(""),
               label1: str = Form(""), href1: str = Form(""),
               label2: str = Form(""), href2: str = Form(""),
               label3: str = Form(""), href3: str = Form(""),
               label4: str = Form(""), href4: str = Form(""),
               theme_color: str = Form(""),
               pix_key: str = Form(""),
               current_password: str = Form(""),
               new_password: str = Form(""),
               confirm_password: str = Form(""),
               photo: UploadFile | None = File(None)):
    db, uid, card = find_card_by_slug(slug)
    if not card:
        raise HTTPException(404, "Cartao nao encontrado")
    owner = card.get("user", "")
    who = current_user_email(request)
    if who != owner:
        return RedirectResponse(f"/{slug}", status_code=303)
    db2 = db_defaults(load())
    def redirect_error(msg: str):
        return RedirectResponse(f"/edit/{slug}?error={urlparse.quote_plus(msg)}", status_code=303)
    prof = db2["profiles"].get(owner, {})
    prof.update({
        "full_name": full_name.strip(),
        "title": title.strip(),
        "whatsapp": sanitize_phone(whatsapp),
        "email_public": email_public.strip(),
        "site_url": (site_url or "").strip(),
        "address": (address or "").strip(),
        "google_review_url": (google_review_url or "").strip(),
        "google_review_show": bool(google_review_show),
    })
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
    pwd_changed = False
    current_password = (current_password or "").strip()
    new_password = (new_password or "").strip()
    confirm_password = (confirm_password or "").strip()
    if current_password or new_password or confirm_password:
        if not (current_password and new_password and confirm_password):
            return redirect_error("Preencha todos os campos de senha.")
        if len(new_password) < 8:
            return redirect_error("Nova senha deve ter no minimo 8 caracteres.")
        if new_password != confirm_password:
            return redirect_error("As senhas nao conferem.")
        user = db2["users"].get(owner)
        if not user or user.get("pwd") != h(current_password):
            return redirect_error("Senha atual incorreta.")
        user["pwd"] = h(new_password)
        db2["users"][owner] = user
        pwd_changed = True
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
        etag = hashlib.md5(data).hexdigest()[:8]
        prof["photo_url"] = f"/static/uploads/{filename}?v={etag}"
    db2["profiles"][owner] = prof
    save(db2)
    # Se perfil ainda estiver incompleto ou senha foi alterada, mantem usuario na edicao com aviso
    if not profile_complete(prof) or pwd_changed:
        params = ["saved=1"]
        if pwd_changed:
            params.append("pwd=1")
        return RedirectResponse(f"/edit/{slug}?{'&'.join(params)}", status_code=303)
    return RedirectResponse(f"/{slug}?saved=1", status_code=303)


@app.get("/blocked", response_class=HTMLResponse)
def blocked(request: Request):
    return templates.TemplateResponse("blocked.html", {"request": request})


@app.get("/{slug}", response_class=HTMLResponse)
def root_slug(slug: str, request: Request):
    db, uid, card = find_card_by_slug(slug)
    if card and card.get("vanity") and slug != card.get("vanity"):
        return RedirectResponse(f"/{html.escape(card.get('vanity'))}", status_code=302)
    if not card:
        return RedirectResponse("/invalid", status_code=302)
    if not card.get("status") or card.get("status") == "pending":
        return RedirectResponse(f"/onboard/{html.escape(uid)}", status_code=302)
    if card.get("status") == "blocked":
        return RedirectResponse("/blocked", status_code=302)
    owner = card.get("user", "")
    prof = load()["profiles"].get(owner, {})
    who = current_user_email(request)
    is_owner = bool(owner and who == owner)
    # Offline flow (vCard QR) — handled early so it is not bypassed by other returns
    offline = request.query_params.get("offline", "")
    if offline:
        full_name = (prof.get("full_name", "") or slug) if prof else slug
        title = prof.get("title", "") if prof else ""
        email_pub = (prof.get("email_public", "") or "") if prof else ""
        wa_raw = (prof.get("whatsapp", "") or "") if prof else ""
        wa_digits = "".join([c for c in wa_raw if c.isdigit()])
        share_url = f"{PUBLIC_BASE}/{slug}"
        # PHOTO inline for offline QR (downscaled) or URI fallback
        photo_line = None
        photo_url = (prof.get("photo_url", "") or "").strip() if prof else ""
        if photo_url:
            try:
                fname = os.path.basename(photo_url.split("?", 1)[0])
                local_path = os.path.join(UPLOADS, fname)
                data_b64 = None
                try:
                    from PIL import Image  # type: ignore
                    im = Image.open(local_path).convert("RGB")
                    im.thumbnail((160,160))
                    _tmp = io.BytesIO(); im.save(_tmp, format='JPEG', quality=70); _tmp.seek(0)
                    raw = _tmp.read()
                    data_b64 = base64.b64encode(raw).decode('ascii') if raw else None
                    typ = 'JPEG'
                except Exception:
                    with open(local_path, 'rb') as fh:
                        raw = fh.read()
                    if raw:
                        ext = (os.path.splitext(fname)[1] or '').lower()
                        typ = 'JPEG' if ext in ('.jpg','.jpeg') else ('PNG' if ext == '.png' else 'JPEG')
                        data_b64 = base64.b64encode(raw).decode('ascii')
                if data_b64:
                    chunks = [data_b64[i:i+76] for i in range(0, len(data_b64), 76)]
                    folded = "\r\n ".join(chunks)
                    photo_line = f"PHOTO;ENCODING=b;TYPE={typ}:{folded}"
            except Exception:
                abs_url = photo_url
                if abs_url.startswith('/'):
                    abs_url = f"{PUBLIC_BASE}{photo_url}"
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
            buf = io.BytesIO(); img.save(buf, format='PNG'); buf.seek(0)
            return "data:image/png;base64," + base64.b64encode(buf.read()).decode('ascii')
        data_url = ""
        try:
            data_url = _qr_png(_build_off(True))
        except Exception:
            try:
                data_url = _qr_png(_build_off(False))
            except Exception:
                try:
                    import qrcode.image.svg as qsvg  # type: ignore
                    buf2 = io.BytesIO(); qrcode.make(_build_off(False), image_factory=qsvg.SvgImage).save(buf2)
                    data_url = "data:image/svg+xml;base64," + base64.b64encode(buf2.getvalue()).decode('ascii')
                except Exception:
                    return HTMLResponse("<h1>Falha ao gerar QR Offline</h1>", status_code=500)
        theme_base = (prof.get("theme_color", "#000000") or "#000000") if prof else "#000000"
        if not re.fullmatch(r"#([0-9a-fA-F]{6})", theme_base or ""):
            theme_base = "#000000"
        bg_hex = theme_base + "30"
        photo = html.escape(prof.get("photo_url", "")) if prof else ""
        off_page = f"""
        <!doctype html><html lang='pt-br'><head>
        <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <link rel='stylesheet' href='{CSS_HREF}'><title>Modo Offline</title></head><body>
        <main class='wrap'>
          <section class='card carbon card-center' style='background-color: {html.escape(bg_hex)}'>
            <div class='topbar'>
              <a class='icon-btn top-left' href='/{html.escape(slug)}' aria-label='Voltar' title='Voltar'>&larr;</a>
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
        return HTMLResponse(_brand_footer_inject(off_page))
    if who == owner and not card.get("vanity"):
        return RedirectResponse(f"/slug/select/{html.escape(uid)}", status_code=302)
    if is_owner and not profile_complete(prof):
        return RedirectResponse(f"/edit/{html.escape(slug)}", status_code=302)
    # Pix flow handling
    pix_mode = request.query_params.get("pix", "")
    if pix_mode:
        pix_key = (prof.get("pix_key", "") or "").strip()
        if not pix_key:
            return RedirectResponse(f"/{html.escape(slug)}", status_code=302)
        if pix_mode in ("amount", "1"):
            photo = html.escape(prof.get("photo_url", "")) if prof else ""
            # Cor do tema para o card do fluxo Pix (usa mesma lógica do card público)
            theme_base = (prof.get("theme_color", "#000000") or "#000000") if prof else "#000000"
            if not re.fullmatch(r"#([0-9a-fA-F]{6})", theme_base or ""):
                theme_base = "#000000"
            bg_hex = theme_base + "30"
            amt_page = f"""
            <!doctype html><html lang='pt-br'><head>
            <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
            <link rel='stylesheet' href='{CSS_HREF}'><title>Pagamento Pix</title></head><body>
            <main class='wrap'>
              <section class='card carbon card-center' style='background-color: {html.escape(bg_hex)}'>
                <div class='topbar'>
                  <a class='icon-btn top-left' href='/{html.escape(slug)}' aria-label='Voltar' title='Voltar'>&larr;</a>
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
                  window.location.href = '/{html.escape(slug)}?pix=qr&v=' + encodeURIComponent(v);
                }});
              }}
            }})();
            </script>
            """
            return HTMLResponse(_brand_footer_inject(amt_page))
        elif pix_mode == "qr":
            raw_v = (request.query_params.get("v", "0") or "0").replace(",", ".")
            try:
                amount = max(0.0, float(raw_v))
            except Exception:
                amount = 0.0
            name = (prof.get("full_name", "") if prof else "") or slug
            city = (prof.get("city", "") if prof else "") or "BRASILIA"
            payload = build_pix_emv(pix_key, amount if amount > 0 else None, name, city, txid="***")
            # Gera imagem do QR com fundo branco e margem adequada; fallback para SVG
            data_url = ""
            try:
                qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
                qr.add_data(payload)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format='PNG')
                buf.seek(0)
                data_url = "data:image/png;base64," + base64.b64encode(buf.read()).decode('ascii')
            except Exception:
                try:
                    import qrcode.image.svg as qsvg  # type: ignore
                    buf2 = io.BytesIO()
                    qrcode.make(payload, image_factory=qsvg.SvgImage).save(buf2)
                    data_url = "data:image/svg+xml;base64," + base64.b64encode(buf2.getvalue()).decode('ascii')
                except Exception:
                    return HTMLResponse("<h1>Falha ao gerar QR Pix</h1><p>Dependencia ausente para gerar imagens. Instale Pillow (PNG) ou tente novamente.</p>", status_code=500)
            photo = html.escape(prof.get("photo_url", "")) if prof else ""
            # Cor do tema aplicada ao card do QR
            theme_base = (prof.get("theme_color", "#000000") or "#000000") if prof else "#000000"
            if not re.fullmatch(r"#([0-9a-fA-F]{6})", theme_base or ""):
                theme_base = "#000000"
            bg_hex = theme_base + "30"
            qr_page = f"""
            <!doctype html><html lang='pt-br'><head>
            <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
            <link rel='stylesheet' href='{CSS_HREF}'><title>Pix para {html.escape(prof.get('full_name',''))}</title></head><body>
            <main class='wrap'>
              <section class='card carbon card-center' style='background-color: {html.escape(bg_hex)}'>
                <div class='topbar'>
                  <a class='icon-btn top-left' href='/{html.escape(slug)}' aria-label='Voltar' title='Voltar'>&larr;</a>
                  <h1 class='page-title'>Pix para {html.escape(prof.get('full_name',''))}</h1>
                </div>
                {f"<img class='avatar avatar-small' src='{photo}' alt='foto'>" if photo else ""}
                <div>
                  <a id='copyPixCode' class='btn' href='#'>Copiar Codigo Pix</a>
                </div>
                <p class='muted' style='font-size:12px;margin:8px 0 4px'>Abra o App do seu banco e pague atraves do QRCode ou Pix Copia e Cola</p>
                <div style='margin:12px 0'>
                  <img src='{data_url}' alt='QR Pix' style='width:220px;height:220px;image-rendering:pixelated;background:#fff;padding:8px;border-radius:8px'>
                </div>
              </section>
            </main>
            <script>
            (function(){{
              var code = {json.dumps(payload)};
              var b = document.getElementById('copyPixCode');
              function legacyCopy(t){{
                try {{
                  var ta = document.createElement('textarea');
                  ta.value = t;
                  ta.setAttribute('readonly','');
                  ta.style.position='fixed'; ta.style.top='0'; ta.style.left='0'; ta.style.opacity='0';
                  document.body.appendChild(ta);
                  ta.focus(); ta.select(); ta.setSelectionRange(0, ta.value.length);
                  var ok = document.execCommand('copy');
                  document.body.removeChild(ta);
                  return ok;
                }} catch(_e) {{ return false; }}
              }}
              function copyViaEvent(t){{
                var ok = false;
                function oncopy(e){{ try {{ e.clipboardData.setData('text/plain', t); e.preventDefault(); ok = true; }} catch(_e) {{ ok = false; }} }}
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
            return HTMLResponse(_brand_footer_inject(qr_page))
    if is_owner:
        view_count = get_card_view_count(uid)
    else:
        view_count = increment_card_view(uid) if should_track_view(request, slug) else get_card_view_count(uid)
    if not is_owner:
        if not profile_complete(prof):
            return HTMLResponse(_brand_footer_inject("""
            <!doctype html><html lang='pt-br'><head>
            <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
            <link rel='stylesheet' href='{CSS_HREF}'><title>Em construcao</title></head>
            <body><main class='wrap'>
              <h1>Cartao digital em construcao</h1>
              <p>O proprietario ainda nao finalizou o preenchimento deste cartao.</p>
              <p class='muted'><a href='/login'>Sou o dono? Entrar</a></p>
            </main></body></html>
            """))
        return visitor_public_card(prof, slug, False, view_count)
    return visitor_public_card(prof, slug, True, view_count)





