from __future__ import annotations

import html
from urllib.parse import quote, quote_plus

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.core import csrf
from api.core.config import get_settings
from api.core.rate_limiter import rate_limit_ip
from api.repositories.sql_repository import SQLRepository
from api.services.auth_service import (
    AuthService,
    RegistrationError,
    AccountExistsError,
    InvalidCredentialsError,
    LoginVerificationRequired,
    TokenInvalidError,
)
from api.services.session_service import clear_session_cookie, current_user_email, set_session_cookie

router = APIRouter(prefix="/auth", tags=["auth"])
auth_service = AuthService()
settings = get_settings()
APP_ENV = settings.app_env
_sql_repo = SQLRepository()


def _css_href(request: Request) -> str:
    """Retorna o href fingerprintado ou o fallback padrao."""
    return getattr(getattr(request.app, "state", None), "css_href", "/static/card.css")


def _templates(request: Request):
    tpl = getattr(getattr(request.app, "state", None), "templates", None)
    if tpl:
        return tpl
    raise RuntimeError("Templates nao configurados")


def _brand_footer(html_doc: str) -> str:
    snippet = (
        "\n    <div class='edit-footer soomei-footer-mark'>\n"
        "      <a class='soomei-watermark' href='https://soomei.cc' target='_blank' rel='noopener' aria-label='Soomei'>\n"
        "        <span class='soomei-watermark__brand'>Soomei</span>\n"
        "        <span class='soomei-watermark__text'>cartão digital</span>\n"
        "      </a>\n"
        "    </div>\n  "
    )
    return html_doc.replace("</main>", snippet + "</main>", 1) if "</main>" in html_doc else (html_doc + snippet)


def _confirm_email_page(request: Request, *, title: str, heading: str, body_html: str, extra_context: dict | None = None):
    templates = _templates(request)
    token = csrf.ensure_csrf_token(request)
    context = {
        "request": request,
        "title": title,
        "heading": heading,
        "body_html": body_html,
        "csrf_token": token,
        "app_env": APP_ENV,
        "dev_verify_path": "",
    }
    if extra_context:
        context.update(extra_context)
    response = templates.TemplateResponse("confirm_email.html", context)
    csrf.set_csrf_cookie(response, token)
    return response


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


def _auth_message_response(
    request: Request,
    *,
    title: str,
    kicker: str,
    heading: str,
    message: str,
    primary_href: str = "/login",
    primary_label: str = "Voltar ao login",
    status_code: int = 200,
) -> HTMLResponse:
    css = _css_href(request)
    html_doc = f"""
    <!doctype html><html lang='pt-br'><head>
      <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
      <link rel='stylesheet' href='{css}'><title>{html.escape(title)}</title>
    </head><body>
      <main class='wrap'>
        <section class='auth-shell'>
          <div class='auth-card carbon'>
            <div class='auth-glow' aria-hidden='true'></div>
            <div class='auth-brand'>
              <img src='/static/img/soomei_logo.png' alt='Soomei' class='auth-logo'>
              <span>Soomei</span>
            </div>
            <p class='auth-kicker'>{html.escape(kicker)}</p>
            <h1>{html.escape(heading)}</h1>
            <p class='auth-intro'>{html.escape(message)}</p>
            <div class='auth-actions'>
              <a class='btn auth-submit' href='{html.escape(primary_href)}'>{html.escape(primary_label)}</a>
            </div>
          </div>
        </section>
      </main>
    </body></html>
    """
    return HTMLResponse(_brand_footer(html_doc), status_code=status_code)


@router.get("/forgot", response_class=HTMLResponse)
def forgot_password(request: Request, message: str = "", error: str = ""):
    css = _css_href(request)
    token = csrf.ensure_csrf_token(request)
    token_html = html.escape(token)
    html_doc = f"""
    <!doctype html><html lang='pt-br'><head>
      <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
      <link rel='stylesheet' href='{css}'><title>Recuperar senha</title>
    </head><body><main class='wrap'>
      <section class='auth-shell'>
        <div class='auth-card carbon'>
          <div class='auth-glow' aria-hidden='true'></div>
          <div class='auth-brand'>
            <img src='/static/img/soomei_logo.png' alt='Soomei' class='auth-logo'>
            <span>Soomei</span>
          </div>
          <div class='auth-support'>
            <p class='auth-kicker'>Recuperação segura</p>
            </div>
          <h1>Recuperar senha</h1>
          <p class='auth-intro'>Informe o e-mail cadastrado e enviaremos um link para você criar uma nova senha com segurança.</p>
          {_banner(message, "ok")}
          {_banner(error, "bad")}
          <form method='post' action='/auth/forgot' class='auth-form'>
            <input type='hidden' name='csrf_token' value='{token_html}'>
            <div class='auth-field'>
              <label for='forgotEmail'>E-mail cadastrado</label>
              <input id='forgotEmail' name='email' type='email' placeholder='voce@empresa.com' required autocomplete='email'>
            </div>
            <button class='btn auth-submit'>Enviar link de redefinição</button>
          </form>
          <div class='auth-support'>
            <a href='/login'>Voltar ao login</a>
          </div>
        </div>
      </section>
    </main></body></html>
    """
    response = HTMLResponse(_brand_footer(html_doc))
    csrf.set_csrf_cookie(response, token)
    return response


@router.post("/forgot")
def forgot_password_submit(request: Request, email: str = Form(""), csrf_token: str = Form("")):
    rate_limit_ip(request, "auth:forgot", limit=5, window_seconds=300)
    csrf.validate_csrf(request, csrf_token)
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
        html_doc = f"""
        <!doctype html><html lang='pt-br'><head>
          <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
          <link rel='stylesheet' href='{css}'><title>Link inválido</title>
        </head><body>
          <main class='wrap'>
            <section class='auth-shell'>
              <div class='auth-card carbon'>
                <div class='auth-glow' aria-hidden='true'></div>
                <div class='auth-brand'>
                  <img src='/static/img/soomei_logo.png' alt='Soomei' class='auth-logo'>
                  <span>Soomei</span>
                </div>
                <div class='auth-support'>
                <p class='auth-kicker'>Link expirado</p>
                </div>
                <h1>Link inválido ou expirado</h1>
                <p class='auth-intro'>Por segurança, links de redefinição têm validade limitada. Solicite um novo link para continuar.</p>
                <div class='auth-actions'>
                  <a class='btn auth-submit' href='/auth/forgot'>Solicitar novamente</a>
                  <a class='btn auth-secondary' href='/login'>Voltar ao login</a>
                </div>
              </div>
            </section>
          </main>
        </body></html>
        """
        return HTMLResponse(_brand_footer(html_doc), status_code=400)
    css = _css_href(request)
    csrf_token = csrf.ensure_csrf_token(request)
    token_value = html.escape(token or "")
    csrf_html = html.escape(csrf_token)
    html_doc = f"""
    <!doctype html><html lang='pt-br'><head>
      <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
      <link rel='stylesheet' href='{css}'><title>Definir nova senha</title>
    </head><body>
      <main class='wrap'>
        <section class='auth-shell'>
          <div class='auth-card carbon'>
            <div class='auth-glow' aria-hidden='true'></div>
            <div class='auth-brand'>
              <img src='/static/img/soomei_logo.png' alt='Soomei' class='auth-logo'>
              <span>Soomei</span>
            </div>
            <p class='auth-kicker'>Nova credencial</p>
            <h1>Definir nova senha</h1>
            <p class='auth-intro'>Crie uma senha com pelo menos 8 caracteres para continuar acessando sua conta.</p>
            {_banner(error, "bad")}
            <form method='post' action='/auth/reset' class='auth-form'>
              <input type='hidden' name='token' value='{token_value}'>
              <input type='hidden' name='csrf_token' value='{csrf_html}'>
              <div class='auth-field'>
                <label for='newPassword'>Nova senha</label>
                <input id='newPassword' name='password' type='password' minlength='8' required autocomplete='new-password' placeholder='Mínimo de 8 caracteres'>
              </div>
              <div class='auth-field'>
                <label for='confirmPassword'>Confirme a senha</label>
                <input id='confirmPassword' name='confirm' type='password' minlength='8' required autocomplete='new-password' placeholder='Repita a nova senha'>
              </div>
              <button class='btn auth-submit'>Atualizar senha</button>
            </form>
            <div class='auth-support'>
              <a href='/auth/forgot'>Solicitar outro link</a>
            </div>
          </div>
        </section>
      </main>
    </body></html>
    """
    response = HTMLResponse(_brand_footer(html_doc))
    csrf.set_csrf_cookie(response, csrf_token)
    return response


@router.post("/reset")
def reset_password(request: Request, token: str = Form(""), password: str = Form(""), confirm: str = Form(""), csrf_token: str = Form("")):
    csrf.validate_csrf(request, csrf_token)
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
    </head><body>
      <main class='wrap'>
        <section class='auth-shell'>
          <div class='auth-card carbon'>
            <div class='auth-glow' aria-hidden='true'></div>
            <div class='auth-brand'>
              <img src='/static/img/soomei_logo.png' alt='Soomei' class='auth-logo'>
              <span>Soomei</span>
            </div>
            <p class='auth-kicker'>Tudo certo</p>
            <h1>Senha atualizada</h1>
            <p class='auth-intro'>Senha redefinida com sucesso para <b>{html.escape(email)}</b>. Agora você já pode entrar novamente.</p>
            <div class='auth-actions'>
              <a class='btn auth-submit' href='/login'>Voltar ao login</a>
            </div>
          </div>
        </section>
      </main>
    </body></html>
    """
    response = HTMLResponse(_brand_footer(html_doc))
    csrf.set_csrf_cookie(response, csrf.ensure_csrf_token(request))
    return response


@router.get("/check_email")
def check_email(value: str = ""):
    """
    Valida se um e-mail já está cadastrado. Reaproveita o mesmo caminho utilizado no front.
    """
    email_value = (value or "").strip().lower()
    if not email_value or "@" not in email_value:
        return {"available": False, "reason": "invalid"}
    return {"available": not _sql_repo.email_exists(email_value)}


@router.post("/register")
def register(
    request: Request,
    uid: str = Form(...),
    email: str = Form(...),
    pin: str = Form(...),
    password: str = Form(...),
    vanity: str = Form(""),
    lgpd: str = Form(None),
    csrf_token: str = Form(""),
):
    rate_limit_ip(request, "auth:register", limit=3, window_seconds=300)
    csrf.validate_csrf(request, csrf_token)
    try:
        result = auth_service.register(uid, email, pin, password, vanity, accepted_terms=bool(lgpd))
    except AccountExistsError:
        return _redirect_back(
            uid,
            email,
            vanity or "",
            "Este e-mail ja esta cadastrado. Use outro e-mail ou entre na conta existente.",
        )
    except RegistrationError as exc:
        return _redirect_back(uid, email, vanity or "", exc.message)
    css = _css_href(request)
    dev_hint = ""
    if APP_ENV != "prod":
        dev_hint = (
            f"<p class='muted'>Ambiente de desenvolvimento: você também pode confirmar clicando <a class='btn' href='{html.escape(result.verify_path)}'>aqui</a>.</p>"
        )
    delivery = (
        f"<p>Enviamos um link de verificação para <b>{html.escape(result.email)}</b>. Confira sua caixa de entrada.</p>"
        if result.email_sent
        else "<p class='banner bad'>Não foi possível enviar o e-mail de confirmação automaticamente. Use o link abaixo para confirmar:</p>"
    )
    manual_link = "" if result.email_sent else f"<p><a class='btn' href='{html.escape(result.verify_path)}'>Confirmar e-mail</a></p>"
    body_html = (
        delivery
        + dev_hint
        + manual_link
        + f"<p>Depois de confirmar, você será direcionado ao cartão <code>/{html.escape(result.dest_slug)}</code>.</p>"
    )
    return _confirm_email_page(
        request,
        title="Confirme seu e-mail",
        heading="Confirme seu e-mail",
        body_html=body_html,
        extra_context={"uid": result.uid, "email": result.email, "dev_verify_path": result.verify_path},
    )


@router.post("/login")
def do_login(request: Request, uid: str = Form(""), email: str = Form(...), password: str = Form(...), csrf_token: str = Form("")):
    rate_limit_ip(request, "auth:login", limit=5, window_seconds=60)
    csrf.validate_csrf(request, csrf_token)
    try:
        outcome = auth_service.login(uid, email, password)
    except InvalidCredentialsError:
        return RedirectResponse(f"/login?uid={uid}&error=Credenciais%20invalidas", status_code=303)
    if isinstance(outcome, LoginVerificationRequired):
        manual = (
            f"<p><a class='btn' href='{html.escape(outcome.verify_path)}'>Confirmar agora</a></p>"
            if APP_ENV != "prod"
            else "<p>Verifique sua caixa de entrada para continuar.</p>"
        )
        delivery = (
            f"<p>Reenviamos o link de verificação para <b>{html.escape(outcome.email)}</b>.</p>"
            if outcome.email_sent
            else f"<p>Já enviamos recentemente um link para <b>{html.escape(outcome.email)}</b>. Verifique sua caixa de entrada (e o spam) ou aguarde alguns minutos antes de solicitar novamente.</p>"
        )
        body_html = "<p class='muted'>Ainda não identificamos a confirmação do seu endereço.</p>" + delivery + manual
        return _confirm_email_page(
            request,
            title="Confirme seu e-mail",
            heading="Confirme seu e-mail",
            body_html=body_html,
            extra_context={"email": outcome.email, "dev_verify_path": outcome.verify_path},
        )
    dest = f"/{outcome.target_slug}" if outcome.target_slug else "/"
    resp = RedirectResponse(dest, status_code=303)
    set_session_cookie(resp, outcome.session_token)
    csrf.set_csrf_cookie(resp, csrf.ensure_csrf_token(request))
    return resp


@router.get("/verify")
def verify_email(request: Request, token: str):
    rate_limit_ip(request, "auth:verify", limit=10, window_seconds=300)
    try:
        result = auth_service.verify_email(token)
    except TokenInvalidError as exc:
        return _auth_message_response(
            request,
            title="Token inválido",
            kicker="Verificação expirada",
            heading="Link inválido ou expirado",
            message=str(exc) or "Solicite um novo link para confirmar seu e-mail.",
            primary_href="/login",
            primary_label="Voltar ao login",
            status_code=400,
        )
    dest = f"/{result.target_slug}" if result.target_slug else "/"
    resp = RedirectResponse(dest, status_code=303)
    set_session_cookie(resp, result.session_token)
    csrf_token = csrf.ensure_csrf_token(request)
    csrf.set_csrf_cookie(resp, csrf_token)
    return resp


@router.get("/pending", response_class=HTMLResponse)
def pending(request: Request, uid: str = "", pin: str = ""):
    uid_value = (uid or "").strip()
    pin_value = (pin or "").strip()
    cookie_pin = None
    raw_cookie = request.cookies.get("pending_pin") or ""
    if raw_cookie and ":" in raw_cookie:
        cookie_uid, cookie_pin_val = raw_cookie.split(":", 1)
        if cookie_uid == uid_value:
            cookie_pin = cookie_pin_val
    if not pin_value:
        pin_value = cookie_pin or ""
    card = _sql_repo.get_card_by_uid(uid_value)
    if not card:
        resp = RedirectResponse("/invalid", status_code=302)
        resp.delete_cookie("pending_pin", path="/auth")
        return resp
    if str(card.pin or "").strip() != pin_value:
        resp = RedirectResponse(f"/onboard/{html.escape(uid_value)}/pin?error=PIN%20incorreto", status_code=303)
        resp.delete_cookie("pending_pin", path="/auth")
        return resp
    owner = (card.owner_email or "").strip()
    if not owner:
        resp = RedirectResponse(f"/onboard/{html.escape(uid_value)}", status_code=303)
        resp.delete_cookie("pending_pin", path="/auth")
        return resp
    user = _sql_repo.get_user(owner)
    if not user:
        # Garante que o token possa ser validado mesmo que o usuario ainda nao exista (ex.: migracao/cadastro incompleto)
        _sql_repo.upsert_user(owner, password_hash="")
        user = _sql_repo.get_user(owner)
    if user and user.email_verified_at:
        resp = RedirectResponse(f"/{html.escape(card.vanity or uid_value)}", status_code=303)
        resp.delete_cookie("pending_pin", path="/auth")
        return resp
    email_sent = auth_service.resend_verification(owner)
    verify_token, _created = auth_service._ensure_verify_token(owner, force_new=False)  # reutiliza ou cria token
    verify_path = f"/auth/verify?token={verify_token}"
    dest_slug = card.vanity or uid_value
    dev_hint = ""
    if APP_ENV != "prod":
        dev_hint = (
            f"<p class='muted'>Ambiente de desenvolvimento: você também pode confirmar clicando <a class='btn' href='{html.escape(verify_path)}'>aqui</a>.</p>"
        )
    delivery = (
        f"<p>Enviamos um link de verificação para <b>{html.escape(owner)}</b>. Confira sua caixa de entrada.</p>"
        if email_sent
        else "<p class='banner bad'>Não foi possível enviar o e-mail de confirmação automaticamente. Use o link abaixo para confirmar:</p>"
    )
    manual_link = "" if email_sent else f"<p><a class='btn' href='{html.escape(verify_path)}'>Confirmar e-mail</a></p>"
    body_html = (
        delivery
        + dev_hint
        + manual_link
        + f"<p>Depois de confirmar, você será direcionado ao cartão <code>/{html.escape(dest_slug)}</code>.</p>"
    )
    response = _confirm_email_page(
        request,
        title="Confirme seu e-mail",
        heading="Confirme seu e-mail",
        body_html=body_html,
        extra_context={"uid": uid_value, "email": owner, "dev_verify_path": verify_path},
    )
    response.delete_cookie("pending_pin", path="/auth")
    return response


@router.post("/logout")
def logout(request: Request, next: str = Form(None), csrf_token: str = Form("")):
    csrf.validate_csrf(request, csrf_token)
    auth_service.logout(request.cookies.get("session"))
    dest = (next or "").strip()
    if not dest or dest == "/":
        ref = request.headers.get("referer") or ""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(ref)
            dest = (parsed.path or "").strip() or "/login"
        except Exception:
            dest = "/login"
    if not isinstance(dest, str) or not dest.startswith("/"):
        dest = "/login"
    resp = RedirectResponse(dest, status_code=303)
    clear_session_cookie(resp)
    return resp


@router.post("/resend_verify")
def resend_verify(request: Request, email: str = Form(""), csrf_token: str = Form("")):
    rate_limit_ip(request, "auth:resend-verify", limit=5, window_seconds=300)
    csrf.validate_csrf(request, csrf_token)
    ok = auth_service.resend_verification(email)
    return {"ok": ok}


@router.post("/resend_verify_pending")
def resend_verify_pending(request: Request, uid: str = Form(""), pin: str = Form(""), csrf_token: str = Form("")):
    rate_limit_ip(request, "auth:resend-verify-pending", limit=5, window_seconds=300)
    csrf.validate_csrf(request, csrf_token)
    ok = auth_service.resend_verification_for_card(uid, pin)
    return {"ok": ok}


@router.post("/change_email_pending")
def change_email_pending(request: Request, uid: str = Form(""), pin: str = Form(""), new_email: str = Form(""), csrf_token: str = Form("")):
    rate_limit_ip(request, "auth:change-email-pending", limit=5, window_seconds=300)
    csrf.validate_csrf(request, csrf_token)
    updated, verify_path, reason = auth_service.change_pending_email(uid, pin, new_email)
    return {"ok": bool(updated), "email": updated or "", "verify_path": verify_path or "", "reason": reason or ""}
