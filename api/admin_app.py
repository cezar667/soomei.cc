from fastapi import FastAPI, HTTPException, Request, Form
from urllib.parse import urlparse
from fastapi.responses import HTMLResponse, RedirectResponse
import os, json, time, secrets, hashlib, html


# --- Minimal, self-contained admin app (MVP) ---
# Uses same JSON store (api/data.json) as the public app.

app = FastAPI(title="Soomei Admin API (MVP)")

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "data.json")
WEB = os.path.join(BASE, "..", "web")
UPLOADS_DIR = os.path.join(WEB, "uploads")


def load_db():
    if os.path.exists(DATA):
        with open(DATA, "r", encoding="utf-8") as f:
            db = json.load(f)
    else:
        db = {}
    # defaults
    db.setdefault("users", {})
    db.setdefault("cards", {})
    db.setdefault("profiles", {})
    db.setdefault("sessions", {})
    db.setdefault("verify_tokens", {})
    db.setdefault("sessions_admin", {})
    return db


def save_db(db):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def h(p: str) -> str:
    # Hash compatível com app público (scrypt)
    return hashlib.scrypt(p.encode(), salt=b"soomei", n=2**14, r=8, p=1).hex()


def _admin_allowed(email: str) -> bool:
    allow = (os.getenv("ADMIN_EMAILS", "") or "").strip()
    if allow:
        allowed = {x.strip().lower() for x in allow.split(",") if x.strip()}
        return (email or "").lower() in allowed
    # Fallback de desenvolvimento: domínio da organização
    return (email or "").lower().endswith("@soomei.com.br")


def _issue_admin_session(db, email: str) -> str:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    db["sessions_admin"][token] = {"email": email, "ts": int(time.time()), "csrf": csrf}
    save_db(db)
    return token


def _count_cards_of_user(db, email: str) -> int:
    c = 0
    for _uid, card in db.get("cards", {}).items():
        if card.get("user") == email:
            c += 1
    return c


def _delete_photo_files(uid: str) -> None:
    try:
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        for ext in (".jpg", ".png"):
            path = os.path.join(UPLOADS_DIR, f"{uid}{ext}")
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
    except Exception:
        pass


def _current_admin_email(request: Request):
    tok = request.cookies.get("admin_session")
    if not tok:
        return None
    db = load_db()
    meta = db.get("sessions_admin", {}).get(tok)
    return meta.get("email") if meta else None


def _current_admin_session(request: Request):
    tok = request.cookies.get("admin_session")
    if not tok:
        return None, None
    db = load_db()
    meta = db.get("sessions_admin", {}).get(tok)
    # Auto-seed CSRF for legacy sessions (created antes da proteção CSRF)
    if isinstance(meta, dict) and not meta.get("csrf"):
        meta["csrf"] = secrets.token_urlsafe(32)
        db["sessions_admin"][tok] = meta
        save_db(db)
    return tok, meta


def _allowed_hosts() -> set[str]:
    hosts = set()
    env = (os.getenv("ADMIN_HOST", "") or "").strip()
    if env:
        for h in env.split(","):
            h = h.strip()
            if h:
                hosts.add(h)
    # Dev defaults
    hosts.update({"localhost:8001", "127.0.0.1:8001"})
    return hosts


def _check_origin(request: Request) -> bool:
    allowed = _allowed_hosts()
    host_hdr = (request.headers.get("host") or "").strip()
    # Sempre aceite o host atual se estiver explicitamente na allowlist
    if host_hdr:
        allowed.add(host_hdr)
    origin = request.headers.get("origin") or ""
    referer = request.headers.get("referer") or ""
    src = origin or referer
    if not src:
        # Sem Origin/Referer: permita se o Host atual estiver permitido (uso comum em dev/LAN)
        return bool(host_hdr and (host_hdr in allowed))
    try:
        netloc = urlparse(src).netloc
    except Exception:
        return False
    return (netloc in allowed)


def _csrf_protect(request: Request, form_token: str) -> None:
    if not _check_origin(request):
        raise HTTPException(403, "invalid origin")
    _, sess = _current_admin_session(request)
    if not (sess and isinstance(sess, dict)):
        raise HTTPException(401, "not authenticated")
    csrf_saved = sess.get("csrf")
    if not csrf_saved or not form_token or csrf_saved != form_token:
        raise HTTPException(403, "invalid csrf token")


def require_admin(request: Request) -> str:
    who = _current_admin_email(request)
    if not who:
        raise HTTPException(status_code=401, detail="not authenticated")
    if not _admin_allowed(who):
        raise HTTPException(status_code=403, detail="forbidden")
    # Enforce verified email
    db = load_db()
    u = db.get("users", {}).get(who)
    if not (u and u.get("email_verified_at")):
        raise HTTPException(status_code=403, detail="email not verified")
    return who


@app.get("/login", response_class=HTMLResponse)
def login_page(next: str = "/", error: str = ""):
    msg = ""
    if error:
        mapping = {
            "credenciais": "Credenciais inválidas.",
            "nao_autorizado": "Usuário não autorizado para o admin.",
            "nao_verificado": "E‑mail não verificado. Confirme seu e‑mail antes de acessar o admin.",
        }
        msg = mapping.get(error, "Erro ao autenticar.")
    return HTMLResponse(
        f"""
        <!doctype html><html lang='pt-br'><head>
        <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <link rel=\"stylesheet\" href=\"https://unpkg.com/@picocss/pico@2.0.6/css/pico.min.css\"> 
        <title>Admin | Login</title></head>
        <body><main class=\"container\">
          <article>
            <h1>Admin | Login</h1>
            {('<mark role=\'alert\'>' + html.escape(msg) + '</mark>') if msg else ''}
            <form method='post' action='/login'>
              <input type='hidden' name='next' value='{html.escape(next)}'>
              <label>E-mail</label>
              <input name='email' type='email' required>
              <label>Senha</label>
              <input name='password' type='password' required>
              <button style='margin-top:12px'>Entrar</button>
            </form>
            <small>Somente administradores autorizados.</small>
          </article>
        </main></body></html>
        """
    )


@app.post("/login")
def do_login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/")):
    # Basic Origin/Referer enforcement for login
    if not _check_origin(request):
        return RedirectResponse("/login?error=credenciais", status_code=303)
    db = load_db()
    u = db.get("users", {}).get(email)
    if not u:
        return RedirectResponse("/login?error=credenciais", status_code=303)
    if u.get("pwd") != h(password):
        return RedirectResponse("/login?error=credenciais", status_code=303)
    # Require verified email for admin login
    if not u.get("email_verified_at"):
        return RedirectResponse("/login?error=nao_verificado", status_code=303)
    if not _admin_allowed(email):
        return RedirectResponse("/login?error=nao_autorizado", status_code=303)
    tok = _issue_admin_session(db, email)
    resp = RedirectResponse(next or "/", status_code=303)
    resp.set_cookie(
        "admin_session",
        value=tok,
        httponly=True,
        samesite="lax",
        secure=False,  # defina True atrás de HTTPS/proxy
        max_age=7 * 24 * 3600,
        path="/",
    )
    return resp


@app.get("/logout")
def logout(request: Request):
    db = load_db()
    tok = request.cookies.get("admin_session")
    if tok:
        db.get("sessions_admin", {}).pop(tok, None)
        save_db(db)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("admin_session", path="/")
    return resp


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    try:
        who = require_admin(request)
    except HTTPException:
        return RedirectResponse("/login?next=/", status_code=303)
    db = load_db()
    cards = db.get("cards", {})
    total = len(cards)
    active = sum(1 for c in cards.values() if (c.get("status") or "").lower() == "active")
    pending = sum(1 for c in cards.values() if (c.get("status") or "").lower() == "pending")
    blocked = sum(1 for c in cards.values() if (c.get("status") or "").lower() == "blocked")
    return HTMLResponse(
        f"""
        <!doctype html><html lang='pt-br'><head>
        <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <link rel="stylesheet" href="https://unpkg.com/@picocss/pico@2.0.6/css/pico.min.css">
        <title>Admin | Dashboard</title></head>
        <body><main class="container">
          <nav>
            <ul>
              <li><strong>Admin</strong></li>
            </ul>
            <ul>
              <li><a href='/cards'>Cartões</a></li>
              <li><a href='/users'>Usuários</a></li>
              <li><a href='/logout'>Sair</a></li>
            </ul>
          </nav>
          <article>
            <h3>Resumo</h3>
            <ul>
              <li>Total de cartões: <b>{total}</b></li>
              <li>Ativos: <b>{active}</b> · Pendentes: <b>{pending}</b> · Bloqueados: <b>{blocked}</b></li>
            </ul>
            <p><a href='/cards' role='button'>Ver cartões</a> <a href='/users' role='button' class='secondary'>Ver usuários</a></p>
          </article>
        </main></body></html>
        """
    )


@app.get("/cards", response_class=HTMLResponse)
def list_cards(request: Request, q: str = "", status: str = ""):
    try:
        require_admin(request)
    except HTTPException:
        return RedirectResponse("/login?next=/cards", status_code=303)
    db = load_db()
    # CSRF token for forms
    _tok, _sess = _current_admin_session(request)
    csrf = (_sess or {}).get("csrf", "")
    items = []
    for uid, meta in db.get("cards", {}).items():
        st = (meta.get("status") or "").lower()
        user = meta.get("user", "")
        vanity = meta.get("vanity", "")
        if status and st != status.lower():
            continue
        if q:
            qq = q.lower()
            if qq not in uid.lower() and qq not in (user or "").lower() and qq not in (vanity or "").lower():
                continue
        items.append((uid, st, user, vanity))
    items.sort(key=lambda t: (t[1] != "active", t[3] or t[0]))

    # Optional notice
    qp = request.query_params
    notice = ""
    ok = qp.get("ok")
    err = qp.get("error")
    if ok == "reset" and qp.get("pin") and qp.get("uid"):
        notice = f"<mark role='status'>Cartão <b>{html.escape(qp.get('uid'))}</b> reiniciado. Novo PIN: <b>{html.escape(qp.get('pin'))}</b></mark>"
    elif ok == "blocked" and qp.get("uid"):
        notice = f"<mark role='status'>Cartão <b>{html.escape(qp.get('uid'))}</b> bloqueado com sucesso.</mark>"
    elif ok == "activated" and qp.get("uid"):
        notice = f"<mark role='status'>Cartão <b>{html.escape(qp.get('uid'))}</b> ativado com sucesso.</mark>"
    elif ok == "created" and qp.get("uid"):
        notice = f"<mark role='status'>Cartão <b>{html.escape(qp.get('uid'))}</b> criado.</mark>"
    elif ok == "1":
        notice = "<mark role='status'>Ação concluída.</mark>"
    elif err:
        mapping = {
            "uid_ja_existe": "UID já existe.",
            "uid": "UID inválido.",
        }
        notice = f"<mark role='alert'>{html.escape(mapping.get(err, 'Erro ao processar ação.'))}</mark>"

    rows = []
    for uid, st, user, vanity in items:
        rows.append(
            "<tr>"
            f"<td>{html.escape(uid)}</td>"
            f"<td>{html.escape(vanity or '')}</td>"
            f"<td>{html.escape(st)}</td>"
            f"<td>{html.escape(user or '')}</td>"
            f"<td>"
            f"<form method='post' action='/cards/{html.escape(uid)}/block' style='display:inline'>"
            f"<input type='hidden' name='csrf_token' value='{html.escape(csrf)}'>"
            f"<button {'disabled' if st=='blocked' else ''}>Bloquear</button></form> "
            f"<form method='post' action='/cards/{html.escape(uid)}/unblock' style='display:inline'>"
            f"<input type='hidden' name='csrf_token' value='{html.escape(csrf)}'>"
            f"<button {'disabled' if st=='active' else ''}>Ativar</button></form> "
            f"<form method='post' action='/cards/{html.escape(uid)}/reset' style='display:inline' onsubmit=\"return confirm('Resetar este cartão? Isso apaga dados associados e volta a pending.');\">"
            f"<input type='hidden' name='csrf_token' value='{html.escape(csrf)}'>"
            f"<button class='secondary'>Reset</button></form> "
            f"<a href='/{html.escape(vanity or uid)}' target='_blank' rel='noopener' role='button' class='contrast'>Abrir</a>"
            f"</td>"
            "</tr>"
        )
    rows_html = "".join(rows) or "<tr><td colspan='5' class='muted'>Nenhum cartão encontrado</td></tr>"

    return HTMLResponse(
        f"""
        <!doctype html><html lang='pt-br'><head>
        <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <link rel="stylesheet" href="https://unpkg.com/@picocss/pico@2.0.6/css/pico.min.css">
        <title>Admin | Cartões</title></head>
        <body><main class="container">
          <nav>
            <ul>
              <li><a href='/'>Dashboard</a></li>
            </ul>
            <ul>
              <li><a href='/users'>Usuários</a></li>
              <li><a href='/logout'>Sair</a></li>
            </ul>
          </nav>
          <h1>Cartões</h1>
          {notice}
          <article>
            <form method='get' action='/cards'>
              <div class="grid">
                <input name='q' placeholder='uid/vanity/e-mail' value='{html.escape(q)}'>
                <input name='status' placeholder='active|pending|blocked' value='{html.escape(status)}'>
                <button>Filtrar</button>
              </div>
            </form>
            <h3 style='margin-top:10px'>Criar novo cartão</h3>
            <form method='post' action='/cards/create'>
              <input type='hidden' name='csrf_token' value='{html.escape(csrf)}'>
              <div class="grid">
                <input name='uid' placeholder='UID' required>
                <input name='pin' placeholder='PIN' required>
                <input name='user' placeholder='E-mail do dono (opcional)'>
                <input name='vanity' placeholder='slug (opcional)'>
                <button>Criar</button>
              </div>
            </form>
            <div style='overflow:auto'>
              <table>
                <thead><tr><th>UID</th><th>Slug</th><th>Status</th><th>User</th><th>Ações</th></tr></thead>
                <tbody>{rows_html}</tbody>
              </table>
            </div>
          </article>
        </main></body></html>
        """
    )


@app.post("/cards/create")
def create_card(request: Request, uid: str = Form(...), pin: str = Form(...), user: str = Form(""), vanity: str = Form(""), csrf_token: str = Form("")):
    _csrf_protect(request, csrf_token)
    try:
        require_admin(request)
    except HTTPException:
        return RedirectResponse("/login?next=/cards", status_code=303)
    uid = (uid or "").strip()
    if not uid:
        return RedirectResponse("/cards?error=uid", status_code=303)
    db = load_db()
    if uid in db.get("cards", {}):
        return RedirectResponse("/cards?error=uid_ja_existe", status_code=303)
    meta = {"uid": uid, "status": "pending", "pin": (pin or "").strip()}
    if (user or "").strip():
        meta["user"] = user.strip()
    if (vanity or "").strip():
        meta["vanity"] = vanity.strip()
    db["cards"][uid] = meta
    save_db(db)
    return RedirectResponse(f"/cards?ok=created&uid={html.escape(uid)}", status_code=303)


@app.post("/cards/{uid}/block")
def block_card(uid: str, request: Request, csrf_token: str = Form("")):
    _csrf_protect(request, csrf_token)
    try:
        require_admin(request)
    except HTTPException:
        return RedirectResponse("/login?next=/cards", status_code=303)
    db = load_db()
    if uid not in db.get("cards", {}):
        raise HTTPException(404, "Card not found")
    db["cards"][uid]["status"] = "blocked"
    save_db(db)
    return RedirectResponse(f"/cards?ok=blocked&uid={html.escape(uid)}", status_code=303)


@app.post("/cards/{uid}/unblock")
def unblock_card(uid: str, request: Request, csrf_token: str = Form("")):
    _csrf_protect(request, csrf_token)
    try:
        require_admin(request)
    except HTTPException:
        return RedirectResponse("/login?next=/cards", status_code=303)
    db = load_db()
    if uid not in db.get("cards", {}):
        raise HTTPException(404, "Card not found")
    db["cards"][uid]["status"] = "active"
    save_db(db)
    return RedirectResponse(f"/cards?ok=activated&uid={html.escape(uid)}", status_code=303)


@app.post("/cards/{uid}/reset")
def reset_card(uid: str, request: Request, csrf_token: str = Form("")):
    _csrf_protect(request, csrf_token)
    try:
        require_admin(request)
    except HTTPException:
        return RedirectResponse("/login?next=/cards", status_code=303)
    uid = (uid or "").strip()
    if not uid:
        raise HTTPException(400, "UID invalido")
    db = load_db()
    card = db.get("cards", {}).get(uid)
    owner = card.get("user") if card else None
    # Remove card
    if uid in db.get("cards", {}):
        del db["cards"][uid]
    # Remove photo files
    _delete_photo_files(uid)
    # If owner had no other cards, remove user, profile, sessions, verify_tokens
    if owner:
        others = _count_cards_of_user(db, owner)
        if others == 0:
            db.get("profiles", {}).pop(owner, None)
            db.get("users", {}).pop(owner, None)
            # sessions
            for tok, meta in list(db.get("sessions", {}).items()):
                if meta.get("email") == owner:
                    db["sessions"].pop(tok, None)
            # verify tokens
            for tok, meta in list(db.get("verify_tokens", {}).items()):
                if meta.get("email") == owner:
                    db["verify_tokens"].pop(tok, None)
    # Recreate pending card with new PIN
    digits = "0123456789"
    new_pin = "".join(secrets.choice(digits) for _ in range(6))
    db.setdefault("cards", {})[uid] = {"uid": uid, "status": "pending", "pin": new_pin}
    save_db(db)
    return RedirectResponse(f"/cards?ok=reset&uid={html.escape(uid)}&pin={html.escape(new_pin)}", status_code=303)

@app.get("/users", response_class=HTMLResponse)
def list_users(request: Request):
    try:
        require_admin(request)
    except HTTPException:
        return RedirectResponse("/login?next=/users", status_code=303)
    db = load_db()
    rows = []
    for email, meta in db.get("users", {}).items():
        rows.append(
            "<tr>"
            f"<td>{html.escape(email)}</td>"
            f"<td>{'sim' if _admin_allowed(email) else 'não'}</td>"
            f"<td>{'verificado' if meta.get('email_verified_at') else 'pendente'}</td>"
            "</tr>"
        )
    rows_html = "".join(rows) or "<tr><td colspan='3' class='muted'>Nenhum usuário</td></tr>"
    return HTMLResponse(
        f"""
        <!doctype html><html lang='pt-br'><head>
        <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <link rel="stylesheet" href="https://unpkg.com/@picocss/pico@2.0.6/css/pico.min.css">
        <title>Admin | Usuários</title></head>
        <body><main class="container">
          <nav>
            <ul>
              <li><a href='/'>Dashboard</a></li>
            </ul>
            <ul>
              <li><a href='/cards'>Cartões</a></li>
              <li><a href='/logout'>Sair</a></li>
            </ul>
          </nav>
          <h1>Usuários</h1>
          <article>
            <div style='overflow:auto'>
              <table>
                <thead><tr><th>E-mail</th><th>Admin?</th><th>Status e-mail</th></tr></thead>
                <tbody>{rows_html}</tbody>
              </table>
            </div>
          </article>
        </main></body></html>
        """
    )
