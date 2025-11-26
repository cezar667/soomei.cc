from __future__ import annotations

import html
import json
import os
import time
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.core.config import get_settings
from api.core.security import hash_password, verify_password
from api.domain.slugs import is_valid_slug
from api.repositories.sql_repository import SQLRepository


app = FastAPI(title="Soomei Admin API")
settings = get_settings()
repo = SQLRepository()

ADMIN_SESSION_TTL_SECONDS = max(600, int(os.getenv("ADMIN_SESSION_TTL_SECONDS", "43200") or 43200))
ADMIN_COOKIE_SECURE = settings.app_env == "prod" or (os.getenv("ADMIN_COOKIE_SECURE") or "").strip() == "1"
ADMIN_HOSTS = {h.strip() for h in (os.getenv("ADMIN_HOST", "") or "").split(",") if h.strip()}
ADMIN_HOSTS.update({"localhost:8001", "127.0.0.1:8001"})


# ---------------------- helpers ----------------------
def _admin_allowed(email: str) -> bool:
    allow = (os.getenv("ADMIN_EMAILS", "") or "").strip()
    if allow:
        allowed = {x.strip().lower() for x in allow.split(",") if x.strip()}
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
    sess = repo.get_admin_session(token)
    now = datetime.now(timezone.utc)
    if not sess or (sess.expires_at and sess.expires_at < now):
        if token:
            repo.delete_admin_session(token)
        return None
    return sess


def _csrf_protect(request: Request, form_token: str) -> None:
    if not _check_origin(request):
        raise HTTPException(403, "origem invalida")
    tok = request.cookies.get("admin_session")
    sess = _load_admin_session(tok)
    if not sess or sess.csrf_token != form_token:
        raise HTTPException(403, "csrf invalido")


def require_admin(request: Request) -> str:
    tok = request.cookies.get("admin_session")
    sess = _load_admin_session(tok)
    if not sess:
        raise HTTPException(401, "nao autenticado")
    email = sess.email
    user = repo.get_user(email)
    if not user or not user.email_verified_at:
        raise HTTPException(403, "email nao verificado")
    if not _admin_allowed(email):
        raise HTTPException(403, "forbidden")
    return email


def _csrf_value(request: Request) -> str:
    tok = request.cookies.get("admin_session")
    sess = _load_admin_session(tok)
    return sess.csrf_token if sess else ""


def _layout(title: str, body: str, csrf_token: str = "") -> HTMLResponse:
    return HTMLResponse(
        f"""
        <!doctype html><html lang='pt-br'><head>
        <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <link rel="stylesheet" href="https://unpkg.com/@picocss/pico@2.0.6/css/pico.min.css">
        <title>{html.escape(title)}</title>
        <style>table {{font-size:14px}} td,th {{white-space:nowrap}}</style>
        </head><body>
        <main class="container">
          <nav><ul><li><strong>Admin</strong></li></ul>
              <ul><li><a href="/">Dashboard</a></li><li><a href="/cards">Cartoes</a></li><li><a href="/domains">Dominios</a></li><li><a href="/users">Usuarios</a></li><li><a href="/logout">Sair</a></li></ul>
          </nav>
          {body}
        </main>
        </body></html>
        """,
        headers={"X-CSRF-Token": csrf_token} if csrf_token else None,
    )


# ---------------------- auth ----------------------
@app.get("/login", response_class=HTMLResponse)
def login_page(next: str = "/", error: str = ""):
    messages = {
        "credenciais": "Credenciais invalidas.",
        "nao_autorizado": "Usuario nao autorizado.",
        "nao_verificado": "E-mail nao verificado.",
    }
    msg = messages.get(error, "")
    return HTMLResponse(
        f"""
        <!doctype html><html lang='pt-br'><head>
        <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <link rel="stylesheet" href="https://unpkg.com/@picocss/pico@2.0.6/css/pico.min.css">
        <title>Admin | Login</title></head>
        <body><main class="container">
          <article>
            <h1>Admin | Login</h1>
            {('<mark role="alert">' + html.escape(msg) + '</mark>') if msg else ''}
            <form method='post' action='/login'>
              <input type='hidden' name='next' value='{html.escape(next)}'>
              <label>E-mail</label><input name='email' type='email' required>
              <label>Senha</label><input name='password' type='password' required>
              <button style='margin-top:12px'>Entrar</button>
            </form>
          </article>
        </main></body></html>
        """
    )


@app.post("/login")
def do_login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/")):
    if not _check_origin(request):
        return RedirectResponse("/login?error=credenciais", status_code=303)
    user = repo.get_user(email)
    if not user or not verify_password(password, user.password_hash):
        return RedirectResponse("/login?error=credenciais", status_code=303)
    if not user.email_verified_at:
        return RedirectResponse("/login?error=nao_verificado", status_code=303)
    if not _admin_allowed(email):
        return RedirectResponse("/login?error=nao_autorizado", status_code=303)
    tok, csrf_token = _issue_admin_session(email)
    resp = RedirectResponse(next or "/", status_code=303)
    resp.set_cookie(
        "admin_session",
        value=tok,
        httponly=True,
        samesite="strict",
        secure=ADMIN_COOKIE_SECURE,
        max_age=ADMIN_SESSION_TTL_SECONDS,
        path="/",
    )
    # expor csrf no header para formularios
    resp.headers["X-CSRF-Token"] = csrf_token
    return resp


@app.get("/logout")
def logout(request: Request):
    tok = request.cookies.get("admin_session")
    if tok:
        repo.delete_admin_session(tok)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("admin_session", path="/")
    return resp


# ---------------------- dashboard ----------------------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    try:
        require_admin(request)
    except HTTPException:
        return RedirectResponse("/login?next=/", status_code=303)
    cards = repo.list_cards()
    total = len(cards)
    status_counts = {"active": 0, "pending": 0, "blocked": 0}
    for c in cards:
        status_counts[(c.status or "").lower()] = status_counts.get((c.status or "").lower(), 0) + 1
    top_views = sorted(((c.vanity or c.uid, int(c.metrics_views or 0)) for c in cards if c.metrics_views), key=lambda x: x[1], reverse=True)[:5]
    status_chart = json.dumps({"labels": ["Ativos", "Pendentes", "Bloqueados"], "values": [status_counts.get("active", 0), status_counts.get("pending", 0), status_counts.get("blocked", 0)]})
    views_chart = json.dumps({"labels": [label for label, _ in top_views] or ["Sem dados"], "values": [v for _, v in top_views] or [0]})
    body = f"""
      <article>
        <h3>Resumo</h3>
        <ul>
          <li>Total de cartoes: <b>{total}</b></li>
          <li>Ativos: <b>{status_counts.get('active',0)}</b> | Pendentes: <b>{status_counts.get('pending',0)}</b> | Bloqueados: <b>{status_counts.get('blocked',0)}</b></li>
        </ul>
        <p><a href='/cards' role='button'>Cartoes</a> <a href='/domains' role='button' class='secondary'>Dominios</a> <a href='/users' role='button' class='secondary'>Usuarios</a></p>
      </article>
      <article>
        <h3>Metricas</h3>
        <div class='grid'>
          <figure><figcaption>Por status</figcaption><canvas id='statusChart' width='320' height='220'></canvas></figure>
          <figure><figcaption>Top views</figcaption><canvas id='viewsChart' width='320' height='220'></canvas></figure>
        </div>
      </article>
      <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.6/dist/chart.umd.min.js" crossorigin="anonymous"></script>
      <script>
        (function(){{
          var s = {status_chart};
          var v = {views_chart};
          if (window.Chart) {{
            new Chart(document.getElementById('statusChart').getContext('2d'), {{
              type:'doughnut', data: {{labels:s.labels, datasets:[{{data:s.values, backgroundColor:['#4ade80','#fbbf24','#f87171']}}]}}
            }});
            new Chart(document.getElementById('viewsChart').getContext('2d'), {{
              type:'bar', data: {{labels:v.labels, datasets:[{{data:v.values, backgroundColor:'#60a5fa'}}]}}
            }});
          }}
        }})();
      </script>
    """
    return _layout("Admin | Dashboard", body, csrf_token=_csrf_value(request))


# ---------------------- cards ----------------------
def _card_row(c, csrf_token: str) -> str:
    status = (c.status or "").lower()
    owner = html.escape(c.owner_email or "")
    actions = []
    details_link = f"<a href='/cards/{html.escape(c.uid)}' class='secondary' style='margin:0 4px'>Detalhes</a>"
    if status == "blocked":
        actions.append(
            f"<form method='post' action='/cards/{html.escape(c.uid)}/unblock' style='display:inline'>"
            f"<input type='hidden' name='csrf_token' value='{csrf_token}'>"
            "<button class='secondary' style='margin:0 4px'>Desbloquear</button>"
            "</form>"
        )
    else:
        actions.append(
            f"<form method='post' action='/cards/{html.escape(c.uid)}/block' style='display:inline'>"
            f"<input type='hidden' name='csrf_token' value='{csrf_token}'>"
            "<button class='secondary' style='margin:0 4px'>Bloquear</button>"
            "</form>"
        )
    actions.append(
        f"<form method='post' action='/cards/{html.escape(c.uid)}/reset' style='display:inline'>"
        f"<input type='hidden' name='csrf_token' value='{csrf_token}'>"
        "<button class='secondary' style='margin:0 4px'>Resetar</button>"
        "</form>"
    )
    actions.append(
        f"<form method='post' action='/cards/{html.escape(c.uid)}/delete' style='display:inline' onsubmit=\"return confirm('Excluir este cartao? Esta acao nao pode ser desfeita.');\">"
        f"<input type='hidden' name='csrf_token' value='{csrf_token}'>"
        "<button class='secondary' style='margin:0 4px'>Excluir</button>"
        "</form>"
    )
    actions_html = "".join(actions)
    return (
        f"<tr>"
        f"<td><code>{html.escape(c.uid)}</code></td>"
        f"<td>{html.escape(c.vanity or '')}</td>"
        f"<td>{owner}</td>"
        f"<td>{html.escape(status)}</td>"
        f"<td>{int(c.metrics_views or 0)}</td>"
        f"<td>{details_link}{actions_html}</td>"
        f"</tr>"
    )


@app.get("/cards", response_class=HTMLResponse)
def list_cards(request: Request, q: str = "", status: str = ""):
    try:
        require_admin(request)
    except HTTPException:
        return RedirectResponse("/login?next=/cards", status_code=303)
    cards = repo.list_cards()
    qnorm = (q or "").strip().lower()
    status_norm = (status or "").strip().lower()
    filtered = []
    for c in cards:
        if status_norm and (c.status or "").lower() != status_norm:
            continue
        if qnorm and qnorm not in (c.uid or "").lower() and qnorm not in (c.vanity or "").lower() and qnorm not in (c.owner_email or "").lower():
            continue
        filtered.append(c)
    csrf_token = _csrf_value(request)
    rows = "\n".join(_card_row(c, csrf_token) for c in filtered)
    ok = (request.query_params.get("ok") or "").strip()
    pin_msg = ""
    if ok == "reset":
        pin_msg = (request.query_params.get("pin") or "").strip()
    alert = ""
    if ok == "created":
        alert = "<mark role='status' style='display:block'>Cartao criado.</mark>"
    elif ok == "blocked":
        alert = "<mark role='status' style='display:block'>Cartao bloqueado.</mark>"
    elif ok == "unblocked":
        alert = "<mark role='status' style='display:block'>Cartao desbloqueado.</mark>"
    elif ok == "reset":
        alert = f"<mark role='status' style='display:block'>Cartao resetado. Novo PIN: <strong>{html.escape(pin_msg)}</strong></mark>"
    elif request.query_params.get("error"):
        alert = f"<mark role='alert' style='display:block'>{html.escape(request.query_params.get('error',''))}</mark>"
    body = f"""
    <article>
      <h3>Cartoes</h3>
      {alert}
      <form class='grid' method='get' action='/cards'>
        <input name='q' placeholder='uid, vanity ou email' value='{html.escape(q)}'>
        <select name='status'>
          <option value=''>Status</option>
          <option value='active' {'selected' if status_norm=='active' else ''}>Ativo</option>
          <option value='pending' {'selected' if status_norm=='pending' else ''}>Pending</option>
          <option value='blocked' {'selected' if status_norm=='blocked' else ''}>Blocked</option>
        </select>
        <button>Filtrar</button>
      </form>
      <table role='grid'>
        <thead><tr><th>UID</th><th>Vanity</th><th>Dono</th><th>Status</th><th>Views</th><th>Ações</th></tr></thead>
        <tbody>{rows or '<tr><td colspan=\"6\">Nenhum registro</td></tr>'}</tbody>
      </table>
    </article>
    <article>
      <h4>Criar cartao</h4>
      <form method='post' action='/cards/create'>
        <input type='hidden' name='csrf_token' value='{csrf_token}'>
        <label>UID <input name='uid' required></label>
        <label>PIN <input name='pin' required></label>
        <label>Vanity (opcional) <input name='vanity'></label>
        <label>Dono (email) <input name='user'></label>
        <button>Criar</button>
      </form>
    </article>
    """
    return _layout("Admin | Cartoes", body, csrf_token=csrf_token)


@app.get("/cards/{uid}", response_class=HTMLResponse)
def card_details(uid: str, request: Request):
    try:
        require_admin(request)
    except HTTPException:
        return RedirectResponse(f"/login?next=/cards/{uid}", status_code=303)
    card = repo.get_card_by_uid(uid)
    if not card:
        return RedirectResponse("/cards?error=nao_encontrado", status_code=303)
    owner = card.owner_email or ""
    profile = repo.get_profile(owner) or {}
    meta = card.custom_domain_meta or {}
    body = f"""
    <article>
      <h3>Detalhes do cartao</h3>
      <p><strong>UID:</strong> {html.escape(card.uid)}</p>
      <p><strong>Vanity:</strong> {html.escape(card.vanity or '')}</p>
      <p><strong>Status:</strong> {html.escape(card.status or '')}</p>
      <p><strong>PIN:</strong> {html.escape(card.pin)}</p>
      <p><strong>Dono:</strong> {html.escape(owner)}</p>
      <p><strong>Billing:</strong> {html.escape(card.billing_status or '')}</p>
      <p><strong>Views:</strong> {int(card.metrics_views or 0)}</p>
      <p><strong>Domínio custom:</strong> {html.escape((meta.get('active_host') or '') or (meta.get('requested_host') or ''))} (status: {html.escape((meta.get('status') or '').lower())})</p>
    </article>
    <article>
      <h4>Perfil</h4>
      <pre style="white-space:pre-wrap">{html.escape(json.dumps(profile, ensure_ascii=False, indent=2))}</pre>
    </article>
    <p><a class='secondary' href='/cards'>Voltar</a> <a class='secondary' target='_blank' href='/{html.escape(card.vanity or card.uid)}'>Ver público</a></p>
    """
    return _layout(f"Admin | Cartao {html.escape(uid)}", body, csrf_token=_csrf_value(request))


@app.post("/cards/create")
def create_card(request: Request, uid: str = Form(...), pin: str = Form(...), user: str = Form(""), vanity: str = Form(""), csrf_token: str = Form("")):
    _csrf_protect(request, csrf_token)
    uid_value = (uid or "").strip()
    pin_value = (pin or "").strip()
    vanity_value = (vanity or "").strip()
    owner = (user or "").strip() or None
    if not uid_value or not pin_value:
        return RedirectResponse("/cards?error=uid_pin", status_code=303)
    if repo.get_card_by_uid(uid_value):
        return RedirectResponse("/cards?error=uid_existente", status_code=303)
    if vanity_value:
        if not is_valid_slug(vanity_value) or repo.slug_exists(vanity_value):
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


def _cleanup_user_if_orphan(email: str, keep_uid: str) -> None:
    others = [c for c in repo.get_cards_by_owner(email) if c.uid != keep_uid]
    if others:
        return
    repo.delete_profile(email)
    repo.delete_user_sessions(email)
    repo.delete_verify_tokens_for_email(email)
    repo.delete_reset_tokens_for_email(email)
    repo.delete_user(email)


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
    return RedirectResponse(f"/cards?ok=reset&pin={pin_value}", status_code=303)


@app.post("/cards/{uid}/delete")
def delete_card(uid: str, request: Request, csrf_token: str = Form("")):
    _csrf_protect(request, csrf_token)
    card = repo.get_card_by_uid(uid)
    if card and card.owner_email:
        _cleanup_user_if_orphan(card.owner_email, uid)
    repo.delete_card(uid)
    return RedirectResponse("/cards?ok=deleted", status_code=303)


# ---------------------- usuarios ----------------------
@app.get("/users", response_class=HTMLResponse)
def list_users(request: Request):
    try:
        require_admin(request)
    except HTTPException:
        return RedirectResponse("/login?next=/users", status_code=303)
    users = repo.list_users()
    rows = []
    for u in users:
        rows.append(
            f"<tr><td>{html.escape(u.email)}</td><td>{'Sim' if u.email_verified_at else 'Nao'}</td><td>{'Sim' if _admin_allowed(u.email) else 'Nao'}</td></tr>"
        )
    body = f"""
    <article>
      <h3>Usuarios</h3>
      <table role='grid'>
        <thead><tr><th>Email</th><th>Verificado</th><th>Admin</th></tr></thead>
        <tbody>{''.join(rows) or '<tr><td colspan=\"3\">Nenhum usuario</td></tr>'}</tbody>
      </table>
    </article>
    """
    return _layout("Admin | Usuarios", body, csrf_token=_csrf_value(request))


@app.post("/users/{email}/reset_password")
def reset_user_password(email: str, request: Request, csrf_token: str = Form(""), password: str = Form(...)):
    _csrf_protect(request, csrf_token)
    if len(password or "") < 8:
        return RedirectResponse("/users?error=pwd_curto", status_code=303)
    repo.update_user_password(email, hash_password(password))
    return RedirectResponse("/users?ok=pwd", status_code=303)


# ---------------------- dominios ----------------------
def _domain_rows() -> str:
    rows = []
    for card in repo.list_cards():
        meta = card.custom_domain_meta or {}
        active = (meta.get("active_host") or "").strip()
        requested = (meta.get("requested_host") or "").strip()
        status = (meta.get("status") or "").lower()
        if not (active or requested or status):
            continue
        rows.append(
            f"<tr><td>{html.escape(card.uid)}</td><td>{html.escape(card.vanity or '')}</td><td>{html.escape(active)}</td><td>{html.escape(requested)}</td><td>{html.escape(status)}</td><td>{html.escape(meta.get('admin_note',''))}</td></tr>"
        )
    return "".join(rows)


@app.get("/domains", response_class=HTMLResponse)
def list_domains(request: Request):
    try:
        require_admin(request)
    except HTTPException:
        return RedirectResponse("/login?next=/domains", status_code=303)
    csrf_token = _csrf_value(request)
    body = f"""
    <article>
      <h3>Dominios personalizados</h3>
      <table role='grid'>
        <thead><tr><th>UID</th><th>Vanity</th><th>Ativo</th><th>Pendente</th><th>Status</th><th>Obs</th></tr></thead>
        <tbody>{_domain_rows() or '<tr><td colspan=\"6\">Nenhum dominio</td></tr>'}</tbody>
      </table>
      <details>
        <summary>Aprovar/Reprovar/Desativar</summary>
        <form method='post' action='/domains/approve' class='grid'>
          <input type='hidden' name='csrf_token' value='{csrf_token}'>
          <label>UID <input name='uid' required></label>
          <label>Nota admin <input name='note'></label>
          <button>Aprovar pendente</button>
        </form>
        <form method='post' action='/domains/reject' class='grid'>
          <input type='hidden' name='csrf_token' value='{csrf_token}'>
          <label>UID <input name='uid' required></label>
          <label>Nota admin <input name='note'></label>
          <button class='secondary'>Reprovar</button>
        </form>
        <form method='post' action='/domains/disable' class='grid'>
          <input type='hidden' name='csrf_token' value='{csrf_token}'>
          <label>UID <input name='uid' required></label>
          <label>Nota admin <input name='note'></label>
          <button class='secondary'>Desativar</button>
        </form>
      </details>
    </article>
    """
    return _layout("Admin | Dominios", body, csrf_token=csrf_token)


def _update_domain(uid: str, fn):
    card = repo.get_card_by_uid(uid)
    if not card:
        return False
    meta = card.custom_domain_meta or {}
    updated_meta = fn(meta)
    repo.update_card_custom_domain_meta(uid, updated_meta)
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
            "updated_at": int(time.time()),
            "requested_host": meta.get("requested_host", ""),
        },
    )
    card = repo.get_card_by_uid(uid)
    host = (card.custom_domain_meta or {}).get("active_host") if card else None
    if ok and host:
        repo.register_custom_domain(host, uid)
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
            "updated_at": int(time.time()),
        },
    )
    return RedirectResponse("/domains?ok=rejected", status_code=303)


@app.post("/domains/disable")
def domain_disable(request: Request, uid: str = Form(...), csrf_token: str = Form(""), note: str = Form("")):
    _csrf_protect(request, csrf_token)
    card = repo.get_card_by_uid(uid)
    meta = card.custom_domain_meta or {} if card else {}
    active_host = meta.get("active_host")
    _update_domain(
        uid,
        lambda meta: {
            **meta,
            "status": "disabled",
            "active_host": "",
            "requested_host": "",
            "admin_note": note,
            "updated_at": int(time.time()),
        },
    )
    if active_host:
        repo.unregister_custom_domain(active_host)
    return RedirectResponse("/domains?ok=disabled", status_code=303)


# ---------------------- misc ----------------------
@app.post("/cards/{uid}/assign")
def assign_owner(uid: str, request: Request, email: str = Form(""), csrf_token: str = Form("")):
    _csrf_protect(request, csrf_token)
    user = repo.get_user(email)
    if not user:
        return RedirectResponse("/cards?error=user_nao_encontrado", status_code=303)
    repo.assign_card_owner(uid, email, status="active", billing_status="ok")
    return RedirectResponse("/cards?ok=assigned", status_code=303)


def create_admin_app() -> FastAPI:
    """Factory compatível com uvicorn/gunicorn."""
    return app
