from __future__ import annotations

import html
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, or_, select

from api.core import csrf
from api.core.config import get_settings
from api.core.http_security import SecurityHeadersMiddleware
from api.core.rate_limiter import rate_limit_ip
from api.core.security import hash_password, verify_password
from api.db import models
from api.db.session import get_session
from api.domain.slugs import is_valid_slug
from api.integrations.membership_platform.service import MembershipWebhookService
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


def _login_csrf_protect(request: Request, form_token: str) -> None:
    """
    Valida o CSRF da tela de login do admin sem considerar o cookie público `session`.

    Cookies não diferenciam porta; por isso, ao acessar localhost:8001, o navegador
    também pode enviar o cookie `session` criado pela app pública em localhost:8000.
    O helper compartilhado de CSRF troca a expectativa para um token derivado dessa
    sessão pública, o que quebra o login do admin. Antes de existir `admin_session`,
    o contrato correto é comparar o campo oculto com o cookie `csrf_token`.
    """
    if not _check_origin(request):
        raise HTTPException(403, "origem invalida")
    supplied = (form_token or "").strip()
    cookie_token = (request.cookies.get(csrf.CSRF_COOKIE_NAME) or "").strip()
    if not supplied:
        raise HTTPException(403, "csrf ausente")
    if not cookie_token:
        raise HTTPException(403, "cookie csrf ausente")
    if not secrets.compare_digest(cookie_token, supplied):
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


def _status_badge(value: str) -> str:
    status = (value or "").strip().lower()
    labels = {
        "active": "Ativo",
        "pending": "Pendente",
        "blocked": "Bloqueado",
        "rejected": "Reprovado",
        "disabled": "Desativado",
        "pending_validation": "Em validação",
        "qualified": "Qualificada",
        "disqualified": "Desqualificada",
    }
    label = labels.get(status, status or "—")
    klass = status if status in labels else "neutral"
    return f"<span class='admin-badge admin-badge--{html.escape(klass)}'>{html.escape(label)}</span>"


def _boolean_badge(value: bool) -> str:
    return (
        "<span class='admin-badge admin-badge--active'>Sim</span>"
        if value
        else "<span class='admin-badge admin-badge--neutral'>Não</span>"
    )


def _webhook_status_badge(value: str) -> str:
    status = (value or "").strip().upper()
    classes = {
        "PROCESSED": "active",
        "RECEIVED": "pending",
        "PROCESSING": "pending",
        "RETRY_PENDING": "pending",
        "IGNORED": "neutral",
        "FAILED": "blocked",
        "DEAD_LETTER": "blocked",
    }
    labels = {
        "PROCESSED": "Processado",
        "RECEIVED": "Recebido",
        "PROCESSING": "Processando",
        "RETRY_PENDING": "Retry pendente",
        "IGNORED": "Ignorado",
        "FAILED": "Falhou",
        "DEAD_LETTER": "Fila morta",
    }
    klass = classes.get(status, "neutral")
    label = labels.get(status, status or "—")
    return f"<span class='admin-badge admin-badge--{klass}'>{html.escape(label)}</span>"


def _subscription_status_badge(value: str) -> str:
    status = (value or "").strip().upper()
    klass = {
        "ACTIVE": "active",
        "PENDING": "pending",
        "OVERDUE": "pending",
        "SUSPENDED": "blocked",
        "CANCELLED": "blocked",
        "REFUNDED": "blocked",
    }.get(status, "neutral")
    return f"<span class='admin-badge admin-badge--{klass}'>{html.escape(status or '—')}</span>"


def _dt(value) -> str:
    if not value:
        return "—"
    try:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return html.escape(str(value))


def _json_pretty(value: object) -> str:
    return html.escape(json.dumps(value or {}, ensure_ascii=False, indent=2, default=str))


def _nav_link(path: str, label: str, current_path: str) -> str:
    active_match = current_path == path if path == "/" else current_path == path or current_path.startswith(path + "/")
    active = " is-active" if active_match else ""
    return f"<a class='admin-nav-link{active}' href='{html.escape(path)}'>{html.escape(label)}</a>"


def _layout(request: Request | None, title: str, body: str, *, csrf_token: str = "") -> HTMLResponse:
    current_path = "/login"
    if request is not None:
        current_path = getattr(getattr(request, "url", None), "path", "") or "/"
    logout_html = ""
    if csrf_token:
        logout_html = (
            "<form method='post' action='/logout' class='admin-logout'>"
            f"<input type='hidden' name='csrf_token' value='{html.escape(csrf_token)}'>"
            "<button type='submit' class='admin-logout-btn'>Sair</button>"
            "</form>"
        )
    is_login = current_path == "/login"
    body_class = "admin-body admin-body--login" if is_login else "admin-body"
    nav_html = "" if is_login else (
        "<nav class='admin-nav'>"
        "<a class='admin-brand' href='/'>"
        "<span class='admin-brand__mark'>S</span>"
        "<span><strong>Soomei Admin</strong><small>Gestão de cartões digitais</small></span>"
        "</a>"
        "<div class='admin-nav__links'>"
        f"{_nav_link('/', 'Dashboard', current_path)}"
        f"{_nav_link('/cards', 'Cartões', current_path)}"
        f"{_nav_link('/webhooks', 'Webhooks', current_path)}"
        f"{_nav_link('/referrals', 'Indicações', current_path)}"
        f"{_nav_link('/domains', 'Domínios', current_path)}"
        f"{_nav_link('/users', 'Usuários', current_path)}"
        f"{logout_html}"
        "</div>"
        "</nav>"
    )
    return HTMLResponse(
        f"""
        <!doctype html><html lang='pt-br'><head>
        <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <link rel="stylesheet" href="https://unpkg.com/@picocss/pico@2.0.6/css/pico.min.css">
        <title>{html.escape(title)}</title>
        <style>
          :root {{
            --admin-bg:#08090c;
            --admin-panel:#111318;
            --admin-panel-2:#171a21;
            --admin-border:rgba(255,255,255,.105);
            --admin-muted:#929aa7;
            --admin-text:#f3f5f8;
            --admin-blue:#8ab4f8;
            --admin-gold:#ffbf7a;
            --admin-green:#54e0ad;
            --admin-red:#ff8d8d;
            --admin-radius:22px;
          }}
          * {{box-sizing:border-box}}
          html {{background:var(--admin-bg)}}
          .admin-body {{
            min-height:100vh;
            margin:0;
            color:var(--admin-text);
            background:
              radial-gradient(circle at 18% -10%,rgba(138,180,248,.18),transparent 34%),
              radial-gradient(circle at 88% 0%,rgba(255,191,122,.12),transparent 30%),
              linear-gradient(180deg,#08090c,#0b0d12 45%,#08090c);
          }}
          .admin-body::before {{
            content:"";
            position:fixed;
            inset:0;
            pointer-events:none;
            opacity:.34;
            background-image:linear-gradient(135deg,rgba(255,255,255,.04) 25%,transparent 25%,transparent 50%,rgba(255,255,255,.04) 50%,rgba(255,255,255,.04) 75%,transparent 75%,transparent);
            background-size:18px 18px;
            mask-image:linear-gradient(180deg,#000,transparent 70%);
          }}
          .admin-shell {{
            position:relative;
            z-index:1;
            max-width:1240px;
            padding:26px 18px 56px;
          }}
          .admin-nav {{
            position:sticky;
            top:14px;
            z-index:10;
            display:flex;
            justify-content:space-between;
            align-items:center;
            gap:18px;
            margin-bottom:26px;
            padding:12px;
            border:1px solid var(--admin-border);
            border-radius:26px;
            background:rgba(14,16,21,.82);
            backdrop-filter:blur(18px);
            box-shadow:0 20px 70px rgba(0,0,0,.34),inset 0 1px 0 rgba(255,255,255,.06);
          }}
          .admin-brand {{
            display:inline-flex;
            align-items:center;
            gap:12px;
            color:var(--admin-text);
            text-decoration:none;
            min-width:max-content;
          }}
          .admin-brand__mark {{
            display:grid;
            place-items:center;
            width:42px;
            height:42px;
            border-radius:15px;
            color:#0b0d12;
            background:linear-gradient(135deg,#fff1d8,#ffbf7a);
            font-weight:950;
            box-shadow:0 12px 34px rgba(255,191,122,.22),inset 0 1px 0 rgba(255,255,255,.8);
          }}
          .admin-brand strong {{display:block;font-size:15px;letter-spacing:-.01em}}
          .admin-brand small {{display:block;color:var(--admin-muted);font-size:11px;line-height:1.2}}
          .admin-nav__links {{
            display:flex;
            gap:8px;
            flex-wrap:wrap;
            align-items:center;
            justify-content:flex-end;
          }}
          .admin-nav-link,.admin-logout-btn,.admin-pager a.secondary {{
            display:inline-flex;
            align-items:center;
            justify-content:center;
            min-height:38px;
            margin:0;
            padding:9px 13px;
            border:1px solid rgba(255,255,255,.09);
            border-radius:999px;
            background:rgba(255,255,255,.045);
            color:#c7ced8;
            text-decoration:none;
            font-size:13px;
            font-weight:800;
            line-height:1;
            transition:transform .18s ease,border-color .18s ease,background .18s ease,color .18s ease,box-shadow .18s ease;
          }}
          .admin-nav-link:hover,.admin-logout-btn:hover,.admin-pager a.secondary:hover {{
            transform:translateY(-1px);
            color:#fff;
            border-color:rgba(138,180,248,.32);
            background:rgba(138,180,248,.12);
            text-decoration:none;
          }}
          .admin-nav-link.is-active {{
            color:#081018;
            border-color:transparent;
            background:linear-gradient(135deg,#ffffff,#dfe8ff);
            box-shadow:0 12px 28px rgba(138,180,248,.2);
          }}
          .admin-logout {{margin:0}}
          .admin-logout-btn {{
            color:#ffcac3;
            border-color:rgba(255,141,141,.18);
            background:rgba(255,141,141,.08);
          }}
          h1,h2,h3,h4 {{letter-spacing:-.03em;color:var(--admin-text)}}
          article {{
            border:1px solid var(--admin-border);
            border-radius:var(--admin-radius);
            background:
              radial-gradient(circle at 100% 0%,rgba(255,255,255,.06),transparent 34%),
              linear-gradient(180deg,rgba(255,255,255,.07),rgba(255,255,255,.028));
            box-shadow:0 18px 54px rgba(0,0,0,.28),inset 0 1px 0 rgba(255,255,255,.055);
            overflow:hidden;
          }}
          article h3,article h4 {{margin-top:0}}
          .admin-summary {{
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
            gap:14px;
            margin-bottom:18px;
          }}
          .admin-summary article {{
            position:relative;
            min-height:132px;
            margin:0;
            padding:20px;
          }}
          .admin-summary article::after {{
            content:"";
            position:absolute;
            right:18px;
            bottom:18px;
            width:42px;
            height:42px;
            border-radius:16px;
            background:linear-gradient(135deg,rgba(138,180,248,.2),rgba(255,191,122,.12));
            box-shadow:inset 0 1px 0 rgba(255,255,255,.1);
          }}
          .admin-summary header {{
            margin:0 0 12px;
            color:var(--admin-muted);
            font-size:12px;
            font-weight:900;
            text-transform:uppercase;
            letter-spacing:.14em;
          }}
          .admin-summary strong {{
            display:block;
            color:#fff;
            font-size:clamp(34px,6vw,48px);
            line-height:.9;
            letter-spacing:-.06em;
          }}
          form.grid {{
            align-items:end;
            gap:12px;
          }}
          label {{color:#dce2ea;font-weight:750}}
          input,select,textarea {{
            min-height:46px;
            border:1px solid rgba(255,255,255,.12)!important;
            border-radius:14px!important;
            background:rgba(5,6,8,.64)!important;
            color:#f6f8fb!important;
            box-shadow:inset 0 1px 0 rgba(255,255,255,.035)!important;
          }}
          input:focus,select:focus,textarea:focus {{
            border-color:rgba(138,180,248,.72)!important;
            box-shadow:0 0 0 4px rgba(138,180,248,.12),inset 0 1px 0 rgba(255,255,255,.05)!important;
          }}
          button,[role=button],a.secondary {{
            border-radius:999px!important;
            font-weight:850!important;
          }}
          button[type=submit]:not(.secondary):not(.admin-logout-btn) {{
            border:0!important;
            background:linear-gradient(135deg,#ffffff,#dfe8ff)!important;
            color:#081018!important;
            box-shadow:0 12px 30px rgba(138,180,248,.2)!important;
          }}
          .secondary {{
            border-color:rgba(255,255,255,.11)!important;
            background:rgba(255,255,255,.055)!important;
            color:#dce2ea!important;
          }}
          .admin-table-wrap, table[role=grid] {{
            border-radius:18px;
          }}
          table {{
            width:100%;
            overflow:hidden;
            border:1px solid rgba(255,255,255,.08);
            border-radius:18px;
            background:rgba(4,5,7,.38);
            font-size:14px;
          }}
          thead th {{
            color:#aeb7c4;
            background:rgba(255,255,255,.055);
            font-size:11px;
            text-transform:uppercase;
            letter-spacing:.12em;
          }}
          td, th {{white-space:nowrap;border-color:rgba(255,255,255,.07)!important}}
          tbody tr:hover {{background:rgba(138,180,248,.055)}}
          code {{
            border-radius:9px;
            background:rgba(138,180,248,.09);
            color:#b9d2ff;
          }}
          pre {{
            border:1px solid rgba(255,255,255,.09);
            border-radius:18px;
            background:rgba(4,5,7,.62);
            color:#cdd4de;
          }}
          .admin-badge {{
            display:inline-flex;
            align-items:center;
            justify-content:center;
            min-height:26px;
            padding:5px 9px;
            border-radius:999px;
            border:1px solid rgba(255,255,255,.1);
            background:rgba(255,255,255,.06);
            color:#dce2ea;
            font-size:11px;
            font-weight:900;
            letter-spacing:.04em;
          }}
          .admin-badge--active {{border-color:rgba(84,224,173,.22);background:rgba(84,224,173,.1);color:#96f3c9}}
          .admin-badge--pending {{border-color:rgba(255,191,122,.24);background:rgba(255,191,122,.1);color:#ffd2a3}}
          .admin-badge--pending_validation {{border-color:rgba(128,203,255,.24);background:rgba(128,203,255,.1);color:#b8ddff}}
          .admin-badge--blocked,.admin-badge--rejected,.admin-badge--disqualified {{border-color:rgba(255,141,141,.24);background:rgba(255,141,141,.1);color:#ffb7b7}}
          .admin-badge--disabled,.admin-badge--neutral {{border-color:rgba(255,255,255,.1);background:rgba(255,255,255,.055);color:#aeb7c4}}
          .admin-pager {{
            display:flex;
            align-items:center;
            justify-content:flex-end;
            gap:10px;
            margin-top:16px;
          }}
          .admin-pager__status {{
            color:var(--admin-muted);
            font-size:13px;
            font-weight:750;
          }}
          .admin-flash {{
            display:block;
            margin:0 0 14px;
            padding:12px 14px;
            border-radius:15px;
            border:1px solid rgba(255,255,255,.1);
            font-weight:750;
          }}
          .admin-flash--ok {{background:rgba(84,224,173,.1);border-color:rgba(84,224,173,.22);color:#9af3c9}}
          .admin-flash--error {{background:rgba(255,141,141,.1);border-color:rgba(255,141,141,.22);color:#ffc1c1}}
          .admin-inline-form {{
            display:inline-flex;
            gap:6px;
            align-items:center;
            margin:0 5px 6px 0;
          }}
          .admin-inline-form button, .admin-inline-form a {{
            min-height:34px;
            margin:0;
            padding:8px 10px;
            font-size:12px;
          }}
          .admin-compact {{font-size:13px;color:var(--admin-muted);line-height:1.45}}
          .admin-domain-note {{white-space:normal;min-width:220px;color:#c7ced8}}
          .admin-grid-2 {{
            display:grid;
            grid-template-columns:minmax(0,1.25fr) minmax(280px,.75fr);
            gap:18px;
            align-items:start;
          }}
          .admin-detail-list {{
            display:grid;
            grid-template-columns:180px minmax(0,1fr);
            gap:8px 14px;
            margin:0;
          }}
          .admin-detail-list dt {{
            color:var(--admin-muted);
            font-size:12px;
            font-weight:900;
            text-transform:uppercase;
            letter-spacing:.12em;
          }}
          .admin-detail-list dd {{
            margin:0;
            min-width:0;
            color:#e6ebf2;
            word-break:break-word;
          }}
          .admin-code-block {{
            max-height:640px;
            overflow:auto;
            white-space:pre-wrap;
            word-break:break-word;
          }}
          .admin-filter-grid {{
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
            gap:12px;
            align-items:end;
          }}
          .admin-actions-row {{
            display:flex;
            gap:8px;
            align-items:center;
            flex-wrap:wrap;
          }}
          .admin-muted-cell {{
            max-width:300px;
            white-space:normal;
            color:#c1c8d2;
            line-height:1.35;
          }}
          .admin-dashboard-grid {{
            display:grid;
            grid-template-columns:repeat(auto-fit,minmax(320px,1fr));
            gap:18px;
            align-items:start;
            margin-bottom:18px;
          }}
          .admin-chart-card {{
            min-height:360px;
          }}
          .admin-chart-head {{
            display:flex;
            align-items:flex-start;
            justify-content:space-between;
            gap:14px;
            flex-wrap:wrap;
            margin-bottom:12px;
          }}
          .admin-chart-head h3 {{
            margin:0;
          }}
          .admin-kpi-row {{
            display:flex;
            gap:10px;
            flex-wrap:wrap;
            margin:10px 0 14px;
          }}
          .admin-kpi-pill {{
            display:inline-flex;
            flex-direction:column;
            gap:2px;
            min-width:118px;
            padding:10px 12px;
            border:1px solid rgba(255,255,255,.09);
            border-radius:16px;
            background:rgba(255,255,255,.045);
          }}
          .admin-kpi-pill small {{
            color:var(--admin-muted);
            font-size:10px;
            font-weight:900;
            text-transform:uppercase;
            letter-spacing:.12em;
          }}
          .admin-kpi-pill strong {{
            color:#fff;
            font-size:24px;
            line-height:1;
          }}
          .admin-line-chart {{
            width:100%;
            min-height:230px;
            border:1px solid rgba(255,255,255,.08);
            border-radius:18px;
            background:linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,.015));
            overflow:hidden;
          }}
          .admin-line-chart text {{
            fill:#8f98a6;
            font-size:11px;
            font-weight:700;
          }}
          .admin-login-shell {{
            min-height:calc(100vh - 52px);
            display:grid;
            place-items:center;
          }}
          .admin-login-card {{
            width:min(100%,460px);
            margin:0 auto;
            padding:30px;
            border-radius:30px;
            text-align:left;
          }}
          .admin-login-brand {{
            display:flex;
            align-items:center;
            gap:14px;
            margin-bottom:22px;
          }}
          .admin-login-brand .admin-brand__mark {{width:50px;height:50px;border-radius:18px}}
          .admin-login-brand strong {{display:block;font-size:18px}}
          .admin-login-brand small {{display:block;color:var(--admin-muted);font-size:12px}}
          .admin-login-card h1 {{
            margin:0;
            font-size:clamp(32px,7vw,44px);
            line-height:.95;
          }}
          .admin-login-card p {{
            color:#aeb7c4;
            line-height:1.5;
          }}
          .admin-login-card form {{margin-top:18px}}
          .admin-login-card button[type=submit] {{width:100%;min-height:52px;margin-top:8px}}
          .admin-login-footnote {{
            margin:16px 0 0;
            color:#727b89;
            font-size:12px;
            text-align:center;
          }}
          @media (max-width:760px) {{
            .admin-shell {{padding:14px 12px 38px}}
            .admin-nav {{position:relative;top:auto;align-items:flex-start;border-radius:22px}}
            .admin-brand {{width:100%}}
            .admin-nav__links {{width:100%;justify-content:flex-start}}
            .admin-nav-link,.admin-logout-btn {{flex:1 1 auto}}
            article {{border-radius:20px}}
            td,th {{white-space:normal}}
            table {{display:block;overflow-x:auto}}
            .admin-grid-2 {{grid-template-columns:1fr}}
            .admin-detail-list {{grid-template-columns:1fr}}
            .admin-login-card {{padding:24px 20px;border-radius:24px}}
          }}
        </style>
        </head><body class="{body_class}">
        <main class="container admin-shell">
          {nav_html}
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
      <section class='admin-login-shell'>
      <article class='admin-login-card'>
        <div class='admin-login-brand'>
          <span class='admin-brand__mark'>S</span>
          <span><strong>Soomei Admin</strong><small>Operação segura</small></span>
        </div>
        <h1>Entrar no painel</h1>
        <p>Gerencie cartões, usuários, domínios personalizados e operações sensíveis da plataforma.</p>
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
        <p class='admin-login-footnote'>Acesso restrito a contas verificadas e autorizadas.</p>
      </article>
      </section>
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
        f"<td>{_status_badge(status)}</td>"
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


def _dashboard_days(value: int) -> int:
    try:
        days = int(value or 30)
    except (TypeError, ValueError):
        return 30
    if days <= 7:
        return 7
    if days <= 30:
        return 30
    if days <= 90:
        return 90
    if days <= 180:
        return 180
    return 365


def _daily_series(model, date_column, *, days: int) -> list[tuple[datetime.date, int]]:
    today = datetime.now(timezone.utc).date()
    start_day = today - timedelta(days=days - 1)
    buckets = {start_day + timedelta(days=offset): 0 for offset in range(days)}
    with get_session() as session:
        rows = session.execute(select(date_column).select_from(model).where(date_column >= datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc))).scalars().all()
    for value in rows:
        if not value:
            continue
        try:
            day = value.astimezone(timezone.utc).date() if getattr(value, "tzinfo", None) else value.date()
        except Exception:
            continue
        if day in buckets:
            buckets[day] += 1
    return list(buckets.items())


def _sum_series(series: list[tuple[object, int]]) -> int:
    return sum(int(value or 0) for _, value in series)


def _line_chart_svg(series: list[tuple[datetime.date, int]], *, color: str, label: str) -> str:
    width = 720
    height = 250
    pad_left = 46
    pad_right = 18
    pad_top = 22
    pad_bottom = 42
    chart_w = width - pad_left - pad_right
    chart_h = height - pad_top - pad_bottom
    max_value = max([value for _, value in series] + [1])
    denom = max(1, len(series) - 1)
    points = []
    for index, (_day, value) in enumerate(series):
        x = pad_left + (chart_w * index / denom)
        y = pad_top + chart_h - (chart_h * int(value or 0) / max_value)
        points.append((x, y, int(value or 0)))
    point_attr = " ".join(f"{x:.2f},{y:.2f}" for x, y, _value in points)
    circles = "".join(
        f"<circle cx='{x:.2f}' cy='{y:.2f}' r='3.2'><title>{html.escape(str(day))}: {value}</title></circle>"
        for (day, _), (x, y, value) in zip(series, points)
        if value > 0 or len(series) <= 31
    )
    grid_lines = []
    for step in range(4):
        y = pad_top + chart_h * step / 3
        value = round(max_value - (max_value * step / 3))
        grid_lines.append(
            f"<line x1='{pad_left}' y1='{y:.2f}' x2='{width - pad_right}' y2='{y:.2f}' stroke='rgba(255,255,255,.08)'/>"
            f"<text x='8' y='{y + 4:.2f}'>{value}</text>"
        )
    first_label = series[0][0].strftime("%d/%m") if series else ""
    last_label = series[-1][0].strftime("%d/%m") if series else ""
    return (
        f"<svg class='admin-line-chart' viewBox='0 0 {width} {height}' role='img' aria-label='{html.escape(label)}' preserveAspectRatio='none'>"
        f"<defs><linearGradient id='chartFill{abs(hash(label))}' x1='0' x2='0' y1='0' y2='1'>"
        f"<stop offset='0%' stop-color='{html.escape(color)}' stop-opacity='.24'/>"
        f"<stop offset='100%' stop-color='{html.escape(color)}' stop-opacity='0'/></linearGradient></defs>"
        f"{''.join(grid_lines)}"
        f"<polygon points='{pad_left},{height - pad_bottom} {point_attr} {width - pad_right},{height - pad_bottom}' fill='url(#chartFill{abs(hash(label))})'/>"
        f"<polyline points='{point_attr}' fill='none' stroke='{html.escape(color)}' stroke-width='4' stroke-linecap='round' stroke-linejoin='round'/>"
        f"<g fill='{html.escape(color)}'>{circles}</g>"
        f"<text x='{pad_left}' y='{height - 14}'>{html.escape(first_label)}</text>"
        f"<text x='{width - pad_right - 44}' y='{height - 14}'>{html.escape(last_label)}</text>"
        f"</svg>"
    )


def _external_subscription_counts() -> dict[str, int]:
    with get_session() as session:
        rows = session.execute(
            select(models.ExternalSubscription.status, func.count(models.ExternalSubscription.id)).group_by(models.ExternalSubscription.status)
        ).all()
    return {str(status or "UNKNOWN").upper(): int(total or 0) for status, total in rows}


def _recent_webhook_failures(limit: int = 5) -> list:
    with get_session() as session:
        return session.execute(
            select(models.WebhookEvent)
            .where(models.WebhookEvent.status.in_(["FAILED", "DEAD_LETTER", "RETRY_PENDING"]))
            .order_by(models.WebhookEvent.received_at.desc())
            .limit(limit)
        ).scalars().all()


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
    try:
        _login_csrf_protect(request, csrf_token)
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
def dashboard(request: Request, days: int = 30):
    try:
        require_admin(request)
    except HTTPException:
        return _redirect_login("/")
    period_days = _dashboard_days(days)
    counts = repo.dashboard_card_counts()
    top_views = repo.top_cards_by_views(limit=5)
    card_series = _daily_series(models.Card, models.Card.created_at, days=period_days)
    webhook_series = _daily_series(models.WebhookEvent, models.WebhookEvent.received_at, days=period_days)
    cards_created_period = _sum_series(card_series)
    webhooks_period = _sum_series(webhook_series)
    subscriptions = _external_subscription_counts()
    active_subs = subscriptions.get("ACTIVE", 0)
    attention_subs = sum(subscriptions.get(status, 0) for status in ("OVERDUE", "SUSPENDED", "CANCELLED", "REFUNDED"))
    failure_rows = "".join(
        "<tr>"
        f"<td><a href='/webhooks/events/{html.escape(event.id)}'><code>{html.escape(event.external_event_id)}</code></a></td>"
        f"<td>{_webhook_status_badge(event.status or '')}</td>"
        f"<td>{html.escape(event.event_type or '')}</td>"
        f"<td class='admin-muted-cell'>{html.escape(event.error_message or event.error_code or '')}</td>"
        "</tr>"
        for event in _recent_webhook_failures(limit=5)
    )
    subscription_rows = "".join(
        "<tr>"
        f"<td>{_subscription_status_badge(status)}</td>"
        f"<td>{total}</td>"
        "</tr>"
        for status, total in sorted(subscriptions.items())
    )
    rows = "".join(
        f"<tr><td>{html.escape(label)}</td><td>{views}</td></tr>"
        for label, views in top_views
    )
    period_options = "".join(
        f"<option value='{value}' {'selected' if period_days == value else ''}>{value} dias</option>"
        for value in (7, 30, 90, 180, 365)
    )
    body = f"""
      <article>
        <div class='admin-chart-head'>
          <div>
            <h1>Dashboard operacional</h1>
            <p class='admin-compact'>Acompanhe crescimento dos cartões, atividade dos webhooks e saúde comercial da integração.</p>
          </div>
          <form method='get' action='/' class='admin-actions-row'>
            <label>Período
              <select name='days' onchange='this.form.submit()'>{period_options}</select>
            </label>
          </form>
        </div>
      </article>
      <section class='admin-summary'>
        <article><header>Total de cartões</header><strong>{counts.get('total', 0)}</strong></article>
        <article><header>Ativos</header><strong>{counts.get('active', 0)}</strong></article>
        <article><header>Pendentes</header><strong>{counts.get('pending', 0)}</strong></article>
        <article><header>Bloqueados</header><strong>{counts.get('blocked', 0)}</strong></article>
      </section>
      <section class='admin-dashboard-grid'>
        <article class='admin-chart-card'>
          <div class='admin-chart-head'>
            <div>
              <h3>Evolução de cartões</h3>
              <p class='admin-compact'>Cartões criados dia a dia no período selecionado.</p>
            </div>
          </div>
          <div class='admin-kpi-row'>
            <span class='admin-kpi-pill'><small>Criados no período</small><strong>{cards_created_period}</strong></span>
            <span class='admin-kpi-pill'><small>Último dia</small><strong>{card_series[-1][1] if card_series else 0}</strong></span>
          </div>
          {_line_chart_svg(card_series, color="#8ab4f8", label="Cartões criados por dia")}
        </article>
        <article class='admin-chart-card'>
          <div class='admin-chart-head'>
            <div>
              <h3>Volume de webhooks</h3>
              <p class='admin-compact'>Eventos recebidos pela integração no mesmo período.</p>
            </div>
          </div>
          <div class='admin-kpi-row'>
            <span class='admin-kpi-pill'><small>Eventos no período</small><strong>{webhooks_period}</strong></span>
            <span class='admin-kpi-pill'><small>Último dia</small><strong>{webhook_series[-1][1] if webhook_series else 0}</strong></span>
          </div>
          {_line_chart_svg(webhook_series, color="#54e0ad", label="Webhooks recebidos por dia")}
        </article>
      </section>
      <section class='admin-dashboard-grid'>
        <article>
          <h3>Assinaturas externas</h3>
          <p class='admin-compact'>Resumo do estado comercial vindo da plataforma externa.</p>
          <div class='admin-kpi-row'>
            <span class='admin-kpi-pill'><small>Ativas</small><strong>{active_subs}</strong></span>
            <span class='admin-kpi-pill'><small>Atenção</small><strong>{attention_subs}</strong></span>
          </div>
          <table role='grid'>
            <thead><tr><th>Status</th><th>Total</th></tr></thead>
            <tbody>{subscription_rows or '<tr><td colspan="2">Sem assinaturas externas.</td></tr>'}</tbody>
          </table>
          <p><a class='secondary' role='button' href='/webhooks/subscriptions'>Ver assinaturas</a></p>
        </article>
        <article>
          <h3>Últimas falhas de webhook</h3>
          <p class='admin-compact'>Eventos que exigem atenção operacional ou reprocessamento.</p>
          <table role='grid'>
            <thead><tr><th>Evento</th><th>Status</th><th>Tipo</th><th>Erro</th></tr></thead>
            <tbody>{failure_rows or '<tr><td colspan="4">Nenhuma falha recente.</td></tr>'}</tbody>
          </table>
          <p><a class='secondary' role='button' href='/webhooks?status=FAILED'>Ver falhas</a></p>
        </article>
      </section>
      <article>
        <h3>Top views</h3>
        <p class='admin-compact'>Cartões com maior volume de visualizações públicas.</p>
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
    dev_badge_tools = ""
    now = datetime.now(timezone.utc)
    with get_session() as session:
        active_badge = session.execute(
            select(models.ProfileBadge)
            .where(
                models.ProfileBadge.card_uid == uid,
                models.ProfileBadge.badge_type == "soomei_connector",
                models.ProfileBadge.expires_at > now,
            )
            .limit(1)
        ).scalar_one_or_none()
    badge_status = (
        f"Ativo até {_dt(active_badge.expires_at)}"
        if active_badge
        else "Sem selo ativo"
    )
    if settings.app_env != "prod":
        csrf_token = _csrf_value(request)
        dev_badge_tools = f"""
      <article>
        <h4>Dev: ativar Destaque Soomei</h4>
        <p class='admin-compact'>Ferramenta apenas de desenvolvimento para visualizar o benefício Destaque Soomei no perfil público, como se a própria Soomei tivesse ativado esse plus de visibilidade.</p>
        <p><strong>Status atual:</strong> {html.escape(badge_status)}</p>
        <form method='post' action='/cards/{html.escape(uid)}/dev/connector-badge' class='grid'>
          <input type='hidden' name='csrf_token' value='{html.escape(csrf_token)}'>
          <label>Validade em dias
            <input name='days' type='number' min='1' max='365' value='30'>
          </label>
          <button type='submit'>Ativar selo de teste</button>
        </form>
      </article>
        """
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
      {dev_badge_tools}
      <p><a class='secondary' href='/cards'>Voltar</a> <a class='secondary' target='_blank' href='/{html.escape(card.vanity or card.uid)}'>Ver público</a></p>
    """
    return _layout(request, f"Admin | Cartão {html.escape(uid)}", body, csrf_token=_csrf_value(request))


@app.post("/cards/{uid}/dev/connector-badge")
def dev_grant_connector_badge(uid: str, request: Request, days: int = Form(30), csrf_token: str = Form("")):
    if settings.app_env == "prod":
        raise HTTPException(404, "recurso indisponivel")
    _csrf_protect(request, csrf_token)
    admin_email = require_admin(request)
    card = repo.get_card_by_uid(uid)
    if not card:
        return RedirectResponse("/cards?error=nao_encontrado", status_code=303)
    safe_days = max(1, min(int(days or 30), 365))
    now = datetime.now(timezone.utc)
    with get_session() as session:
        badge = session.execute(
            select(models.ProfileBadge)
            .where(
                models.ProfileBadge.card_uid == uid,
                models.ProfileBadge.badge_type == "soomei_connector",
            )
            .limit(1)
        ).scalar_one_or_none()
        expires_at = now + timedelta(days=safe_days)
        if badge:
            badge.label = "Destaque Soomei"
            badge.starts_at = now
            badge.expires_at = expires_at
            badge.source = "admin_dev"
            badge.source_id = admin_email
            badge.updated_at = now
        else:
            session.add(
                models.ProfileBadge(
                    id=secrets.token_hex(16),
                    card_uid=uid,
                    badge_type="soomei_connector",
                    label="Destaque Soomei",
                    starts_at=now,
                    expires_at=expires_at,
                    source="admin_dev",
                    source_id=admin_email,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()
    return RedirectResponse(f"/cards/{html.escape(uid)}?ok=connector_badge", status_code=303)


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
        f"<td>{_boolean_badge(bool(user.email_verified_at))}</td>"
        f"<td>{_boolean_badge(_admin_allowed(user.email))}</td>"
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
            f"<td>{_status_badge(status)}</td>"
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


def _webhooks_alert(request: Request) -> str:
    ok = (request.query_params.get("ok") or "").strip()
    if ok == "retry":
        status = (request.query_params.get("status") or "").strip()
        return _flash_markup("ok", f"Reprocessamento solicitado. Status atual: {status or '—'}")
    error = (request.query_params.get("error") or "").strip()
    error_map = {
        "nao_encontrado": "Evento não encontrado.",
        "csrf": "Sessão expirada. Tente novamente.",
    }
    return _flash_markup("error", error_map.get(error, error))


def _webhook_event_filters(*, status: str = "", provider: str = "", event_type: str = "", q: str = "") -> list:
    filters: list = []
    if status:
        filters.append(models.WebhookEvent.status == status.strip().upper())
    if provider:
        filters.append(func.lower(models.WebhookEvent.provider) == provider.strip().lower())
    if event_type:
        filters.append(func.lower(models.WebhookEvent.event_type) == event_type.strip().lower())
    qnorm = (q or "").strip().lower()
    if qnorm:
        pattern = f"%{qnorm}%"
        filters.append(
            or_(
                func.lower(models.WebhookEvent.id).like(pattern),
                func.lower(models.WebhookEvent.external_event_id).like(pattern),
                func.lower(models.WebhookEvent.event_type).like(pattern),
                func.lower(func.coalesce(models.WebhookEvent.correlation_id, "")).like(pattern),
                func.lower(func.coalesce(models.WebhookEvent.error_code, "")).like(pattern),
                func.lower(func.coalesce(models.WebhookEvent.error_message, "")).like(pattern),
            )
        )
    return filters


def _webhook_events_page(*, status: str = "", provider: str = "", event_type: str = "", q: str = "", page: int = 1) -> PageResult:
    stmt = select(models.WebhookEvent).where(*_webhook_event_filters(status=status, provider=provider, event_type=event_type, q=q))
    return repo._page_from_statement(
        stmt,
        page=page,
        page_size=PAGE_SIZE,
        order_by=[models.WebhookEvent.received_at.desc()],
    )


def _webhook_counts() -> dict[str, int]:
    with get_session() as session:
        rows = session.execute(
            select(models.WebhookEvent.status, func.count(models.WebhookEvent.id)).group_by(models.WebhookEvent.status)
        ).all()
    result = {str(status or "UNKNOWN"): int(total or 0) for status, total in rows}
    result["TOTAL"] = sum(result.values())
    return result


def _webhook_retry_form(event_id: str, csrf_token: str, *, label: str = "Reprocessar") -> str:
    return (
        f"<form method='post' action='/webhooks/events/{html.escape(event_id)}/retry' class='admin-inline-form'>"
        f"<input type='hidden' name='csrf_token' value='{html.escape(csrf_token)}'>"
        f"<button class='secondary' type='submit'>{html.escape(label)}</button>"
        "</form>"
    )


def _webhook_event_row(event, csrf_token: str) -> str:
    retry = ""
    if (event.status or "").upper() in {"RECEIVED", "FAILED", "RETRY_PENDING"}:
        retry = _webhook_retry_form(event.id, csrf_token)
    error = event.error_message or event.error_code or ""
    return (
        "<tr>"
        f"<td><a href='/webhooks/events/{html.escape(event.id)}'><code>{html.escape(event.external_event_id)}</code></a></td>"
        f"<td>{html.escape(event.provider or '')}</td>"
        f"<td>{html.escape(event.event_type or '')}</td>"
        f"<td>{_webhook_status_badge(event.status or '')}</td>"
        f"<td>{int(event.attempts or 0)}</td>"
        f"<td>{_dt(event.received_at)}</td>"
        f"<td class='admin-muted-cell'>{html.escape(error)}</td>"
        f"<td><div class='admin-actions-row'><a class='secondary' role='button' href='/webhooks/events/{html.escape(event.id)}'>Detalhes</a>{retry}</div></td>"
        "</tr>"
    )


def _event_payload_data(event) -> dict:
    payload = event.payload or {}
    data = payload.get("data") if isinstance(payload, dict) else {}
    return data if isinstance(data, dict) else {}


def _related_records_for_event(event) -> tuple[object | None, object | None, object | None]:
    data = _event_payload_data(event)
    customer_id = str(data.get("customer_id") or "").strip()
    subscription_id = str(data.get("subscription_id") or data.get("order_id") or "").strip()
    product_id = str(data.get("product_id") or "").strip()
    provider = (event.provider or "").strip()
    member = None
    subscription = None
    card = None
    with get_session() as session:
        if provider and customer_id:
            member = session.execute(
                select(models.Member)
                .where(models.Member.provider == provider, models.Member.external_customer_id == customer_id)
                .limit(1)
            ).scalar_one_or_none()
        if provider and subscription_id:
            subscription = session.execute(
                select(models.ExternalSubscription)
                .where(
                    models.ExternalSubscription.provider == provider,
                    models.ExternalSubscription.external_subscription_id == subscription_id,
                )
                .limit(1)
            ).scalar_one_or_none()
            card_filters = [
                models.Card.external_provider == provider,
                models.Card.external_subscription_id == subscription_id,
            ]
            if product_id:
                card_filters.append(models.Card.external_product_id == product_id)
            card = session.execute(select(models.Card).where(*card_filters).limit(1)).scalar_one_or_none()
            if not card and product_id:
                card = session.execute(
                    select(models.Card)
                    .where(
                        models.Card.external_provider == provider,
                        models.Card.external_subscription_id == subscription_id,
                    )
                    .limit(1)
                ).scalar_one_or_none()
        if not subscription and provider and customer_id:
            subscription = session.execute(
                select(models.ExternalSubscription)
                .where(
                    models.ExternalSubscription.provider == provider,
                    models.ExternalSubscription.external_customer_id == customer_id,
                )
                .limit(1)
            ).scalar_one_or_none()
    return member, subscription, card


def _status_history_rows(uid: str, *, limit: int = 20) -> str:
    if not uid:
        return ""
    with get_session() as session:
        rows = session.execute(
            select(models.CardStatusHistory)
            .where(models.CardStatusHistory.card_uid == uid)
            .order_by(models.CardStatusHistory.created_at.desc())
            .limit(limit)
        ).scalars().all()
    return "".join(
        "<tr>"
        f"<td>{_dt(row.created_at)}</td>"
        f"<td>{html.escape(row.previous_status or '')}</td>"
        f"<td>{html.escape(row.new_status or '')}</td>"
        f"<td>{html.escape(row.reason or '')}</td>"
        f"<td>{html.escape(row.external_event_id or '')}</td>"
        "</tr>"
        for row in rows
    )


@app.get("/webhooks", response_class=HTMLResponse)
def list_webhooks(request: Request, status: str = "", provider: str = "", event_type: str = "", q: str = "", page: int = 1):
    try:
        require_admin(request)
    except HTTPException:
        return _redirect_login("/webhooks")
    csrf_token = _csrf_value(request)
    page_result = _webhook_events_page(status=status, provider=provider, event_type=event_type, q=q, page=page)
    counts = _webhook_counts()
    rows = "\n".join(_webhook_event_row(event, csrf_token) for event in page_result.items)
    status_options = ["", "RECEIVED", "PROCESSING", "PROCESSED", "FAILED", "RETRY_PENDING", "IGNORED", "DEAD_LETTER"]
    status_select = "".join(
        f"<option value='{html.escape(value)}' {'selected' if status.upper() == value else ''}>{html.escape(value or 'Todos os status')}</option>"
        for value in status_options
    )
    body = f"""
      <section class='admin-summary'>
        <article><header>Total eventos</header><strong>{counts.get('TOTAL', 0)}</strong></article>
        <article><header>Processados</header><strong>{counts.get('PROCESSED', 0)}</strong></article>
        <article><header>Falhas</header><strong>{counts.get('FAILED', 0) + counts.get('DEAD_LETTER', 0)}</strong></article>
        <article><header>Pendentes</header><strong>{counts.get('RECEIVED', 0) + counts.get('RETRY_PENDING', 0)}</strong></article>
      </section>
      <article>
        <h3>Eventos de webhook</h3>
        <p class='admin-compact'>Acompanhe recebimento, processamento, payload e tentativas da integração TheMembers/assinaturas.</p>
        {_webhooks_alert(request)}
        <form class='admin-filter-grid' method='get' action='/webhooks'>
          <label>Busca <input name='q' placeholder='event id, tipo, erro, correlação' value='{html.escape(q)}'></label>
          <label>Status <select name='status'>{status_select}</select></label>
          <label>Provider <input name='provider' placeholder='themembers' value='{html.escape(provider)}'></label>
          <label>Tipo <input name='event_type' placeholder='subscription.payment_approved' value='{html.escape(event_type)}'></label>
          <button type='submit'>Filtrar</button>
        </form>
        <p class='admin-actions-row'>
          <a class='secondary' role='button' href='/webhooks/subscriptions'>Ver assinaturas externas</a>
        </p>
        <table role='grid'>
          <thead><tr><th>Evento externo</th><th>Provider</th><th>Tipo</th><th>Status</th><th>Tent.</th><th>Recebido</th><th>Erro</th><th>Ações</th></tr></thead>
          <tbody>{rows or '<tr><td colspan="8">Nenhum evento encontrado.</td></tr>'}</tbody>
        </table>
        {_pager_html('/webhooks', page_result, q=q, status=status, provider=provider, event_type=event_type)}
      </article>
    """
    return _layout(request, "Admin | Webhooks", body, csrf_token=csrf_token)


@app.get("/webhooks/events/{event_id}", response_class=HTMLResponse)
def webhook_event_detail(event_id: str, request: Request):
    try:
        require_admin(request)
    except HTTPException:
        return _redirect_login(f"/webhooks/events/{event_id}")
    with get_session() as session:
        event = session.get(models.WebhookEvent, event_id)
    if not event:
        return RedirectResponse("/webhooks?error=nao_encontrado", status_code=303)
    csrf_token = _csrf_value(request)
    member, subscription, card = _related_records_for_event(event)
    data = _event_payload_data(event)
    retry_html = ""
    if (event.status or "").upper() in {"RECEIVED", "FAILED", "RETRY_PENDING"}:
        retry_html = _webhook_retry_form(event.id, csrf_token, label="Reprocessar agora")
    card_uid = getattr(card, "uid", "") or ""
    history_rows = _status_history_rows(card_uid) if card_uid else ""
    related_html = f"""
      <dl class='admin-detail-list'>
        <dt>Cliente externo</dt><dd>{html.escape(str(data.get('customer_id') or ''))}</dd>
        <dt>Assinatura/Pedido</dt><dd>{html.escape(str(data.get('subscription_id') or data.get('order_id') or ''))}</dd>
        <dt>Produto</dt><dd>{html.escape(str(data.get('product_id') or ''))}</dd>
        <dt>Membro local</dt><dd>{html.escape(getattr(member, 'email', '') or '—')}</dd>
        <dt>Status assinatura</dt><dd>{_subscription_status_badge(getattr(subscription, 'status', '') or '')}</dd>
        <dt>Cartão</dt><dd>{f"<a href='/cards/{html.escape(card_uid)}'><code>{html.escape(card_uid)}</code></a>" if card_uid else '—'}</dd>
        <dt>Status cartão</dt><dd>{_status_badge(getattr(card, 'status', '') or '') if card else '—'}</dd>
      </dl>
    """
    body = f"""
      <article>
        <p><a class='secondary' href='/webhooks' role='button'>← Voltar para eventos</a></p>
        {_webhooks_alert(request)}
        <div class='admin-actions-row'>{retry_html}</div>
      </article>
      <section class='admin-grid-2'>
        <article>
          <h3>Evento</h3>
          <dl class='admin-detail-list'>
            <dt>ID interno</dt><dd><code>{html.escape(event.id)}</code></dd>
            <dt>Evento externo</dt><dd><code>{html.escape(event.external_event_id)}</code></dd>
            <dt>Provider</dt><dd>{html.escape(event.provider or '')}</dd>
            <dt>Tipo</dt><dd>{html.escape(event.event_type or '')}</dd>
            <dt>Status</dt><dd>{_webhook_status_badge(event.status or '')}</dd>
            <dt>Tentativas</dt><dd>{int(event.attempts or 0)}</dd>
            <dt>Correlação</dt><dd>{html.escape(event.correlation_id or '—')}</dd>
            <dt>Recebido</dt><dd>{_dt(event.received_at)}</dd>
            <dt>Início processamento</dt><dd>{_dt(event.processing_started_at)}</dd>
            <dt>Processado</dt><dd>{_dt(event.processed_at)}</dd>
            <dt>Próximo retry</dt><dd>{_dt(event.next_retry_at)}</dd>
            <dt>Código erro</dt><dd>{html.escape(event.error_code or '—')}</dd>
            <dt>Mensagem erro</dt><dd>{html.escape(event.error_message or '—')}</dd>
          </dl>
        </article>
        <article>
          <h3>Relações</h3>
          {related_html}
        </article>
      </section>
      <article>
        <h3>Payload recebido</h3>
        <pre class='admin-code-block'>{_json_pretty(event.payload)}</pre>
      </article>
      <article>
        <h3>Histórico do cartão relacionado</h3>
        <table role='grid'>
          <thead><tr><th>Data</th><th>Anterior</th><th>Novo</th><th>Motivo</th><th>Evento externo</th></tr></thead>
          <tbody>{history_rows or '<tr><td colspan="5">Nenhum histórico relacionado.</td></tr>'}</tbody>
        </table>
      </article>
    """
    return _layout(request, "Admin | Evento webhook", body, csrf_token=csrf_token)


def _subscription_filters(*, status: str = "", provider: str = "", q: str = "") -> list:
    filters: list = []
    if status:
        filters.append(func.upper(models.ExternalSubscription.status) == status.strip().upper())
    if provider:
        filters.append(func.lower(models.ExternalSubscription.provider) == provider.strip().lower())
    qnorm = (q or "").strip().lower()
    if qnorm:
        pattern = f"%{qnorm}%"
        filters.append(
            or_(
                func.lower(models.ExternalSubscription.external_customer_id).like(pattern),
                func.lower(func.coalesce(models.ExternalSubscription.external_subscription_id, "")).like(pattern),
                func.lower(func.coalesce(models.ExternalSubscription.external_order_id, "")).like(pattern),
                func.lower(func.coalesce(models.ExternalSubscription.external_product_id, "")).like(pattern),
            )
        )
    return filters


def _subscription_card(provider: str, subscription_id: str, product_id: str = ""):
    if not provider or not subscription_id:
        return None
    filters = [models.Card.external_provider == provider, models.Card.external_subscription_id == subscription_id]
    if product_id:
        filters.append(models.Card.external_product_id == product_id)
    with get_session() as session:
        card = session.execute(select(models.Card).where(*filters).limit(1)).scalar_one_or_none()
        if card or not product_id:
            return card
        return session.execute(
            select(models.Card)
            .where(models.Card.external_provider == provider, models.Card.external_subscription_id == subscription_id)
            .limit(1)
        ).scalar_one_or_none()


def _subscriptions_page(*, status: str = "", provider: str = "", q: str = "", page: int = 1) -> PageResult:
    stmt = select(models.ExternalSubscription).where(*_subscription_filters(status=status, provider=provider, q=q))
    return repo._page_from_statement(
        stmt,
        page=page,
        page_size=PAGE_SIZE,
        order_by=[models.ExternalSubscription.updated_at.desc()],
    )


@app.get("/webhooks/subscriptions", response_class=HTMLResponse)
def list_external_subscriptions(request: Request, status: str = "", provider: str = "", q: str = "", page: int = 1):
    try:
        require_admin(request)
    except HTTPException:
        return _redirect_login("/webhooks/subscriptions")
    page_result = _subscriptions_page(status=status, provider=provider, q=q, page=page)
    rows = []
    for sub in page_result.items:
        card = _subscription_card(sub.provider, sub.external_subscription_id or "", sub.external_product_id or "")
        card_link = (
            f"<a href='/cards/{html.escape(card.uid)}'><code>{html.escape(card.uid)}</code></a>"
            if card
            else "—"
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(sub.provider or '')}</td>"
            f"<td><code>{html.escape(sub.external_subscription_id or '')}</code></td>"
            f"<td>{html.escape(sub.external_customer_id or '')}</td>"
            f"<td>{html.escape(sub.external_product_id or '')}</td>"
            f"<td>{_subscription_status_badge(sub.status or '')}</td>"
            f"<td>{card_link}</td>"
            f"<td>{_dt(sub.updated_at)}</td>"
            "</tr>"
        )
    status_options = ["", "PENDING", "ACTIVE", "OVERDUE", "SUSPENDED", "CANCELLED", "REFUNDED"]
    status_select = "".join(
        f"<option value='{html.escape(value)}' {'selected' if status.upper() == value else ''}>{html.escape(value or 'Todos os status')}</option>"
        for value in status_options
    )
    body = f"""
      <article>
        <p><a class='secondary' href='/webhooks' role='button'>← Eventos</a></p>
        <h3>Assinaturas externas</h3>
        <p class='admin-compact'>Visão consolidada do estado comercial recebido pela integração e sua relação com cartões Soomei.</p>
        <form class='admin-filter-grid' method='get' action='/webhooks/subscriptions'>
          <label>Busca <input name='q' placeholder='cliente, assinatura, pedido, produto' value='{html.escape(q)}'></label>
          <label>Status <select name='status'>{status_select}</select></label>
          <label>Provider <input name='provider' placeholder='themembers' value='{html.escape(provider)}'></label>
          <button type='submit'>Filtrar</button>
        </form>
        <table role='grid'>
          <thead><tr><th>Provider</th><th>Assinatura</th><th>Cliente externo</th><th>Produto</th><th>Status</th><th>Cartão</th><th>Atualizado</th></tr></thead>
          <tbody>{''.join(rows) or '<tr><td colspan="7">Nenhuma assinatura encontrada.</td></tr>'}</tbody>
        </table>
        {_pager_html('/webhooks/subscriptions', page_result, q=q, status=status, provider=provider)}
      </article>
    """
    return _layout(request, "Admin | Assinaturas externas", body, csrf_token=_csrf_value(request))


@app.post("/webhooks/events/{event_id}/retry")
def retry_webhook_event_page(event_id: str, request: Request, csrf_token: str = Form("")):
    try:
        _csrf_protect(request, csrf_token)
        require_admin(request)
    except HTTPException:
        return RedirectResponse(f"/webhooks/events/{html.escape(event_id)}?error=csrf", status_code=303)
    service = MembershipWebhookService()
    event = service.process_event(event_id)
    if not event:
        return RedirectResponse("/webhooks?error=nao_encontrado", status_code=303)
    return RedirectResponse(
        f"/webhooks/events/{html.escape(event_id)}?ok=retry&status={html.escape(event.status or '')}",
        status_code=303,
    )


def _referrals_filters(*, status: str = "", q: str = "") -> list:
    filters: list = []
    if status:
        filters.append(func.lower(models.Referral.status) == status.strip().lower())
    qnorm = (q or "").strip().lower()
    if qnorm:
        pattern = f"%{qnorm}%"
        filters.append(
            or_(
                func.lower(models.Referral.code_used).like(pattern),
                func.lower(func.coalesce(models.Referral.referrer_email, "")).like(pattern),
                func.lower(func.coalesce(models.Referral.referred_email, "")).like(pattern),
                func.lower(func.coalesce(models.Referral.referrer_card_uid, "")).like(pattern),
                func.lower(models.Referral.referred_card_uid).like(pattern),
            )
        )
    return filters


def _referrals_page(*, status: str = "", q: str = "", page: int = 1) -> PageResult:
    stmt = select(models.Referral).where(*_referrals_filters(status=status, q=q))
    return repo._page_from_statement(
        stmt,
        page=page,
        page_size=PAGE_SIZE,
        order_by=[models.Referral.created_at.desc()],
    )


def _referral_counts() -> dict[str, int]:
    with get_session() as session:
        total = int(session.execute(select(func.count(models.Referral.id))).scalar() or 0)
        pending = int(
            session.execute(
                select(func.count(models.Referral.id)).where(models.Referral.status == "pending_validation")
            ).scalar()
            or 0
        )
        qualified = int(session.execute(select(func.count(models.Referral.id)).where(models.Referral.status == "qualified")).scalar() or 0)
        coupons = int(session.execute(select(func.count(models.RaffleEntry.id)).where(models.RaffleEntry.status == "active")).scalar() or 0)
        active_badges = int(
            session.execute(
                select(func.count(models.ProfileBadge.id)).where(models.ProfileBadge.expires_at > datetime.now(timezone.utc))
            ).scalar()
            or 0
        )
    return {"total": total, "pending": pending, "qualified": qualified, "coupons": coupons, "active_badges": active_badges}


@app.get("/referrals", response_class=HTMLResponse)
def list_referrals(request: Request, status: str = "", q: str = "", page: int = 1):
    try:
        require_admin(request)
    except HTTPException:
        return _redirect_login("/referrals")
    counts = _referral_counts()
    page_result = _referrals_page(status=status, q=q, page=page)
    rows = []
    for ref in page_result.items:
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(ref.code_used or '')}</code></td>"
            f"<td>{html.escape(ref.referrer_email or '—')}<br><small><code>{html.escape(ref.referrer_card_uid or '')}</code></small></td>"
            f"<td>{html.escape(ref.referred_email or '—')}<br><small><code>{html.escape(ref.referred_card_uid or '')}</code></small></td>"
            f"<td>{_status_badge(ref.status or '')}</td>"
            f"<td>{_dt(ref.qualified_at or ref.qualify_after or ref.created_at)}</td>"
            f"<td>{html.escape(ref.rejection_reason or '—')}</td>"
            "</tr>"
        )
    status_select = "".join(
        f"<option value='{html.escape(value)}' {'selected' if status.lower() == value else ''}>{html.escape(label)}</option>"
        for value, label in [
            ("", "Todos"),
            ("pending_validation", "Em validação"),
            ("qualified", "Qualificadas"),
            ("disqualified", "Desqualificadas"),
            ("rejected", "Rejeitadas"),
        ]
    )
    body = f"""
      <section class='admin-summary'>
        <article><header>Total indicações</header><strong>{counts['total']}</strong></article>
        <article><header>Em validação</header><strong>{counts['pending']}</strong></article>
        <article><header>Qualificadas</header><strong>{counts['qualified']}</strong></article>
        <article><header>Selos ativos</header><strong>{counts['active_badges']}</strong></article>
        <article><header>Cupons ativos</header><strong>{counts['coupons']}</strong></article>
      </section>
      <article>
        <h3>Indicações e recompensas</h3>
        <p class='admin-compact'>Acompanhe códigos usados na ativação, benefícios concedidos e cupons da campanha Pix da Virada.</p>
        <form class='admin-filter-grid' method='get' action='/referrals'>
          <label>Busca <input name='q' placeholder='código, e-mail ou cartão' value='{html.escape(q)}'></label>
          <label>Status <select name='status'>{status_select}</select></label>
          <button type='submit'>Filtrar</button>
        </form>
        <table role='grid'>
          <thead><tr><th>Código</th><th>Indicador</th><th>Indicado</th><th>Status</th><th>Validação/Data</th><th>Obs.</th></tr></thead>
          <tbody>{''.join(rows) or '<tr><td colspan="6">Nenhuma indicação encontrada.</td></tr>'}</tbody>
        </table>
        {_pager_html('/referrals', page_result, q=q, status=status)}
      </article>
    """
    return _layout(request, "Admin | Indicações", body, csrf_token=_csrf_value(request))


@app.get("/api/v1/admin/webhook-events")
def admin_list_webhook_events(request: Request, status: str = "", external_event_id: str = "", limit: int = 50):
    require_admin(request)
    safe_limit = max(1, min(int(limit or 50), 100))
    filters = []
    if status:
        filters.append(models.WebhookEvent.status == status.strip().upper())
    if external_event_id:
        filters.append(models.WebhookEvent.external_event_id == external_event_id.strip())
    with get_session() as session:
        stmt = (
            select(models.WebhookEvent)
            .where(*filters)
            .order_by(models.WebhookEvent.received_at.desc())
            .limit(safe_limit)
        )
        rows = session.execute(stmt).scalars().all()
    return JSONResponse(
        {
            "items": [
                {
                    "id": row.id,
                    "provider": row.provider,
                    "external_event_id": row.external_event_id,
                    "event_type": row.event_type,
                    "status": row.status,
                    "attempts": row.attempts,
                    "received_at": row.received_at.isoformat() if row.received_at else None,
                    "processed_at": row.processed_at.isoformat() if row.processed_at else None,
                    "next_retry_at": row.next_retry_at.isoformat() if row.next_retry_at else None,
                    "error_code": row.error_code,
                    "error_message": row.error_message,
                    "correlation_id": row.correlation_id,
                }
                for row in rows
            ]
        }
    )


@app.get("/api/v1/admin/card-status-history/{uid}")
def admin_card_status_history(uid: str, request: Request, limit: int = 50):
    require_admin(request)
    safe_limit = max(1, min(int(limit or 50), 100))
    with get_session() as session:
        stmt = (
            select(models.CardStatusHistory)
            .where(models.CardStatusHistory.card_uid == uid)
            .order_by(models.CardStatusHistory.created_at.desc())
            .limit(safe_limit)
        )
        rows = session.execute(stmt).scalars().all()
    return JSONResponse(
        {
            "items": [
                {
                    "id": row.id,
                    "card_uid": row.card_uid,
                    "previous_status": row.previous_status,
                    "new_status": row.new_status,
                    "reason": row.reason,
                    "source": row.source,
                    "actor_id": row.actor_id,
                    "external_event_id": row.external_event_id,
                    "metadata": row.metadata_json,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]
        }
    )


@app.post("/api/v1/admin/webhook-events/{event_id}/retry")
def admin_retry_webhook_event(event_id: str, request: Request, csrf_token: str = Form("")):
    _csrf_protect(request, csrf_token)
    email = require_admin(request)
    service = MembershipWebhookService()
    event = service.process_event(event_id)
    return JSONResponse(
        {
            "requested_by": email,
            "event_id": event_id,
            "status": event.status if event else "not_found",
        },
        status_code=200 if event else 404,
    )


def create_admin_app() -> FastAPI:
    """Factory compatível com uvicorn/gunicorn."""
    return app
