"""
Authentication and identity related use cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import secrets
import time

from api.core.config import get_settings
from api.core.mailer import send_email
from api.core.security import hash_password, verify_password
from api.core.utils import absolute_url
from api.domain.slugs import is_valid_slug, slug_in_use
from api.repositories.json_storage import load, save, db_defaults
from api.services.session_service import issue_session


class AuthError(Exception):
    """Base class for authentication-related exceptions."""


class RegistrationError(AuthError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class AccountExistsError(AuthError):
    pass


class InvalidCredentialsError(AuthError):
    pass


class TokenInvalidError(AuthError):
    pass


@dataclass
class RegisterResult:
    email: str
    verify_path: str
    verify_url: str
    dest_slug: str
    email_sent: bool


@dataclass
class LoginSuccess:
    email: str
    session_token: str
    target_slug: Optional[str]


@dataclass
class LoginVerificationRequired:
    email: str
    verify_path: str
    verify_url: str
    email_sent: bool


@dataclass
class VerifyResult:
    email: str
    session_token: str
    target_slug: Optional[str]


@dataclass
class AuthService:
    """Handles registration, login, verification and password reset flows."""

    def __post_init__(self):
        self.settings = get_settings()

    # -------------------------------------- helpers --------------------------------------
    def _now(self) -> int:
        return int(time.time())

    def _token_expired(self, created_at: int | None, now: int) -> bool:
        ttl = self.settings.email_verification_ttl_seconds
        if ttl <= 0:
            return False
        created = int(created_at or 0)
        if not created:
            return True
        return (created + ttl) < now

    def _resolve_target_slug(self, db: dict, email: str, uid_hint: Optional[str]) -> Optional[str]:
        cards = db.get("cards", {})
        if uid_hint and uid_hint in cards:
            return cards[uid_hint].get("vanity", uid_hint)
        for uid, card in cards.items():
            if card.get("user") == email:
                return card.get("vanity", uid)
        return None

    def _ensure_verify_token(self, db: dict, email: str, force_new: bool = False) -> tuple[str, bool]:
        tokens = db.setdefault("verify_tokens", {})
        now = self._now()
        for token, meta in list(tokens.items()):
            if self._token_expired(meta.get("created_at"), now):
                tokens.pop(token, None)
                continue
            if not force_new and meta.get("email") == email:
                return token, False
        token = secrets.token_urlsafe(24)
        tokens[token] = {"email": email, "created_at": now}
        return token, True

    def _verify_email_html(self, verify_url: str, prompt: str) -> str:
        return f"""
        <p>Olá!</p>
        <p>{prompt}</p>
        <p><a href="{verify_url}" style="background:#0ea5e9;color:#fff;padding:12px 18px;border-radius:8px;text-decoration:none;">Confirmar meu e-mail</a></p>
        <p>Se o botão não funcionar, copie e cole este link no navegador:</p>
        <p><a href="{verify_url}">{verify_url}</a></p>
        <p>Equipe Soomei</p>
        """

    # -------------------------------------- registro --------------------------------------
    def register(self, uid: str, email: str, pin: str, password: str, vanity: str = "", accepted_terms: bool = False) -> RegisterResult:
        if not accepted_terms:
            raise RegistrationError("E necessario aceitar os termos")
        raw_email = (email or "").strip()
        if not raw_email:
            raise RegistrationError("Email obrigatorio")
        db = db_defaults(load())
        vanity_value = (vanity or "").strip()
        if vanity_value:
            if not is_valid_slug(vanity_value):
                raise RegistrationError("Slug invalido. Use 3-30 caracteres [a-z0-9-]")
            if slug_in_use(db, vanity_value):
                raise RegistrationError("Slug indisponivel, tente outro")
        card = db.get("cards", {}).get(uid)
        if not card:
            raise RegistrationError("Cartao nao encontrado para ativacao")
        if pin != card.get("pin", "123456"):
            raise RegistrationError("PIN incorreto. Verifique e tente novamente")
        if len(password or "") < 8:
            raise RegistrationError("Senha muito curta. Use no minimo 8 caracteres")
        if raw_email in db.get("users", {}):
            raise AccountExistsError("Conta ja existe")

        db.setdefault("users", {})[raw_email] = {"email": raw_email, "pwd": hash_password(password), "email_verified_at": None}
        card.update({"status": "active", "billing_status": "ok", "user": raw_email})
        if vanity_value:
            card["vanity"] = vanity_value
        db.setdefault("cards", {})[uid] = card
        db.setdefault("profiles", {})[raw_email] = {
            "full_name": "",
            "title": "",
            "links": [],
            "whatsapp": "",
            "pix_key": "",
            "email_public": "",
            "site_url": "",
            "photo_url": "",
            "cover_url": "",
        }
        token, _ = self._ensure_verify_token(db, raw_email, force_new=True)
        save(db)
        verify_path = f"/auth/verify?token={token}"
        verify_url = absolute_url(verify_path)
        dest_slug = card.get("vanity", uid)
        email_sent = send_email(
            "Confirme seu e-mail - Soomei",
            raw_email,
            self._verify_email_html(verify_url, "Use o botão abaixo para confirmar seu e-mail e ativar o seu cartão digital:"),
            f"Olá! Confirme seu e-mail acessando: {verify_url}",
        )
        return RegisterResult(email=raw_email, verify_path=verify_path, verify_url=verify_url, dest_slug=dest_slug, email_sent=email_sent)

    # -------------------------------------- login --------------------------------------
    def login(self, uid_hint: str, email: str, password: str) -> LoginSuccess | LoginVerificationRequired:
        raw_email = (email or "").strip()
        if not raw_email:
            raise InvalidCredentialsError("Credenciais invalidas")
        db = db_defaults(load())
        user = db.get("users", {}).get(raw_email)
        if not user or not verify_password(password, user.get("pwd")):
            raise InvalidCredentialsError("Credenciais invalidas")
        if user.get("pwd") and not str(user.get("pwd", "")).startswith("argon2$"):
            user["pwd"] = hash_password(password)
            db.setdefault("users", {})[raw_email] = user
            save(db)

        if not user.get("email_verified_at"):
            token, created = self._ensure_verify_token(db, raw_email)
            save(db)
            verify_path = f"/auth/verify?token={token}"
            verify_url = absolute_url(verify_path)
            email_sent = False
            if created:
                email_sent = send_email(
                    "Confirme seu e-mail - Soomei",
                    raw_email,
                    self._verify_email_html(verify_url, "Para concluir seu acesso, confirme seu e-mail clicando no botão abaixo:"),
                    f"Confirme seu e-mail: {verify_url}",
                )
            return LoginVerificationRequired(email=raw_email, verify_path=verify_path, verify_url=verify_url, email_sent=email_sent)

        target = self._resolve_target_slug(db, raw_email, uid_hint)
        token = issue_session(db, raw_email)
        return LoginSuccess(email=raw_email, session_token=token, target_slug=target)

    # -------------------------------------- verificação --------------------------------------
    def verify_email(self, token: str) -> VerifyResult:
        token_value = (token or "").strip()
        if not token_value:
            raise TokenInvalidError("Token invalido ou expirado.")
        db = db_defaults(load())
        meta = db.get("verify_tokens", {}).pop(token_value, None)
        if not meta:
            raise TokenInvalidError("Token invalido ou expirado.")
        if self._token_expired(meta.get("created_at"), self._now()):
            save(db)
            raise TokenInvalidError("Token invalido ou expirado.")
        email = meta.get("email")
        if not email or email not in db.get("users", {}):
            save(db)
            raise TokenInvalidError("Usuario nao encontrado para este token.")
        db["users"][email]["email_verified_at"] = self._now()
        save(db)
        target = self._resolve_target_slug(db, email, None)
        session_token = issue_session(db, email)
        return VerifyResult(email=email, session_token=session_token, target_slug=target)

    def logout(self, session_token: Optional[str]):
        if not session_token:
            return
        db = db_defaults(load())
        sessions = db.get("sessions", {})
        if session_token in sessions:
            sessions.pop(session_token, None)
            save(db)

    # -------------------------------------- reset de senha --------------------------------------
    def issue_password_reset(self, email: str) -> bool:
        addr = (email or "").strip().lower()
        if not addr:
            return False
        db = db_defaults(load())
        users = {k.lower(): k for k in db.get("users", {}).keys()}
        if addr not in users:
            return False
        token = secrets.token_urlsafe(32)
        db.setdefault("reset_tokens", {})[token] = {"email": users[addr], "created_at": self._now()}
        save(db)
        reset_url = absolute_url(f"/auth/reset?token={token}")
        html_body = f"""
        <p>Olá!</p>
        <p>Recebemos um pedido para redefinir sua senha.</p>
        <p><a href="{reset_url}" style="background:#0ea5e9;color:#fff;padding:12px 18px;border-radius:8px;text-decoration:none;">Redefinir senha</a></p>
        <p>Se não foi você, ignore esta mensagem.</p>
        """
        send_email("Redefina sua senha - Soomei", users[addr], html_body, f"Use este link para redefinir sua senha: {reset_url}")
        return True

    def validate_reset_token(self, token: str) -> dict | None:
        token = (token or "").strip()
        if not token:
            return None
        db = db_defaults(load())
        meta = db.get("reset_tokens", {}).get(token)
        if not meta:
            return None
        created = int(meta.get("created_at") or 0)
        if created < self._now() - self.settings.password_reset_ttl:
            db["reset_tokens"].pop(token, None)
            save(db)
            return None
        return {"token": token, "email": meta.get("email", "")}

    def reset_password(self, token: str, password: str) -> str | None:
        token = (token or "").strip()
        db = db_defaults(load())
        meta = db.get("reset_tokens", {}).get(token)
        if not meta:
            return None
        created = int(meta.get("created_at") or 0)
        if created < self._now() - self.settings.password_reset_ttl:
            db["reset_tokens"].pop(token, None)
            save(db)
            return None
        email = (meta.get("email") or "").strip()
        user = db.get("users", {}).get(email)
        if not user:
            db["reset_tokens"].pop(token, None)
            save(db)
            return None
        user["pwd"] = hash_password(password)
        db["users"][email] = user
        db["reset_tokens"].pop(token, None)
        save(db)
        return email
