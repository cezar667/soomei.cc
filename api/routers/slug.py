from __future__ import annotations

import html

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from api.services.slug_service import (
    SlugService,
    InvalidSlugError,
    SlugUnavailableError,
    CardNotFoundError,
)
from api.services.card_service import find_card_by_slug
from api.services.session_service import current_user_email

router = APIRouter(prefix="/slug", tags=["slug"])


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


def _redirect_to_card(card: dict, uid: str) -> RedirectResponse:
    dest = card.get("vanity", uid)
    return RedirectResponse(f"/{html.escape(dest)}", status_code=303)


@router.get("/check")
def slug_check(request: Request, value: str = ""):
    svc = _get_slug_service(request)
    return {"available": svc.is_available(value)}


@router.get("/select/{card_id}", response_class=HTMLResponse)
def slug_select(card_id: str, request: Request):
    _, uid, card = find_card_by_slug(card_id)
    if not card or not uid:
        raise HTTPException(404, "Cartao nao encontrado")
    owner = card.get("user", "")
    who = current_user_email(request)
    if who != owner:
        return _redirect_to_card(card, uid)
    templates = _get_templates(request)
    current = card.get("vanity", "") or ""
    back_slug = card.get("vanity", uid)
    return templates.TemplateResponse(
        "slug_select.html",
        {"request": request, "uid": uid, "current": current, "back_slug": back_slug},
    )


@router.post("/select/{card_id}")
def slug_select_post(card_id: str, request: Request, value: str = Form("")):
    _, uid, card = find_card_by_slug(card_id)
    if not card or not uid:
        raise HTTPException(404, "Cartao nao encontrado")
    owner = card.get("user", "")
    who = current_user_email(request)
    if who != owner:
        return _redirect_to_card(card, uid)
    svc = _get_slug_service(request)
    try:
        new_slug = svc.assign_slug(uid, value)
    except InvalidSlugError:
        return HTMLResponse("Slug invalido. Use 3-30 caracteres [a-z0-9-]", status_code=400)
    except SlugUnavailableError:
        return HTMLResponse("Slug indisponivel, tente outro.", status_code=409)
    except CardNotFoundError:
        raise HTTPException(404, "Cartao nao encontrado")
    return RedirectResponse(f"/{html.escape(new_slug)}", status_code=303)
