"""Application settings, loaded from environment / .env.

Twelve-factor: every deployment-specific value comes from the environment. The
defaults are safe for local development only.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- App ---
    app_name: str = "InvoiceIQ"
    api_v1_prefix: str = "/api/v1"
    environment: str = Field(default="development")

    # --- Database ---
    # Async SQLAlchemy URL. Postgres in prod, SQLite for zero-setup local/dev/test.
    #   postgres:  postgresql+asyncpg://user:pass@host:5432/invoiceiq
    #   sqlite:    sqlite+aiosqlite:///./invoiceiq.db
    database_url: str = Field(default="sqlite+aiosqlite:///./invoiceiq.db")

    # --- Auth ---
    # MUST be overridden in production (openssl rand -hex 32).
    secret_key: str = Field(default="dev-insecure-change-me")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 24h

    # --- Email invoice intake ---
    # Domain for per-org inbound addresses (`<token>@<domain>`). An email provider's
    # inbound-parse webhook (SendGrid/Mailgun/Postmark) posts attachments to
    # `POST /email/inbound`. When `inbound_email_secret` is set, that webhook must
    # present the matching secret (header `X-Inbound-Secret` or a `secret` field);
    # when unset, the endpoint is open (dev convenience).
    inbound_email_domain: str = Field(default="in.invoiceiq.app")
    inbound_email_secret: str | None = Field(default=None)

    # --- CORS ---
    # Comma-separated list of allowed origins for the SPA.
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
