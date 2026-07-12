from __future__ import annotations

import html

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from api.core import csrf
from api.core.rate_limiter import rate_limit_ip
from api.services.slug_service import (
    SlugService,
    InvalidSlugError,
    SlugUnavailableError,
    CardNotFoundError,
)
from api.services.session_service import current_user_email
from api.services.card_service import find_card_by_slug
from api.repositories.sql_repository import SQLRepository

router = APIRouter(prefix="/slug", tags=["slug"])
_sql_repo = SQLRepository()


def _get_slug_service(request: Request) -> SlugService:
    svc = getattr(getattr(request.app, "state", None), "slug_service", None)
    if not svc:
        raise RuntimeError("SlugService nao configurado")
    return svc


def _get_templates(request: Request) -> Jinja2Templates:
    tpl = getattr(getattr(request.app, "state", None), "templates", None)
    if tpl:
        return tpl
    raise RuntimeError("Templates nao configurados")


def _css_href(request: Request) -> str:
    return getattr(getattr(request.app, "state", None), "css_href", "/static/card.css")


def _slug_message_response(request: Request, *, heading: str, message: str, status_code: int) -> HTMLResponse:
    css = _css_href(request)
    html_doc = f"""
    <!doctype html><html lang='pt-br'><head>
      <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
      <link rel='stylesheet' href='{css}'><title>{html.escape(heading)}</title>
    </head><body>
      <main class='wrap'>
        <section class='slug-shell'>
          <div class='slug-card carbon'>
            <div class='slug-hero'>
              <img src='/static/img/soomei_logo.png' alt='Soomei' class='slug-logo'>
              <p class='slug-kicker'>Link público</p>
              <h1>{html.escape(heading)}</h1>
              <p class='slug-intro'>{html.escape(message)}</p>
            </div>
            <div class='slug-actions'>
              <a class='btn slug-secondary' href='/'>Voltar</a>
            </div>
          </div>
        </section>
      </main>
    </body></html>
    """
    return HTMLResponse(html_doc, status_code=status_code)


def _redirect_to_card(card: dict, uid: str) -> RedirectResponse:
    dest = card.get("vanity", uid)
    return RedirectResponse(f"/{html.escape(dest)}", status_code=303)


def _wants_json_response(request: Request) -> bool:
    requested_with = (request.headers.get("x-requested-with") or "").lower()
    accept = (request.headers.get("accept") or "").lower()
    return requested_with == "xmlhttprequest" or "application/json" in accept


def _find_card_context(card_id: str) -> tuple[str | None, dict | None]:
    entity = _sql_repo.get_card_by_vanity(card_id) or _sql_repo.get_card_by_uid(card_id)
    if entity:
        card_data = {
            "user": (entity.owner_email or "").strip(),
            "vanity": (entity.vanity or entity.uid or "").strip(),
        }
        return entity.uid, card_data
    _, uid, card = find_card_by_slug(card_id)
    return uid, card


@router.get("/check")
def slug_check(request: Request, value: str = ""):
    rate_limit_ip(request, "slug:check", limit=20, window_seconds=60)
    svc = _get_slug_service(request)
    return {"available": svc.is_available(value)}


@router.get("/select/{card_id}", response_class=HTMLResponse)
def slug_select(card_id: str, request: Request):
    uid, card = _find_card_context(card_id)
    if not card or not uid:
        raise HTTPException(404, "Cartao nao encontrado")
    owner = card.get("user", "")
    who = current_user_email(request)
    if who != owner:
        return _redirect_to_card(card, uid)
    templates = _get_templates(request)
    current = card.get("vanity", "") or ""
    back_slug = card.get("vanity", uid)
    next_target = "edit" if request.query_params.get("next") == "edit" else ""
    back_href = f"/edit/{html.escape(back_slug)}" if next_target == "edit" else f"/{html.escape(back_slug)}"
    token = csrf.ensure_csrf_token(request)
    response = templates.TemplateResponse(
        "slug_select.html",
        {
            "request": request,
            "uid": uid,
            "current": current,
            "back_slug": back_slug,
            "back_href": back_href,
            "next": next_target,
            "csrf_token": token,
        },
    )
    csrf.set_csrf_cookie(response, token)
    return response


@router.post("/select/{card_id}")
def slug_select_post(
    card_id: str,
    request: Request,
    value: str = Form(""),
    csrf_token: str = Form(""),
    next: str = Form(""),
):
    uid, card = _find_card_context(card_id)
    if not card or not uid:
        raise HTTPException(404, "Cartao nao encontrado")
    owner = card.get("user", "")
    who = current_user_email(request)
    if who != owner:
        return _redirect_to_card(card, uid)
    csrf.validate_csrf(request, csrf_token)
    rate_limit_ip(request, "slug:update", limit=10, window_seconds=60)
    svc = _get_slug_service(request)
    try:
        new_slug = svc.assign_slug(uid, value)
    except InvalidSlugError:
        if _wants_json_response(request):
            return JSONResponse(
                {
                    "ok": False,
                    "error": "invalid_slug",
                    "message": "Use 3-30 caracteres: letras minúsculas, números e hífen.",
                },
                status_code=400,
            )
        return _slug_message_response(
            request,
            heading="Link inválido",
            message="Use 3-30 caracteres: letras minúsculas, números e hífen.",
            status_code=400,
        )
    except SlugUnavailableError:
        if _wants_json_response(request):
            return JSONResponse(
                {
                    "ok": False,
                    "error": "slug_unavailable",
                    "message": "Esse endereço já está em uso. Tente uma variação do seu nome ou marca.",
                },
                status_code=409,
            )
        return _slug_message_response(
            request,
            heading="Link indisponível",
            message="Esse endereço já está em uso. Tente uma variação do seu nome ou marca.",
            status_code=409,
        )
    except CardNotFoundError:
        raise HTTPException(404, "Cartao nao encontrado")
    if _wants_json_response(request):
        return JSONResponse(
            {
                "ok": True,
                "slug": new_slug,
                "public_url": f"/{new_slug}",
                "edit_url": f"/edit/{new_slug}",
            }
        )
    redirect_path = f"/edit/{html.escape(new_slug)}" if next == "edit" else f"/{html.escape(new_slug)}"
    return RedirectResponse(redirect_path, status_code=303)
