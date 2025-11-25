from __future__ import annotations

import html
import os

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from api.core import csrf
from api.core.config import get_settings
from api.repositories.sql_repository import SQLRepository

router = APIRouter(prefix="", tags=["pages"])
_sql_repo = SQLRepository()
_settings = get_settings()

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
    footer_html = BRAND_FOOTER(content) if BRAND_FOOTER else content
    marker = "</footer>"
    if marker in footer_html:
        action = "<span class='footer-auth-slot'></span>"
        footer_html = footer_html.replace(marker, f"{action}{marker}", 1)
    return footer_html


@router.get("/onboard/{uid}", response_class=HTMLResponse)
def onboard(request: Request, uid: str, email: str = "", vanity: str = "", error: str = ""):
    card_entity = _sql_repo.get_card_by_uid(uid)
    if not card_entity:
        return RedirectResponse("/invalid", status_code=302)
    status = (card_entity.status or "").lower()
    if status == "active":
        dest = (card_entity.vanity or uid)
        return RedirectResponse(f"/{html.escape(dest)}", status_code=302)
    if status == "blocked":
        return RedirectResponse("/blocked", status_code=302)
    templates = _templates(request)
    csrf_token = csrf.ensure_csrf_token(request)
    context = {
        "request": request,
        "uid": uid,
        "email": email,
        "vanity": vanity,
        "error": error,
        "uid_exists": True,
        "show_welcome": True,
        "csrf_token": csrf_token,
    }
    response = templates.TemplateResponse("onboard.html", context)
    csrf.set_csrf_cookie(response, csrf_token)
    return response

@router.get("/login", response_class=HTMLResponse)
def login(request: Request, uid: str = "", error: str = ""):
    templates = _templates(request)
    csrf_token = csrf.ensure_csrf_token(request)
    response = templates.TemplateResponse(
        "login.html", {"request": request, "uid": uid, "error": error, "csrf_token": csrf_token}
    )
    csrf.set_csrf_cookie(response, csrf_token)
    return response


@router.get("/invalid", response_class=HTMLResponse)
def invalid(request: Request):
    templates = _templates(request)
    return templates.TemplateResponse("invalid.html", {"request": request})


@router.get("/onboard/{uid}/pin", response_class=HTMLResponse)
def onboard_pin(request: Request, uid: str, error: str = ""):
    csrf_token = csrf.ensure_csrf_token(request)
    html_doc = f"""
    <!doctype html><html lang='pt-br'><head>
      <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
      <link rel='stylesheet' href='/static/card.css'>
      <title>Confirmar PIN</title>
    </head><body>
      <main class='wrap'>
        <section class='card card-public carbon card-center'>
          <h1>Confirmar PIN</h1>
          <p>Informe o PIN da sua carta para continuar a ativação.</p>
          {f"<p class='banner bad'>{html.escape(error)}</p>" if error else ""}
          <form method='post' action='/onboard/{html.escape(uid)}/pin' class='grid'>
            <input type='hidden' name='csrf_token' value='{html.escape(csrf_token)}'>
            <label>PIN</label>
            <input name='pin' type='password' inputmode='numeric' pattern='[0-9]*' required>
            <button class='btn'>Continuar</button>
          </form>
          <p class='muted' style='margin-top:8px'>Não sabe o PIN? Ele está impresso na carta/etiqueta do cartão.</p>
        </section>
      </main>
    </body></html>
    """
    response = HTMLResponse(html_doc)
    csrf.set_csrf_cookie(response, csrf_token)
    return response


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


@router.post("/onboard/{uid}/pin")
def onboard_pin_post(request: Request, uid: str, pin: str = Form(""), csrf_token: str = Form("")):
    csrf.validate_csrf(request, csrf_token)
    card_entity = _sql_repo.get_card_by_uid(uid)
    if not card_entity:
        return RedirectResponse("/invalid", status_code=302)
    if str(pin or "").strip() != str(card_entity.pin or "").strip():
        resp = RedirectResponse(f"/onboard/{uid}/pin?error=PIN%20incorreto", status_code=303)
        resp.delete_cookie("pending_pin", path="/auth")
        return resp
    resp = RedirectResponse(f"/auth/pending?uid={uid}", status_code=303)
    secure_cookie = _settings.app_env == "prod"
    resp.set_cookie(
        "pending_pin",
        f"{uid}:{pin}",
        max_age=300,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
        path="/auth",
    )
    csrf.set_csrf_cookie(resp, csrf.ensure_csrf_token(request))
    return resp
