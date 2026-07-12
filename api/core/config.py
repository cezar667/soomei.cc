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
    database_url: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    password_reset_ttl: int
    custom_domains_enabled: bool
    session_ttl_seconds: int
    email_verification_ttl_seconds: int
    membership_webhook_enabled: bool
    membership_webhook_secret: str
    membership_webhook_previous_secret: str
    membership_webhook_max_delay_seconds: int
    membership_webhook_provider: str
    membership_webhook_rate_limit_per_minute: int
    membership_webhook_max_payload_bytes: int
    membership_webhook_max_retries: int
    membership_webhook_worker_batch_size: int
    membership_webhook_worker_interval_seconds: int


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
        database_url=os.getenv("DATABASE_URL", ""),
        smtp_host=os.getenv("SMTP_HOST", ""),
        smtp_port=_int(os.getenv("SMTP_PORT", "465"), 465),
        smtp_user=os.getenv("SMTP_USER", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        smtp_from=os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "")),
        password_reset_ttl=_int(os.getenv("PASSWORD_RESET_TTL", "86400"), 86400),
        custom_domains_enabled=_bool(os.getenv("CUSTOM_DOMAINS_ENABLED"), False),
        session_ttl_seconds=_int(os.getenv("SESSION_TTL_SECONDS", "86400"), 86400),
        email_verification_ttl_seconds=_int(os.getenv("EMAIL_VERIFICATION_TTL_SECONDS", "900"), 900),
        membership_webhook_enabled=_bool(os.getenv("MEMBERSHIP_WEBHOOK_ENABLED"), False),
        membership_webhook_secret=os.getenv("MEMBERSHIP_WEBHOOK_SECRET", ""),
        membership_webhook_previous_secret=os.getenv("MEMBERSHIP_WEBHOOK_PREVIOUS_SECRET", ""),
        membership_webhook_max_delay_seconds=_int(os.getenv("MEMBERSHIP_WEBHOOK_MAX_DELAY_SECONDS", "300"), 300),
        membership_webhook_provider=os.getenv("MEMBERSHIP_WEBHOOK_PROVIDER", "membership_platform"),
        membership_webhook_rate_limit_per_minute=_int(os.getenv("MEMBERSHIP_WEBHOOK_RATE_LIMIT_PER_MINUTE", "100"), 100),
        membership_webhook_max_payload_bytes=_int(os.getenv("MEMBERSHIP_WEBHOOK_MAX_PAYLOAD_BYTES", "1048576"), 1048576),
        membership_webhook_max_retries=_int(os.getenv("MEMBERSHIP_WEBHOOK_MAX_RETRIES", "5"), 5),
        membership_webhook_worker_batch_size=_int(os.getenv("MEMBERSHIP_WEBHOOK_WORKER_BATCH_SIZE", "50"), 50),
        membership_webhook_worker_interval_seconds=_int(os.getenv("MEMBERSHIP_WEBHOOK_WORKER_INTERVAL_SECONDS", "5"), 5),
    )


def validate_membership_webhook_settings(settings: Settings | None = None) -> None:
    """Fail fast for inconsistent webhook configuration in production."""
    cfg = settings or get_settings()
    if cfg.app_env == "prod" and cfg.membership_webhook_enabled and not cfg.membership_webhook_secret:
        raise RuntimeError("MEMBERSHIP_WEBHOOK_SECRET must be configured when webhooks are enabled in production.")
    if cfg.membership_webhook_max_delay_seconds <= 0:
        raise RuntimeError("MEMBERSHIP_WEBHOOK_MAX_DELAY_SECONDS must be greater than zero.")
    if cfg.membership_webhook_max_payload_bytes <= 0:
        raise RuntimeError("MEMBERSHIP_WEBHOOK_MAX_PAYLOAD_BYTES must be greater than zero.")
    if cfg.membership_webhook_max_retries < 0:
        raise RuntimeError("MEMBERSHIP_WEBHOOK_MAX_RETRIES cannot be negative.")
