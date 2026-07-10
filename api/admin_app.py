from __future__ import annotations

import html
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.core import csrf
from api.core.config import get_settings
from api.core.http_security import SecurityHeadersMiddleware
from api.core.rate_limiter import rate_limit_ip
from api.core.security import hash_password, verify_password
from api.domain.slugs import is_valid_slug
from api.repositories.sql_repository import PageResult, SQLRepository


app = FastAPI(title="Soomei Admin API")
settings = get_settings()
repo = SQLRepository()
app.add_middleware(SecurityHeadersMiddleware, enforce_hsts=settings.app_env == "prod")

ADMIN_SESSION_TTL_SECONDS = max(600, int(os.getenv("ADMIN_SESSION_TTL_SECONDS", "43200") or 43200))
ADMIN_COOKIE_SECURE = settings.app_env == "prod" or (os.getenv("ADMIN_COOKIE_SECURE") or "").strip() == "1"
ADMIN_HOSTS = {h.strip() for h in (os.getenv("ADMIN_HOST", "") or "").split(",") if h.strip()}
ADMIN_HOSTS.update({"localhost:8001", "127.0.0.1:8001"})
PAGE_SIZE = 25


def _admin_allowed(email: str) -> bool:
    allow = (os.getenv("ADMIN_EMAILS", "") or "").strip()
    if allow:
        allowed = {value.strip().lower() for value in allow.split(",") if value.strip()}
        return (email or "").lower() in allowed
    return (email or "").lower().endswith("@soomei.com.br")


def _check_origin(request: Request) -> bool:
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    host = (request.headers.get("host") or "").strip()
    allowed = set(ADMIN_HOSTS)
    if host:
        allowed.add(host)
    if not origin:
        return True
    try:
        netloc = origin.split("://", 1)[-1].split("/", 1)[0]
    except Exception:
        return False
    return netloc in allowed


def _issue_admin_session(email: str) -> tuple[str, str]:
    csrf_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ADMIN_SESSION_TTL_SECONDS)
    token = repo.create_admin_session(email, csrf_token, expires_at)
    return token, csrf_token


def _load_admin_session(token: Optional[str]):
    if not token:
        return None
    session = repo.get_admin_session(token)
    now = datetime.now(timezone.utc)
    if not session or (session.expires_at and session.expires_at < now):
        repo.delete_admin_session(token)
        return None
    return session


def _csrf_protect(request: Request, form_token: str) -> None:
    if not _check_origin(request):
        raise HTTPException(403, "origem invalida")
    token = request.cookies.get("admin_session")
    session = _load_admin_session(token)
    if not session or session.csrf_token != form_token:
        raise HTTPException(403, "csrf invalido")


def require_admin(request: Request) -> str:
    token = request.cookies.get("admin_session")
    session = _load_admin_session(token)
    if not session:
        raise HTTPException(401, "nao autenticado")
    user = repo.get_user(session.email)
    if not user or not user.email_verified_at:
        raise HTTPException(403, "email nao verificado")
    if not _admin_allowed(session.email):
        raise HTTPException(403, "forbidden")
    return session.email


def _csrf_value(request: Request) -> str:
    token = request.cookies.get("admin_session")
    session = _load_admin_session(token)
    return session.csrf_token if session else ""


def _query_string(**params: object) -> str:
    data = {}
    for key, value in params.items():
        if value in ("", None):
            continue
        data[key] = str(value)
    return urlencode(data)


def _page_link(path: str, page: int, **params: object) -> str:
    query = _query_string(page=page, **params)
    return f"{path}?{query}" if query else path


def _pager_html(path: str, page_result: PageResult, **params: object) -> str:
    if page_result.total <= page_result.page_size:
        return ""
    parts: list[str] = []
    if page_result.page > 1:
        parts.append(
            f"<a class='secondary' href='{html.escape(_page_link(path, page_result.page - 1, **params))}'>Anterior</a>"
        )
    parts.append(f"<span class='admin-pager__status'>Página {page_result.page} de {page_result.pages}</span>")
    if page_result.page < page_result.pages:
        parts.append(
            f"<a class='secondary' href='{html.escape(_page_link(path, page_result.page + 1, **params))}'>Próxima</a>"
        )
    return "<nav class='admin-pager'>" + "".join(parts) + "</nav>"


def _flash_markup(kind: str, message: str) -> str:
    if not message:
        return ""
    safe = html.escape(message)
    klass = "admin-flash admin-flash--ok" if kind == "ok" else "admin-flash admin-flash--error"
    return f"<p class='{klass}' role='status'>{safe}</p>"


def _layout(request: Request | None, title: str, body: str, *, csrf_token: str = "") -> HTMLResponse:
    logout_html = ""
    if csrf_token:
        logout_html = (
            "<form method='post' action='/logout' class='admin-logout'>"
            f"<input type='hidden' name='csrf_token' value='{html.escape(csrf_token)}'>"
            "<button type='submit'>Sair</button>"
            "</form>"
        )
    return HTMLResponse(
        f"""
        <!doctype html><html lang='pt-br'><head>
        <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <link rel="stylesheet" href="https://unpkg.com/@picocss/pico@2.0.6/css/pico.min.css">
        <title>{html.escape(title)}</title>
        <style>
          table {{font-size:14px}}
          td, th {{white-space:nowrap}}
          .admin-shell {{padding-bottom:40px}}
          .admin-nav {{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}}
          .admin-nav ul {{align-items:center}}
          .admin-nav__links {{display:flex;gap:12px;flex-wrap:wrap;align-items:center}}
          .admin-nav__links a {{text-decoration:none}}
          .admin-logout {{margin:0}}
          .admin-logout button {{margin:0}}
          .admin-summary {{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
          .admin-summary article {{margin:0}}
          .admin-pager {{display:flex;align-items:center;justify-content:flex-end;gap:10px;margin-top:14px}}
          .admin-pager__status {{font-size:14px;color:var(--pico-muted-color)}}
          .admin-flash {{display:block;padding:10px 12px;border-radius:10px}}
          .admin-flash--ok {{background:#e9f8ee;color:#134b26}}
          .admin-flash--error {{background:#fdecec;color:#7e1d1d}}
          .admin-inline-form {{display:inline-flex;gap:6px;align-items:center;margin:0 4px 4px 0}}
          .admin-inline-form button, .admin-inline-form a {{margin:0}}
          .admin-compact {{font-size:13px;color:var(--pico-muted-color)}}
          .admin-domain-note {{white-space:normal;min-width:220px}}
        </style>
        </head><body>
        <main class="container admin-shell">
          <nav class="admin-nav">
            <ul><li><strong>Admin</strong></li></ul>
            <div class="admin-nav__links">
              <a href="/">Dashboard</a>
              <a href="/cards">Cartões</a>
              <a href="/domains">Domínios</a>
              <a href="/users">Usuários</a>
              {logout_html}
            </div>
          </nav>
          {body}
        </main>
        </body></html>
        """
    )


def _login_page(*, next_path: str = "/", error: str = "") -> HTMLResponse:
    messages = {
        "credenciais": "Credenciais inválidas.",
        "nao_autorizado": "Usuário não autorizado.",
        "nao_verificado": "E-mail não verificado.",
        "csrf": "Sua sessão expirou. Tente novamente.",
    }
    csrf_token = secrets.token_urlsafe(32)
    body = f"""
      <article>
        <h1>Admin</h1>
        {_flash_markup('error', messages.get(error, ''))}
        <form method='post' action='/login'>
          <input type='hidden' name='next' value='{html.escape(next_path)}'>
          <input type='hidden' name='csrf_token' value='{html.escape(csrf_token)}'>
          <label for='adminEmail'>E-mail</label>
          <input id='adminEmail' name='email' type='email' required autocomplete='email'>
          <label for='adminPassword'>Senha</label>
          <input id='adminPassword' name='password' type='password' required autocomplete='current-password'>
          <button type='submit'>Entrar</button>
        </form>
      </article>
    """
    response = _layout(None, "Admin | Login", body)
    csrf.set_csrf_cookie(response, csrf_token)
    return response


def _redirect_login(path: str) -> RedirectResponse:
    return RedirectResponse(f"/login?next={html.escape(path)}", status_code=303)


def _card_row(card, csrf_token: str) -> str:
    status = (card.status or "").lower()
    owner = html.escape(card.owner_email or "")
    uid = html.escape(card.uid)
    actions = [
        f"<a href='/cards/{uid}' class='secondary' role='button'>Detalhes</a>",
    ]
    if status == "blocked":
        actions.append(
            "<form method='post' action='/cards/{uid}/unblock' class='admin-inline-form'>"
            "<input type='hidden' name='csrf_token' value='{csrf}'>"
            "<button class='secondary' type='submit'>Desbloquear</button>"
            "</form>".format(uid=uid, csrf=html.escape(csrf_token))
        )
    else:
        actions.append(
            "<form method='post' action='/cards/{uid}/block' class='admin-inline-form'>"
            "<input type='hidden' name='csrf_token' value='{csrf}'>"
            "<button class='secondary' type='submit'>Bloquear</button>"
            "</form>".format(uid=uid, csrf=html.escape(csrf_token))
        )
    actions.append(
        "<form method='post' action='/cards/{uid}/reset' class='admin-inline-form'>"
        "<input type='hidden' name='csrf_token' value='{csrf}'>"
        "<button class='secondary' type='submit'>Resetar</button>"
        "</form>".format(uid=uid, csrf=html.escape(csrf_token))
    )
    actions.append(
        "<form method='post' action='/cards/{uid}/delete' class='admin-inline-form' "
        "onsubmit=\"return confirm('Excluir este cartão? Esta ação não pode ser desfeita.');\">"
        "<input type='hidden' name='csrf_token' value='{csrf}'>"
        "<button class='secondary' type='submit'>Excluir</button>"
        "</form>".format(uid=uid, csrf=html.escape(csrf_token))
    )
    return (
        "<tr>"
        f"<td><code>{uid}</code></td>"
        f"<td>{html.escape(card.vanity or '')}</td>"
        f"<td>{owner}</td>"
        f"<td>{html.escape(status)}</td>"
        f"<td>{int(card.metrics_views or 0)}</td>"
        f"<td>{''.join(actions)}</td>"
        "</tr>"
    )


def _cards_alert(request: Request) -> str:
    ok = (request.query_params.get("ok") or "").strip()
    if ok == "created":
        return _flash_markup("ok", "Cartão criado.")
    if ok == "blocked":
        return _flash_markup("ok", "Cartão bloqueado.")
    if ok == "unblocked":
        return _flash_markup("ok", "Cartão desbloqueado.")
    if ok == "deleted":
        return _flash_markup("ok", "Cartão excluído.")
    if ok == "assigned":
        return _flash_markup("ok", "Dono atualizado.")
    if ok == "reset":
        pin_value = (request.query_params.get("pin") or "").strip()
        return _flash_markup("ok", f"Cartão resetado. Novo PIN: {pin_value}")
    error = (request.query_params.get("error") or "").strip()
    error_map = {
        "uid_pin": "UID e PIN são obrigatórios.",
        "uid_existente": "Esse UID já existe.",
        "slug_indisponivel": "Slug indisponível.",
        "user_nao_encontrado": "Usuário não encontrado.",
        "nao_encontrado": "Cartão não encontrado.",
    }
    return _flash_markup("error", error_map.get(error, error))


def _users_alert(request: Request) -> str:
    ok = (request.query_params.get("ok") or "").strip()
    if ok == "pwd":
        return _flash_markup("ok", "Senha redefinida.")
    error = (request.query_params.get("error") or "").strip()
    error_map = {
        "pwd_curto": "A senha deve ter ao menos 8 caracteres.",
    }
    return _flash_markup("error", error_map.get(error, error))


def _domains_alert(request: Request) -> str:
    ok = (request.query_params.get("ok") or "").strip()
    if ok == "approved":
        return _flash_markup("ok", "Domínio aprovado.")
    if ok == "rejected":
        return _flash_markup("ok", "Domínio reprovado.")
    if ok == "disabled":
        return _flash_markup("ok", "Domínio desativado.")
    error = (request.query_params.get("error") or "").strip()
    return _flash_markup("error", error)


def _cleanup_user_if_orphan(email: str, keep_uid: str) -> None:
    remaining_cards = [card for card in repo.get_cards_by_owner(email) if card.uid != keep_uid]
    if remaining_cards:
        return
    repo.delete_profile(email)
    repo.delete_user_sessions(email)
    repo.delete_verify_tokens_for_email(email)
    repo.delete_reset_tokens_for_email(email)
    repo.delete_user(email)


@app.get("/login", response_class=HTMLResponse)
def login_page(next: str = "/", error: str = ""):
    return _login_page(next_path=next or "/", error=error)


@app.post("/login")
def do_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    csrf_token: str = Form(""),
):
    rate_limit_ip(request, "admin:login", limit=5, window_seconds=60)
    if not _check_origin(request):
        return RedirectResponse("/login?error=credenciais", status_code=303)
    try:
        csrf.validate_csrf(request, csrf_token)
    except HTTPException:
        return RedirectResponse("/login?error=csrf", status_code=303)
    user = repo.get_user(email)
    if not user or not verify_password(password, user.password_hash):
        return RedirectResponse("/login?error=credenciais", status_code=303)
    if not user.email_verified_at:
        return RedirectResponse("/login?error=nao_verificado", status_code=303)
    if not _admin_allowed(email):
        return RedirectResponse("/login?error=nao_autorizado", status_code=303)
    next_path = (next or "/").strip() or "/"
    if not next_path.startswith("/"):
        next_path = "/"
    token, session_csrf = _issue_admin_session(email)
    response = RedirectResponse(next_path, status_code=303)
    response.set_cookie(
        "admin_session",
        value=token,
        httponly=True,
        samesite="strict",
        secure=ADMIN_COOKIE_SECURE,
        max_age=ADMIN_SESSION_TTL_SECONDS,
        path="/",
    )
    csrf.set_csrf_cookie(response, csrf.ensure_csrf_token(request))
    response.headers["X-CSRF-Token"] = session_csrf
    return response


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    _csrf_protect(request, csrf_token)
    token = request.cookies.get("admin_session")
    if token:
        repo.delete_admin_session(token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("admin_session", path="/")
    csrf.set_csrf_cookie(response, csrf.ensure_csrf_token(request))
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    try:
        require_admin(request)
    except HTTPException:
        return _redirect_login("/")
    counts = repo.dashboard_card_counts()
    top_views = repo.top_cards_by_views(limit=5)
    rows = "".join(
        f"<tr><td>{html.escape(label)}</td><td>{views}</td></tr>"
        for label, views in top_views
    )
    body = f"""
      <section class='admin-summary'>
        <article><header>Total</header><strong>{counts.get('total', 0)}</strong></article>
        <article><header>Ativos</header><strong>{counts.get('active', 0)}</strong></article>
        <article><header>Pendentes</header><strong>{counts.get('pending', 0)}</strong></article>
        <article><header>Bloqueados</header><strong>{counts.get('blocked', 0)}</strong></article>
      </section>
      <article>
        <h3>Top views</h3>
        <table role='grid'>
          <thead><tr><th>Cartão</th><th>Views</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="2">Sem dados.</td></tr>'}</tbody>
        </table>
      </article>
    """
    return _layout(request, "Admin | Dashboard", body, csrf_token=_csrf_value(request))


@app.get("/cards", response_class=HTMLResponse)
def list_cards(request: Request, q: str = "", status: str = "", page: int = 1):
    try:
        require_admin(request)
    except HTTPException:
        return _redirect_login("/cards")
    page_result = repo.search_cards(q=q, status=status, page=page, page_size=PAGE_SIZE)
    csrf_token = _csrf_value(request)
    rows = "\n".join(_card_row(card, csrf_token) for card in page_result.items)
    pager = _pager_html("/cards", page_result, q=q, status=status)
    body = f"""
      <article>
        <h3>Cartões</h3>
        {_cards_alert(request)}
        <form class='grid' method='get' action='/cards'>
          <input name='q' placeholder='uid, vanity ou email' value='{html.escape(q)}'>
          <select name='status'>
            <option value=''>Status</option>
            <option value='active' {'selected' if status == 'active' else ''}>Ativo</option>
            <option value='pending' {'selected' if status == 'pending' else ''}>Pendente</option>
            <option value='blocked' {'selected' if status == 'blocked' else ''}>Bloqueado</option>
          </select>
          <button type='submit'>Filtrar</button>
        </form>
        <table role='grid'>
          <thead><tr><th>UID</th><th>Vanity</th><th>Dono</th><th>Status</th><th>Views</th><th>Ações</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="6">Nenhum registro.</td></tr>'}</tbody>
        </table>
        {pager}
      </article>
      <article>
        <h4>Criar cartão</h4>
        <form method='post' action='/cards/create'>
          <input type='hidden' name='csrf_token' value='{html.escape(csrf_token)}'>
          <label>UID <input name='uid' required></label>
          <label>PIN <input name='pin' required></label>
          <label>Vanity (opcional) <input name='vanity'></label>
          <label>Dono (e-mail) <input name='user'></label>
          <button type='submit'>Criar</button>
        </form>
      </article>
    """
    return _layout(request, "Admin | Cartões", body, csrf_token=csrf_token)


@app.get("/cards/{uid}", response_class=HTMLResponse)
def card_details(uid: str, request: Request):
    try:
        require_admin(request)
    except HTTPException:
        return _redirect_login(f"/cards/{uid}")
    card = repo.get_card_by_uid(uid)
    if not card:
        return RedirectResponse("/cards?error=nao_encontrado", status_code=303)
    owner = card.owner_email or ""
    profile = repo.get_profile(owner) or {}
    meta = card.custom_domain_meta or {}
    body = f"""
      <article>
        <h3>Detalhes do cartão</h3>
        <p><strong>UID:</strong> {html.escape(card.uid)}</p>
        <p><strong>Vanity:</strong> {html.escape(card.vanity or '')}</p>
        <p><strong>Status:</strong> {html.escape(card.status or '')}</p>
        <p><strong>PIN:</strong> {html.escape(card.pin)}</p>
        <p><strong>Dono:</strong> {html.escape(owner)}</p>
        <p><strong>Billing:</strong> {html.escape(card.billing_status or '')}</p>
        <p><strong>Views:</strong> {int(card.metrics_views or 0)}</p>
        <p><strong>Domínio custom:</strong> {html.escape((meta.get('active_host') or '') or (meta.get('requested_host') or ''))}</p>
      </article>
      <article>
        <h4>Perfil</h4>
        <pre style="white-space:pre-wrap">{html.escape(json.dumps(profile, ensure_ascii=False, indent=2))}</pre>
      </article>
      <p><a class='secondary' href='/cards'>Voltar</a> <a class='secondary' target='_blank' href='/{html.escape(card.vanity or card.uid)}'>Ver público</a></p>
    """
    return _layout(request, f"Admin | Cartão {html.escape(uid)}", body, csrf_token=_csrf_value(request))


@app.post("/cards/create")
def create_card(
    request: Request,
    uid: str = Form(...),
    pin: str = Form(...),
    user: str = Form(""),
    vanity: str = Form(""),
    csrf_token: str = Form(""),
):
    _csrf_protect(request, csrf_token)
    uid_value = (uid or "").strip()
    pin_value = (pin or "").strip()
    vanity_value = (vanity or "").strip()
    owner = (user or "").strip() or None
    if not uid_value or not pin_value:
        return RedirectResponse("/cards?error=uid_pin", status_code=303)
    if repo.get_card_by_uid(uid_value):
        return RedirectResponse("/cards?error=uid_existente", status_code=303)
    if vanity_value and (not is_valid_slug(vanity_value) or repo.slug_exists(vanity_value)):
        return RedirectResponse("/cards?error=slug_indisponivel", status_code=303)
    if owner and not repo.get_user(owner):
        owner = None
    repo.create_card(uid_value, pin_value, vanity_value or None, owner)
    if owner:
        repo.assign_card_owner(uid_value, owner, status="pending")
    return RedirectResponse("/cards?ok=created", status_code=303)


@app.post("/cards/{uid}/block")
def block_card(uid: str, request: Request, csrf_token: str = Form("")):
    _csrf_protect(request, csrf_token)
    repo.update_card_status(uid, "blocked")
    return RedirectResponse("/cards?ok=blocked", status_code=303)


@app.post("/cards/{uid}/unblock")
def unblock_card(uid: str, request: Request, csrf_token: str = Form("")):
    _csrf_protect(request, csrf_token)
    repo.update_card_status(uid, "active")
    return RedirectResponse("/cards?ok=unblocked", status_code=303)


@app.post("/cards/{uid}/reset")
def reset_card(uid: str, request: Request, csrf_token: str = Form(""), new_pin: str = Form("")):
    _csrf_protect(request, csrf_token)
    card = repo.get_card_by_uid(uid)
    if not card:
        return RedirectResponse("/cards?error=nao_encontrado", status_code=303)
    owner = card.owner_email
    pin_value = (new_pin or "").strip() or "".join(secrets.choice("0123456789") for _ in range(6))
    repo.reset_card(uid, new_pin=pin_value, clear_owner=True, clear_vanity=True, clear_custom_domain=True)
    if owner:
        _cleanup_user_if_orphan(owner, uid)
    return RedirectResponse(f"/cards?ok=reset&pin={html.escape(pin_value)}", status_code=303)


@app.post("/cards/{uid}/delete")
def delete_card(uid: str, request: Request, csrf_token: str = Form("")):
    _csrf_protect(request, csrf_token)
    card = repo.get_card_by_uid(uid)
    if card and card.owner_email:
        _cleanup_user_if_orphan(card.owner_email, uid)
    repo.delete_card(uid)
    return RedirectResponse("/cards?ok=deleted", status_code=303)


@app.post("/cards/{uid}/assign")
def assign_owner(uid: str, request: Request, email: str = Form(""), csrf_token: str = Form("")):
    _csrf_protect(request, csrf_token)
    user = repo.get_user(email)
    if not user:
        return RedirectResponse("/cards?error=user_nao_encontrado", status_code=303)
    repo.assign_card_owner(uid, email, status="active", billing_status="ok")
    return RedirectResponse("/cards?ok=assigned", status_code=303)


@app.get("/users", response_class=HTMLResponse)
def list_users(request: Request, q: str = "", page: int = 1):
    try:
        require_admin(request)
    except HTTPException:
        return _redirect_login("/users")
    page_result = repo.search_users(q=q, page=page, page_size=PAGE_SIZE)
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(user.email)}</td>"
        f"<td>{'Sim' if user.email_verified_at else 'Não'}</td>"
        f"<td>{'Sim' if _admin_allowed(user.email) else 'Não'}</td>"
        "</tr>"
        for user in page_result.items
    )
    body = f"""
      <article>
        <h3>Usuários</h3>
        {_users_alert(request)}
        <form class='grid' method='get' action='/users'>
          <input name='q' placeholder='e-mail' value='{html.escape(q)}'>
          <button type='submit'>Filtrar</button>
        </form>
        <table role='grid'>
          <thead><tr><th>E-mail</th><th>Verificado</th><th>Admin</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="3">Nenhum usuário.</td></tr>'}</tbody>
        </table>
        {_pager_html('/users', page_result, q=q)}
      </article>
    """
    return _layout(request, "Admin | Usuários", body, csrf_token=_csrf_value(request))


@app.post("/users/{email}/reset_password")
def reset_user_password(email: str, request: Request, csrf_token: str = Form(""), password: str = Form(...)):
    _csrf_protect(request, csrf_token)
    if len(password or "") < 8:
        return RedirectResponse("/users?error=pwd_curto", status_code=303)
    repo.update_user_password(email, hash_password(password))
    return RedirectResponse("/users?ok=pwd", status_code=303)


def _domain_rows(cards: list[object]) -> str:
    rows = []
    for card in cards:
        meta = card.custom_domain_meta or {}
        active = (meta.get("active_host") or "").strip()
        requested = (meta.get("requested_host") or "").strip()
        status = (meta.get("status") or "").lower()
        note = (meta.get("admin_note") or "").strip()
        rows.append(
            "<tr>"
            f"<td>{html.escape(card.uid)}</td>"
            f"<td>{html.escape(card.vanity or '')}</td>"
            f"<td>{html.escape(active)}</td>"
            f"<td>{html.escape(requested)}</td>"
            f"<td>{html.escape(status)}</td>"
            f"<td class='admin-domain-note'>{html.escape(note)}</td>"
            "</tr>"
        )
    return "".join(rows)


@app.get("/domains", response_class=HTMLResponse)
def list_domains(request: Request, q: str = "", page: int = 1):
    try:
        require_admin(request)
    except HTTPException:
        return _redirect_login("/domains")
    page_result = repo.list_cards_with_custom_domains(q=q, page=page, page_size=PAGE_SIZE)
    csrf_token = _csrf_value(request)
    body = f"""
      <article>
        <h3>Domínios personalizados</h3>
        {_domains_alert(request)}
        <form class='grid' method='get' action='/domains'>
          <input name='q' placeholder='uid, vanity ou host' value='{html.escape(q)}'>
          <button type='submit'>Filtrar</button>
        </form>
        <table role='grid'>
          <thead><tr><th>UID</th><th>Vanity</th><th>Ativo</th><th>Pendente</th><th>Status</th><th>Obs</th></tr></thead>
          <tbody>{_domain_rows(page_result.items) or '<tr><td colspan="6">Nenhum domínio.</td></tr>'}</tbody>
        </table>
        {_pager_html('/domains', page_result, q=q)}
      </article>
      <article>
        <h4>Revisão</h4>
        <p class='admin-compact'>Aprovar ativa o host solicitado. Reprovar mantém o histórico. Desativar remove o host ativo do registro.</p>
        <form method='post' action='/domains/approve' class='grid'>
          <input type='hidden' name='csrf_token' value='{html.escape(csrf_token)}'>
          <label>UID <input name='uid' required></label>
          <label>Nota admin <input name='note'></label>
          <button type='submit'>Aprovar pendente</button>
        </form>
        <form method='post' action='/domains/reject' class='grid'>
          <input type='hidden' name='csrf_token' value='{html.escape(csrf_token)}'>
          <label>UID <input name='uid' required></label>
          <label>Nota admin <input name='note'></label>
          <button class='secondary' type='submit'>Reprovar</button>
        </form>
        <form method='post' action='/domains/disable' class='grid'>
          <input type='hidden' name='csrf_token' value='{html.escape(csrf_token)}'>
          <label>UID <input name='uid' required></label>
          <label>Nota admin <input name='note'></label>
          <button class='secondary' type='submit'>Desativar</button>
        </form>
      </article>
    """
    return _layout(request, "Admin | Domínios", body, csrf_token=csrf_token)


def _update_domain(uid: str, fn):
    card = repo.get_card_by_uid(uid)
    if not card:
        return False
    meta = card.custom_domain_meta or {}
    repo.update_card_custom_domain_meta(uid, fn(meta))
    return True


@app.post("/domains/approve")
def domain_approve(request: Request, uid: str = Form(...), csrf_token: str = Form(""), note: str = Form("")):
    _csrf_protect(request, csrf_token)
    ok = _update_domain(
        uid,
        lambda meta: {
            **meta,
            "active_host": meta.get("requested_host") or meta.get("active_host") or "",
            "status": "active",
            "admin_note": note,
            "requested_host": meta.get("requested_host", ""),
            "updated_at": int(datetime.now(timezone.utc).timestamp()),
        },
    )
    card = repo.get_card_by_uid(uid)
    host = (card.custom_domain_meta or {}).get("active_host") if card else None
    if ok and host:
        repo.register_custom_domain(str(host).strip().lower(), uid)
    return RedirectResponse("/domains?ok=approved", status_code=303)


@app.post("/domains/reject")
def domain_reject(request: Request, uid: str = Form(...), csrf_token: str = Form(""), note: str = Form("")):
    _csrf_protect(request, csrf_token)
    _update_domain(
        uid,
        lambda meta: {
            **meta,
            "status": "rejected",
            "admin_note": note,
            "updated_at": int(datetime.now(timezone.utc).timestamp()),
        },
    )
    return RedirectResponse("/domains?ok=rejected", status_code=303)


@app.post("/domains/disable")
def domain_disable(request: Request, uid: str = Form(...), csrf_token: str = Form(""), note: str = Form("")):
    _csrf_protect(request, csrf_token)
    card = repo.get_card_by_uid(uid)
    meta = card.custom_domain_meta or {} if card else {}
    active_host = str(meta.get("active_host") or "").strip().lower()
    _update_domain(
        uid,
        lambda current: {
            **current,
            "status": "disabled",
            "active_host": "",
            "requested_host": "",
            "admin_note": note,
            "updated_at": int(datetime.now(timezone.utc).timestamp()),
        },
    )
    if active_host:
        repo.unregister_custom_domain(active_host)
    return RedirectResponse("/domains?ok=disabled", status_code=303)


def create_admin_app() -> FastAPI:
    """Factory compatível com uvicorn/gunicorn."""
    return app
