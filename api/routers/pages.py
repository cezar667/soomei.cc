from __future__ import annotations

import html
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from api.core import csrf
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
    footer_html = BRAND_FOOTER(content) if BRAND_FOOTER else content
    marker = "</footer>"
    if marker in footer_html:
        action = "<span class='footer-auth-slot'></span>"
        footer_html = footer_html.replace(marker, f"{action}{marker}", 1)
    return footer_html


@router.get("/onboard/{uid}", response_class=HTMLResponse)
def onboard(request: Request, uid: str, email: str = "", vanity: str = "", error: str = ""):
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
    templates = _templates(request)
    csrf_token = csrf.ensure_csrf_token(request)
    context = {
        "request": request,
        "uid": uid,
        "email": email,
        "vanity": vanity,
        "error": error,
        "uid_exists": uid_exists,
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
