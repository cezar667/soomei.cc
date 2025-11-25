"""
Authentication and identity related use cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
import time

from api.core.config import get_settings
from api.core.mailer import send_email
from api.core.security import hash_password, verify_password
from api.core.utils import absolute_url
from api.domain.slugs import is_valid_slug
from api.repositories.sql_repository import SQLRepository
from api.services.session_service import delete_session, issue_session


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
    uid: str
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
        self.repository = SQLRepository()

    # -------------------------------------- helpers --------------------------------------
    def _now(self) -> int:
        return int(time.time())

    def _token_expired(self, created_at: datetime | int | None, now: int, *, ttl_seconds: int | None = None) -> bool:
        ttl = ttl_seconds if ttl_seconds is not None else self.settings.email_verification_ttl_seconds
        if ttl <= 0:
            return False
        created_ts = 0
        if isinstance(created_at, datetime):
            # Normalize aware datetimes to UTC to avoid misreading offsets (ex.: -03:00) as UTC timestamps.
            normalized = created_at.astimezone(timezone.utc) if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
            created_ts = int(normalized.timestamp())
        else:
            created_ts = int(created_at or 0)
        if not created_ts:
            return True
        return (created_ts + ttl) < now

    def _resolve_target_slug(self, email: str, uid_hint: Optional[str]) -> Optional[str]:
        if uid_hint:
            card = self.repository.get_card_by_uid(uid_hint)
            if card and (card.owner_email == email):
                return card.vanity or uid_hint
        for card in self.repository.get_cards_by_owner(email):
            return card.vanity or card.uid
        return None

    def _cleanup_unverified_account(self, email: str) -> None:
        """Remove dados de uma conta não verificada (tokens, sessões, perfil e usuário)."""
        if not email:
            return
        self.repository.delete_verify_tokens_for_email(email)
        self.repository.delete_reset_tokens_for_email(email)
        self.repository.delete_user_sessions(email)
        self.repository.delete_profile(email)
        self.repository.delete_user(email)

    def _ensure_verify_token(self, email: str, force_new: bool = False) -> tuple[str, bool]:
        now = self._now()
        existing = self.repository.get_verify_token_for_email(email)
        if existing and not force_new and not self._token_expired(existing.created_at, now):
            return existing.token, False
        if existing and (force_new or self._token_expired(existing.created_at, now)):
            self.repository.delete_verify_tokens_for_email(email)
        token = self.repository.create_verify_token(email)
        return token, True

    def resend_verification(self, email: str) -> bool:
        email = (email or "").strip()
        if not email:
            return False
        user = self.repository.get_user(email)
        if not user or user.email_verified_at:
            return False
        token, created = self._ensure_verify_token(email, force_new=False)
        verify_path = f"/auth/verify?token={token}"
        verify_url = absolute_url(verify_path)
        send_email(
            "Confirme seu e-mail - Soomei",
            email,
            self._verify_email_html(verify_url, "Para concluir seu acesso, confirme seu e-mail clicando no botão abaixo:"),
            f"Confirme seu e-mail: {verify_url}",
        )
        return True

    def resend_verification_for_card(self, uid: str, pin: str) -> bool:
        card = self.repository.get_card_by_uid(uid)
        if not card or (card.status or "").lower() != "active":
            return False
        if str(pin or "").strip() != str(card.pin or "").strip():
            return False
        owner = (card.owner_email or "").strip()
        if not owner:
            return False
        user = self.repository.get_user(owner)
        if not user or user.email_verified_at:
            return False
        return self.resend_verification(owner)

    def change_pending_email(self, uid: str, pin: str, new_email: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Atualiza o e-mail de um cartao pendente de verificacao.
        Retorna (novo_email, verify_path, erro) onde erro pode indicar motivo especifico.
        """
        card = self.repository.get_card_by_uid(uid)
        if not card or (card.status or "").lower() != "active":
            return None, None, "card_not_found"
        if str(pin or "").strip() != str(card.pin or "").strip():
            return None, None, "invalid_pin"
        owner = (card.owner_email or "").strip()
        if not owner:
            return None, None, "no_owner"
        user = self.repository.get_user(owner)
        if user and user.email_verified_at:
            return None, None, "already_verified"
        new_addr = (new_email or "").strip()
        if not new_addr:
            return None, None, "invalid_email"
        existing_new = self.repository.get_user(new_addr)
        if existing_new and existing_new.email_verified_at:
            return None, None, "email_in_use"
        old_profile = self.repository.get_profile(owner) or {}
        # clean old unverified
        self._cleanup_unverified_account(owner)
        pwd_placeholder = user.password_hash if user else ""
        self.repository.upsert_user(new_addr, password_hash=pwd_placeholder)
        self.repository.assign_card_owner(uid, new_addr, status="active", billing_status=card.billing_status, vanity=card.vanity)
        self.repository.upsert_profile(new_addr, old_profile)
        token, _ = self._ensure_verify_token(new_addr, force_new=True)
        verify_path = f"/auth/verify?token={token}"
        verify_url = absolute_url(verify_path)
        send_email(
            "Confirme seu e-mail - Soomei",
            new_addr,
            self._verify_email_html(verify_url, "Use o botao abaixo para confirmar seu e-mail e ativar o seu cartao digital:"),
            f"Ola! Confirme seu e-mail acessando: {verify_url}",
        )
        return new_addr, verify_path, None

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
        vanity_value = (vanity or "").strip()
        if vanity_value:
            if not is_valid_slug(vanity_value):
                raise RegistrationError("Slug invalido. Use 3-30 caracteres [a-z0-9-]")
            if self.repository.slug_exists(vanity_value):
                raise RegistrationError("Slug indisponivel, tente outro")
        card_entity = self.repository.get_card_by_uid(uid)
        if not card_entity:
            raise RegistrationError("Cartao nao encontrado para ativacao")
        if pin != (card_entity.pin or "123456"):
            raise RegistrationError("PIN incorreto. Verifique e tente novamente")
        if len(password or "") < 8:
            raise RegistrationError("Senha muito curta. Use no minimo 8 caracteres")
        existing_owner = (card_entity.owner_email or "").strip()
        existing_user = self.repository.get_user(raw_email)
        if existing_user and existing_user.email_verified_at:
            # Conta já existe e verificada
            raise AccountExistsError("Conta ja existe")
        if existing_owner and existing_owner != raw_email:
            owner_user = self.repository.get_user(existing_owner)
            if owner_user and owner_user.email_verified_at:
                raise AccountExistsError("Cartao ja foi ativado com outro email.")
            # Limpa dono anterior não verificado para permitir correção de e-mail
            self._cleanup_unverified_account(existing_owner)

        password_hash = hash_password(password)
        self.repository.upsert_user(raw_email, password_hash=password_hash)
        self.repository.assign_card_owner(uid, raw_email, status="active", billing_status="ok", vanity=vanity_value or None)
        self.repository.upsert_profile(
            raw_email,
            {"full_name": "", "title": "", "links": [], "whatsapp": "", "pix_key": "", "email_public": "", "site_url": "", "photo_url": "", "cover_url": ""},
        )
        token, _ = self._ensure_verify_token(raw_email, force_new=True)
        verify_path = f"/auth/verify?token={token}"
        verify_url = absolute_url(verify_path)
        dest_slug = vanity_value or uid
        email_sent = send_email(
            "Confirme seu e-mail - Soomei",
            raw_email,
            self._verify_email_html(verify_url, "Use o botão abaixo para confirmar seu e-mail e ativar o seu cartão digital:"),
            f"Olá! Confirme seu e-mail acessando: {verify_url}",
        )
        return RegisterResult(uid=uid, email=raw_email, verify_path=verify_path, verify_url=verify_url, dest_slug=dest_slug, email_sent=email_sent)

    # -------------------------------------- login --------------------------------------
    def login(self, uid_hint: str, email: str, password: str) -> LoginSuccess | LoginVerificationRequired:
        raw_email = (email or "").strip()
        if not raw_email:
            raise InvalidCredentialsError("Credenciais invalidas")
        user = self.repository.get_user(raw_email)
        if not user or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError("Credenciais invalidas")
        if user.password_hash and not str(user.password_hash).startswith("argon2$"):
            new_hash = hash_password(password)
            self.repository.update_user_password(raw_email, new_hash)

        if not user.email_verified_at:
            token, created = self._ensure_verify_token(raw_email)
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

        target = self._resolve_target_slug(raw_email, uid_hint)
        token = issue_session(raw_email)
        return LoginSuccess(email=raw_email, session_token=token, target_slug=target)

    # -------------------------------------- verificação --------------------------------------
    def verify_email(self, token: str) -> VerifyResult:
        token_value = (token or "").strip()
        if not token_value:
            raise TokenInvalidError("Token invalido ou expirado.")
        entity = self.repository.get_verify_token(token_value)
        now = self._now()
        if not entity or self._token_expired(entity.created_at, now):
            if entity:
                self.repository.delete_verify_token(token_value)
            raise TokenInvalidError("Token invalido ou expirado.")
        email = entity.email or ""
        user = self.repository.get_user(email)
        if not user:
            self.repository.delete_verify_token(token_value)
            raise TokenInvalidError("Usuario nao encontrado para este token.")
        self.repository.set_user_verified(email)
        self.repository.delete_verify_tokens_for_email(email)
        target = self._resolve_target_slug(email, None)
        session_token = issue_session(email)
        return VerifyResult(email=email, session_token=session_token, target_slug=target)

    def logout(self, session_token: Optional[str]):
        if not session_token:
            return
        delete_session(session_token)

    # -------------------------------------- reset de senha --------------------------------------
    def issue_password_reset(self, email: str) -> bool:
        raw = (email or "").strip()
        if not raw:
            return False
        user = self.repository.get_user(raw)
        if not user:
            return False
        self.repository.delete_reset_tokens_for_email(raw)
        token = self.repository.create_reset_token(raw)
        reset_url = absolute_url(f"/auth/reset?token={token}")
        html_body = f"""
        <p>Olá!</p>
        <p>Recebemos um pedido para redefinir sua senha.</p>
        <p><a href="{reset_url}" style="background:#0ea5e9;color:#fff;padding:12px 18px;border-radius:8px;text-decoration:none;">Redefinir senha</a></p>
        <p>Se não foi você, ignore esta mensagem.</p>
        """
        send_email("Redefina sua senha - Soomei", raw, html_body, f"Use este link para redefinir sua senha: {reset_url}")
        return True

    def validate_reset_token(self, token: str) -> dict | None:
        token = (token or "").strip()
        if not token:
            return None
        entity = self.repository.get_reset_token(token)
        if not entity:
            return None
        now = self._now()
        if self._token_expired(entity.created_at, now, ttl_seconds=self.settings.password_reset_ttl):
            self.repository.delete_reset_token(token)
            return None
        return {"token": token, "email": entity.email}

    def reset_password(self, token: str, password: str) -> str | None:
        token = (token or "").strip()
        if not token:
            return None
        entity = self.repository.get_reset_token(token)
        if not entity:
            return None
        now = self._now()
        if self._token_expired(entity.created_at, now, ttl_seconds=self.settings.password_reset_ttl):
            self.repository.delete_reset_token(token)
            return None
        email = (entity.email or "").strip()
        if not email:
            self.repository.delete_reset_token(token)
            return None
        user = self.repository.get_user(email)
        if not user:
            self.repository.delete_reset_token(token)
            return None
        self.repository.update_user_password(email, hash_password(password))
        self.repository.delete_reset_token(token)
        return email
