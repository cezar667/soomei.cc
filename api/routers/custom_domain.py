from __future__ import annotations

from fastapi import APIRouter, Form, Request, HTTPException
from fastapi.responses import JSONResponse

from api.core import csrf
from api.core.config import get_settings
from api.core.rate_limiter import rate_limit_ip
from api.services.custom_domain_service import (
    CustomDomainService,
    CustomDomainError,
    CustomDomainState,
)
from api.services.session_service import current_user_email

router = APIRouter(prefix="/custom-domain", tags=["custom-domain"])
service = CustomDomainService()
settings = get_settings()


def _feature_disabled() -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": "disabled", "message": "Domínios personalizados ainda não foram habilitados neste ambiente."},
        status_code=403,
    )


def _error_response(err: CustomDomainError) -> JSONResponse:
    return JSONResponse({"ok": False, "error": err.code, "message": err.message}, status_code=err.status_code)


def _ok_response(state: CustomDomainState) -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "status": state.status,
        "requested_host": state.requested_host,
        "active_host": state.active_host,
    })


@router.post("/request/{slug}")
async def request_custom_domain(slug: str, request: Request, host: str = Form(""), csrf_token: str = Form("")):
    if not settings.custom_domains_enabled:
        return _feature_disabled()
    user = current_user_email(request)
    rate_limit_ip(request, "custom-domain:request", limit=5, window_seconds=60)
    csrf.validate_csrf(request, csrf_token)
    try:
        state = service.request(slug, user, host)
    except CustomDomainError as exc:
        if exc.code == "not_found":
            raise HTTPException(404, exc.message)
        return _error_response(exc)
    return _ok_response(state)


@router.post("/withdraw/{slug}")
async def withdraw_custom_domain(slug: str, request: Request, csrf_token: str = Form("")):
    if not settings.custom_domains_enabled:
        return _feature_disabled()
    user = current_user_email(request)
    rate_limit_ip(request, "custom-domain:withdraw", limit=5, window_seconds=60)
    csrf.validate_csrf(request, csrf_token)
    try:
        state = service.withdraw(slug, user)
    except CustomDomainError as exc:
        if exc.code == "not_found":
            raise HTTPException(404, exc.message)
        return _error_response(exc)
    return _ok_response(state)


@router.post("/remove/{slug}")
async def remove_custom_domain(slug: str, request: Request, csrf_token: str = Form("")):
    if not settings.custom_domains_enabled:
        return _feature_disabled()
    user = current_user_email(request)
    rate_limit_ip(request, "custom-domain:remove", limit=5, window_seconds=60)
    csrf.validate_csrf(request, csrf_token)
    try:
        state = service.remove(slug, user)
    except CustomDomainError as exc:
        if exc.code == "not_found":
            raise HTTPException(404, exc.message)
        return _error_response(exc)
    return _ok_response(state)
