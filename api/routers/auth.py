from __future__ import annotations

import html
from urllib.parse import quote, quote_plus

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.core.config import get_settings
from api.repositories.json_storage import db_defaults, load
from api.services.auth_service import (
    AuthService,
    RegistrationError,
    AccountExistsError,
    InvalidCredentialsError,
    LoginVerificationRequired,
    TokenInvalidError,
)

router = APIRouter(prefix="/auth", tags=["auth"])
auth_service = AuthService()
settings = get_settings()
APP_ENV = settings.app_env


def _css_href(request: Request) -> str:
    """Retorna o href fingerprintado ou o fallback padrao."""
    return getattr(getattr(request.app, "state", None), "css_href", "/static/card.css")


def _banner(content: str, css_class: str) -> str:
    if not content:
        return ""
    safe = html.escape(content)
    return f"<p class='banner {css_class}'>{safe}</p>"


def _encode(value: str) -> str:
    return quote(value or "", safe="")


def _redirect_back(uid: str, email: str, vanity: str, err: str):
    dest = f"/onboard/{uid}?error={quote_plus(err)}&email={quote_plus(email)}&vanity={quote_plus(vanity)}"
    return RedirectResponse(dest, status_code=303)


@router.get("/forgot", response_class=HTMLResponse)
def forgot_password(request: Request, message: str = "", error: str = ""):
    css = _css_href(request)
    html_doc = f"""
    <!doctype html><html lang='pt-br'><head>
      <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
      <link rel='stylesheet' href='{css}'><title>Recuperar senha</title>
    </head><body><main class='wrap'>
      <h1>Recuperar senha</h1>
      <p>Informe o e-mail cadastrado para receber o link de redefinicao.</p>
      <form method='post' action='/auth/forgot' class='grid'>
        <input name='email' type='email' placeholder='voce@exemplo.com' required>
        <button class='btn'>Enviar link</button>
      </form>
      {_banner(message, "ok")}
      {_banner(error, "bad")}
      <p><a class='muted' href='/login'>Voltar ao login</a></p>
    </main></body></html>
    """
    return HTMLResponse(html_doc)


@router.post("/forgot")
def forgot_password_submit(email: str = Form("")):
    auth_service.issue_password_reset(email)
    return RedirectResponse(
        "/auth/forgot?message=Se%20o%20email%20estiver%20cadastrado,%20enviaremos%20o%20link%20em%20instantes.",
        status_code=303,
    )


@router.get("/reset", response_class=HTMLResponse)
def reset_form(request: Request, token: str = "", error: str = ""):
    meta = auth_service.validate_reset_token(token)
    if not meta:
        css = _css_href(request)
        return HTMLResponse(
            f"""
            <!doctype html><html lang='pt-br'><head>
              <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
              <link rel='stylesheet' href='{css}'><title>Link invalido</title>
            </head><body><main class='wrap'>
              <h1>Link invalido ou expirado</h1>
              <p><a href='/auth/forgot'>Solicitar novamente</a></p>
            </main></body></html>
            """,
            status_code=400,
        )
    css = _css_href(request)
    token_value = html.escape(token or "")
    html_doc = f"""
    <!doctype html><html lang='pt-br'><head>
      <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
      <link rel='stylesheet' href='{css}'><title>Definir nova senha</title>
    </head><body><main class='wrap'>
      <h1>Definir nova senha</h1>
      <form method='post' action='/auth/reset' class='grid'>
        <input type='hidden' name='token' value='{token_value}'>
        <label>Nova senha</label>
        <input name='password' type='password' minlength='8' required>
        <label>Confirme a senha</label>
        <input name='confirm' type='password' minlength='8' required>
        <button class='btn'>Atualizar</button>
      </form>
      {_banner(error, "bad")}
    </main></body></html>
    """
    return HTMLResponse(html_doc)


@router.post("/reset")
def reset_password(request: Request, token: str = Form(""), password: str = Form(""), confirm: str = Form("")):
    validation_error = None
    if len(password or "") < 8:
        validation_error = "Senha deve ter no minimo 8 caracteres."
    elif password != confirm:
        validation_error = "As senhas nao conferem."
    if validation_error:
        token_param = _encode(token)
        return RedirectResponse(
            f"/auth/reset?token={token_param}&error={_encode(validation_error)}",
            status_code=303,
        )
    email = auth_service.reset_password(token, password)
    if not email:
        return RedirectResponse("/auth/forgot?error=Link%20invalido%20ou%20expirado.", status_code=303)
    css = _css_href(request)
    html_doc = f"""
    <!doctype html><html lang='pt-br'><head>
      <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
      <link rel='stylesheet' href='{css}'><title>Senha atualizada</title>
    </head><body><main class='wrap'>
      <h1>Senha atualizada</h1>
      <p>Senha redefinida com sucesso para <b>{html.escape(email)}</b>.</p>
      <p><a class='btn' href='/login'>Voltar ao login</a></p>
    </main></body></html>
    """
    return HTMLResponse(html_doc)


@router.get("/check_email")
def check_email(value: str = ""):
    """
    Valida se um e-mail já está cadastrado. Reaproveita o mesmo caminho utilizado no front.
    """
    db = db_defaults(load())
    normalized = (value or "").strip().lower()
    available = bool(normalized) and normalized not in db.get("users", {})
    return {"available": available}


@router.post("/register")
def register(
    request: Request,
    uid: str = Form(...),
    email: str = Form(...),
    pin: str = Form(...),
    password: str = Form(...),
    vanity: str = Form(""),
    lgpd: str = Form(None),
):
    try:
        result = auth_service.register(uid, email, pin, password, vanity, accepted_terms=bool(lgpd))
    except AccountExistsError:
        return RedirectResponse(f"/login?uid={uid}&error=Conta%20ja%20existe", status_code=303)
    except RegistrationError as exc:
        return _redirect_back(uid, email, vanity or "", exc.message)
    css = _css_href(request)
    dev_hint = ""
    if APP_ENV != "prod":
        dev_hint = f"<p class='muted'>Ambiente de desenvolvimento: você também pode confirmar clicando <a class='btn' href='{html.escape(result.verify_path)}'>aqui</a>.</p>"
    delivery = (
        f"<p>Enviamos um link de verificação para <b>{html.escape(result.email)}</b>. Confira sua caixa de entrada.</p>"
        if result.email_sent
        else "<p class='banner bad'>Não foi possível enviar o e-mail de confirmação automaticamente. Use o link abaixo para confirmar:</p>"
    )
    manual_link = "" if result.email_sent else f"<p><a class='btn' href='{html.escape(result.verify_path)}'>Confirmar email</a></p>"
    html_doc = f"""
    <!doctype html><html lang='pt-br'><head>
      <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
      <link rel='stylesheet' href='{css}'><title>Confirme seu email</title>
    </head><body><main class='wrap'>
      <h1>Confirme seu email</h1>
      {delivery}
      {dev_hint}
      {manual_link}
      <p>Depois de confirmar, voce sera direcionado ao cartao <code>/{html.escape(result.dest_slug)}</code>.</p>
    </main></body></html>
    """
    return HTMLResponse(html_doc)


@router.post("/login")
def do_login(request: Request, uid: str = Form(""), email: str = Form(...), password: str = Form(...)):
    try:
        outcome = auth_service.login(uid, email, password)
    except InvalidCredentialsError:
        return RedirectResponse(f"/login?uid={uid}&error=Credenciais%20invalidas", status_code=303)
    if isinstance(outcome, LoginVerificationRequired):
        css = _css_href(request)
        html_doc = f"""
        <!doctype html><html lang='pt-br'><head>
        <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <link rel='stylesheet' href='{css}'><title>Confirme seu email</title></head>
        <body><main class='wrap'>
          <h1>Confirme seu email</h1>
          <p class='muted'>Ainda não identificamos a confirmação do seu endereço.</p>
          <p>Reenviamos o link de verificação para <b>{html.escape(outcome.email)}</b>.</p>
          {("<p><a class='btn' href='" + html.escape(outcome.verify_path) + "'>Confirmar agora</a></p>" if APP_ENV != "prod" else "<p>Verifique sua caixa de entrada para continuar.</p>")}
        </main></body></html>
        """
        return HTMLResponse(html_doc)
    dest = f"/{outcome.target_slug}" if outcome.target_slug else "/"
    resp = RedirectResponse(dest, status_code=303)
    resp.set_cookie("session", outcome.session_token, httponly=True, samesite="lax")
    return resp


@router.get("/verify")
def verify_email(token: str):
    try:
        result = auth_service.verify_email(token)
    except TokenInvalidError as exc:
        return HTMLResponse(str(exc), status_code=400)
    dest = f"/{result.target_slug}" if result.target_slug else "/"
    resp = RedirectResponse(dest, status_code=303)
    resp.set_cookie("session", result.session_token, httponly=True, samesite="lax")
    return resp


@router.api_route("/logout", methods=["GET", "POST"])
def logout(request: Request, next: str = "/"):
    auth_service.logout(request.cookies.get("session"))
    dest = next or request.headers.get("referer") or "/"
    if not isinstance(dest, str) or not dest.startswith("/"):
        dest = "/"
    resp = RedirectResponse(dest, status_code=303)
    resp.delete_cookie("session")
    return resp
