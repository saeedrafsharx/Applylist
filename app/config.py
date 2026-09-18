from __future__ import annotations

from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from the environment (or a local .env)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── core ────────────────────────────────────────────────────
    app_name: str = "ApplyList"
    base_url: str = "http://localhost:8000"
    environment: Literal["dev", "staging", "production"] = "dev"
    secret_key: str = "dev-secret-change-me"
    session_cookie: str = "ct_session"
    session_max_age: int = 60 * 60 * 24 * 14  # 14 days

    # ── database ────────────────────────────────────────────────
    database_url: str = "postgresql+psycopg://applylist:applylist@localhost:5432/applylist"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # ── email (SMTP) ────────────────────────────────────────────
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "ApplyList <no-reply@applylist.ir>"
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    email_verification_ttl_hours: int = 24
    password_reset_ttl_hours: int = 2

    # ── Zarinpal ────────────────────────────────────────────────
    zarinpal_merchant_id: str = ""
    zarinpal_sandbox: bool = True
    # Zarinpal v4 quotes amounts in Rial. Prices below are Rial per period.
    zarinpal_currency: str = "IRR"

    # ── Claude ──────────────────────────────────────────────────
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    ai_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    ai_max_tokens: int = 8000
    ai_monthly_message_quota: int = 300
    ai_history_turns: int = 20

    # ── scraper ─────────────────────────────────────────────────
    scraper_user_agent: str = (
        "ApplyListBot/1.0 (+https://applylist.ir/bot; contact@applylist.ir)"
    )
    scraper_delay_seconds: float = 2.0
    scraper_timeout_seconds: float = 20.0
    scraper_max_pages: int = 50
    scraper_respect_robots: bool = True

    # ── bootstrap admin ─────────────────────────────────────────
    admin_email: Optional[str] = None
    admin_username: str = "admin"
    admin_password: Optional[str] = None

    @field_validator("database_url")
    @classmethod
    def _normalize_pg_scheme(cls, v: str) -> str:
        """Accept the `postgres://` / `postgresql://` URLs hosts hand out."""
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://") :]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host)

    @property
    def ai_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def payments_enabled(self) -> bool:
        return bool(self.zarinpal_merchant_id)

    @property
    def zarinpal_api_base(self) -> str:
        return (
            "https://sandbox.zarinpal.com/pg/v4/payment"
            if self.zarinpal_sandbox
            else "https://payment.zarinpal.com/pg/v4/payment"
        )

    @property
    def zarinpal_startpay_base(self) -> str:
        return (
            "https://sandbox.zarinpal.com/pg/StartPay"
            if self.zarinpal_sandbox
            else "https://www.zarinpal.com/pg/StartPay"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
