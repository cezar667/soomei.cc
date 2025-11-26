"""
Configuration helpers for the Soomei backend.

Eventually this module will expose a Settings object (pydantic or plain) that
reads environment variables (public base URL, SMTP, storage paths, feature
flags, etc.) so that routers/services do not fetch os.environ directly.
"""

from dataclasses import dataclass
from functools import lru_cache
import os


@dataclass(frozen=True)
class Settings:
    """Typed view of environment variables."""

    app_env: str
    public_base_url: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    password_reset_ttl: int
    custom_domains_enabled: bool
    session_ttl_seconds: int
    email_verification_ttl_seconds: int


@lru_cache
def get_settings() -> Settings:
    """Read the current environment and build a Settings instance."""
    def _int(value: str, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _bool(value: str | None, default: bool = False) -> bool:
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    return Settings(
        app_env=(os.getenv("APP_ENV") or "dev").lower(),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "https://soomei.cc").rstrip("/"),
        smtp_host=os.getenv("SMTP_HOST", ""),
        smtp_port=_int(os.getenv("SMTP_PORT", "465"), 465),
        smtp_user=os.getenv("SMTP_USER", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        smtp_from=os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "")),
        password_reset_ttl=_int(os.getenv("PASSWORD_RESET_TTL", "86400"), 86400),
        custom_domains_enabled=_bool(os.getenv("CUSTOM_DOMAINS_ENABLED"), False),
        session_ttl_seconds=_int(os.getenv("SESSION_TTL_SECONDS", "86400"), 86400),
        email_verification_ttl_seconds=_int(os.getenv("EMAIL_VERIFICATION_TTL_SECONDS", "900"), 900),
    )
