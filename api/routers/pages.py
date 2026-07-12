from __future__ import annotations

import html
import os

from fastapi import APIRouter, Form, Request
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
    templates = getattr(getattr(request.app, "state", None), "templates", None)
    if templates:
        return templates
    raise RuntimeError("Templates nao configurados")


def _apply_brand_footer(content: str) -> str:
    footer_html = BRAND_FOOTER(content) if BRAND_FOOTER else content
    marker = "</footer>"
    if marker in footer_html:
        footer_html = footer_html.replace(marker, "<span class='footer-auth-slot'></span></footer>", 1)
    return footer_html


@router.get("/onboard/{uid}", response_class=HTMLResponse)
def onboard(request: Request, uid: str, email: str = "", vanity: str = "", error: str = ""):
    card_entity = _sql_repo.get_card_by_uid(uid)
    if not card_entity:
        return RedirectResponse("/invalid", status_code=302)
    status = (card_entity.status or "").lower()
    if status == "active":
        return RedirectResponse(f"/{html.escape(card_entity.vanity or uid)}", status_code=302)
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
        "login.html",
        {"request": request, "uid": uid, "error": error, "csrf_token": csrf_token},
    )
    csrf.set_csrf_cookie(response, csrf_token)
    return response


@router.get("/invalid", response_class=HTMLResponse)
def invalid(request: Request):
    templates = _templates(request)
    return templates.TemplateResponse("invalid.html", {"request": request})


@router.get("/onboard/{uid}/pin", response_class=HTMLResponse)
def onboard_pin(request: Request, uid: str, error: str = ""):
    templates = _templates(request)
    csrf_token = csrf.ensure_csrf_token(request)
    response = templates.TemplateResponse(
        "onboard_pin.html",
        {"request": request, "uid": uid, "error": error, "csrf_token": csrf_token},
    )
    csrf.set_csrf_cookie(response, csrf_token)
    return response


@router.get("/legal/terms", response_class=HTMLResponse)
def legal_terms(request: Request):
    if not LEGAL_TERMS_PATH or not os.path.exists(LEGAL_TERMS_PATH):
        html_doc = f"""
        <!doctype html><html lang='pt-br'><head>
        <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <link rel='stylesheet' href='{html.escape(CSS_HREF)}'><title>Termos indisponíveis</title></head><body>
        <main class='wrap'>
          <section class='status-shell'>
            <div class='status-card carbon'>
              <div class='status-glow' aria-hidden='true'></div>
              <div class='status-brand'>
                <img src='/static/img/soomei_logo.png' alt='Soomei' class='status-logo'>
                <span>Soomei</span>
              </div>
              <div class='status-body'>
                <p class='status-kicker'>Documento</p>
                <h1>Termos indisponíveis</h1>
                <p class='status-intro'>Não conseguimos carregar os termos agora. Tente novamente em instantes ou volte para continuar sua navegação.</p>
                <div class='status-actions'>
                  <a class='btn primary' href='/login'>Ir para login</a>
                  <a class='btn ghost' href='javascript:history.back()'>Voltar</a>
                </div>
              </div>
            </div>
          </section>
        </main>
        </body></html>
        """
        return HTMLResponse(_apply_brand_footer(html_doc), status_code=404)
    with open(LEGAL_TERMS_PATH, "r", encoding="utf-8") as handle:
        safe = html.escape(handle.read()).replace("\n", "<br>")
    templates = _templates(request)
    response = templates.TemplateResponse("legal_terms.html", {"request": request, "safe": safe})
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


# Silencia requisicoes de debug do Chrome (evita 404 ruidoso em logs)
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
        response = RedirectResponse(f"/onboard/{uid}/pin?error=PIN%20incorreto", status_code=303)
        response.delete_cookie("pending_pin", path="/auth")
        return response
    response = RedirectResponse(f"/auth/pending?uid={uid}", status_code=303)
    response.set_cookie(
        "pending_pin",
        f"{uid}:{pin}",
        max_age=300,
        httponly=True,
        samesite="lax",
        secure=_settings.app_env == "prod",
        path="/auth",
    )
    csrf.set_csrf_cookie(response, csrf.ensure_csrf_token(request))
    return response
