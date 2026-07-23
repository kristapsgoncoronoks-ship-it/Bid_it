"""Application settings, loaded from environment / .env.

Twelve-factor: every deployment-specific value comes from the environment. The
defaults are safe for local development only.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRET_KEY = "dev-insecure-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_name: str = "InvoiceIQ"
    api_v1_prefix: str = "/api/v1"
    environment: str = Field(default="development")

    # --- Observability ---
    # Emit one structured JSON log line per request (ship to Loki/CloudWatch/etc).
    # Defaults on in production, off (human-readable) elsewhere — see main.py.
    log_json: bool | None = Field(default=None)
    # Expose Prometheus metrics at /metrics (only if prometheus-client installed).
    metrics_enabled: bool = Field(default=True)

    # --- Database ---
    # Async SQLAlchemy URL. Postgres in prod, SQLite for zero-setup local/dev/test.
    #   postgres:  postgresql+asyncpg://user:pass@host:5432/invoiceiq
    #   sqlite:    sqlite+aiosqlite:///./invoiceiq.db
    database_url: str = Field(default="sqlite+aiosqlite:///./invoiceiq.db")
    # Connection pool (Postgres). Size per worker process; total connections =
    # workers × replicas × (pool_size + max_overflow) must stay under Postgres
    # max_connections. Ignored for SQLite.
    db_pool_size: int = Field(default=10)
    db_max_overflow: int = Field(default=10)
    db_pool_timeout: int = Field(default=30)
    # Set true when Postgres is reached through PgBouncer in TRANSACTION pooling
    # mode (the scale-out topology). asyncpg's server-side prepared-statement
    # cache is incompatible with transaction pooling — a statement prepared on one
    # server connection isn't visible on the next — so we disable it. Harmless in
    # session pooling / direct connections; ignored for SQLite.
    db_pgbouncer: bool = Field(default=False)

    # --- Object storage (document bytes; ADR-0008) ---
    # backend: local (filesystem, default) | s3 (S3-compatible incl. MinIO) | memory (tests).
    storage_backend: str = Field(default="local")
    storage_local_path: str = Field(default="./var/storage")
    storage_s3_bucket: str = Field(default="invoiceiq-documents")
    storage_s3_endpoint_url: str = Field(
        default=""
    )  # e.g. http://minio:9000 for MinIO; blank = AWS
    storage_s3_region: str = Field(default="")
    storage_s3_prefix: str = Field(default="")  # optional key prefix within the bucket

    # --- Auth ---
    # MUST be overridden in production (openssl rand -hex 32).
    secret_key: str = Field(default=INSECURE_SECRET_KEY)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 24h
    # Per-account brute-force lockout: after N consecutive failed logins, the
    # account is locked for `login_lockout_minutes` (a successful login resets it).
    login_max_failed_attempts: int = Field(default=10)
    login_lockout_minutes: int = Field(default=15)
    # Number of TRUSTED reverse-proxy hops in front of the app (e.g. Cloudflare +
    # nginx = 2). The client IP used for rate-limiting is taken this many hops from
    # the right of X-Forwarded-For; 0 (default) IGNORES the header entirely and uses
    # the socket peer, so a spoofed XFF cannot mint fresh rate-limit buckets.
    trusted_proxy_count: int = Field(default=0)

    # --- Email invoice intake ---
    # Domain for per-org inbound addresses (`<token>@<domain>`). An email provider's
    # inbound-parse webhook (SendGrid/Mailgun/Postmark) posts attachments to
    # `POST /email/inbound`. When `inbound_email_secret` is set, that webhook must
    # present the matching secret (header `X-Inbound-Secret` or a `secret` field);
    # when unset, the endpoint is open (dev convenience).
    inbound_email_domain: str = Field(default="in.invoiceiq.app")
    inbound_email_secret: str | None = Field(default=None)

    # --- Outbound email (invoice delivery + payment reminders) ---
    # When smtp_host is set, messages are RELAYED via SMTP; otherwise every send
    # is still RECORDED to the outbox (demo/no-relay) and never fails a request.
    smtp_host: str | None = Field(default=None)
    smtp_port: int = Field(default=587)
    smtp_user: str | None = Field(default=None)
    smtp_password: str | None = Field(default=None)
    smtp_starttls: bool = Field(default=True)
    # Default From address; falls back to the issuer's billing email when unset.
    smtp_from: str = Field(default="billing@invoiceiq.app")

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host)

    # Public base URL of the SPA — used to build email-verification and
    # password-reset links (`{app_base_url}/verify-email?token=…`).
    app_base_url: str = Field(default="http://localhost:5173")
    # When on, login refuses an account whose email is not yet verified (SSO and
    # platform operators are exempt). Default OFF = no behaviour change; new
    # accounts still receive a verification link and can verify at their leisure.
    require_email_verification: bool = Field(default=False)

    # --- TLS / proxy hardening (Cloudflare + nginx origin) ---
    # Emit HSTS on HTTPS responses. On by default — it is only ever sent on an
    # HTTPS request (plain-http dev never receives it), so enabling it is safe.
    hsts_enabled: bool = Field(default=True)
    hsts_max_age: int = Field(default=63072000)  # 2 years

    # --- File security (uploads & email attachments) ---
    max_upload_mb: int = Field(default=15)
    # Optional ClamAV daemon for malware scanning. When enabled, a scan failure
    # fails CLOSED (the file is rejected). When disabled (default), type
    # validation + EICAR detection still apply.
    clamav_enabled: bool = Field(default=False)
    clamav_host: str = Field(default="127.0.0.1")
    clamav_port: int = Field(default=3310)
    clamav_unix_socket: str | None = Field(default=None)

    # --- Billing (ADR-0013) ---
    # Two providers behind one seam, selected by `billing_provider`:
    #   stripe   — subscription platform: Checkout + Portal + signed webhook is
    #              the authority for plan/status.
    #   everypay — Baltic (EE/LV/LT) card gateway: hosted payment page, redirect
    #              back + server-side status verify; recurring via a stored token.
    #   auto     — pick stripe if a Stripe key is set, else everypay if its creds
    #              are set, else the NullProvider (nothing charges).
    #   none     — force the NullProvider.
    billing_provider: str = Field(default="auto")  # auto | stripe | everypay | none

    # Stripe.
    stripe_secret_key: str | None = Field(default=None)
    stripe_webhook_secret: str | None = Field(default=None)
    stripe_price_starter: str | None = Field(default=None)
    stripe_price_pro: str | None = Field(default=None)
    # Metered usage/overage: map an internal meter to a Stripe Billing Meter
    # `event_name`. Unset → that metric is not reported to Stripe.
    stripe_meter_upload: str | None = Field(default=None)

    # EveryPay (https://every-pay.com). Test base: https://igw-demo.every-pay.com/api/v4
    # Live base: https://pay.every-pay.eu/api/v4. HTTP Basic (api_username/secret).
    everypay_api_username: str | None = Field(default=None)
    everypay_api_secret: str | None = Field(default=None)
    everypay_account_name: str | None = Field(default=None)  # processing account, e.g. "EUR3D1"
    everypay_api_base_url: str = Field(default="https://igw-demo.every-pay.com/api/v4")

    # Where the provider redirects the browser back to after checkout / portal.
    billing_success_url: str = Field(default="http://localhost:5173/billing?checkout=success")
    billing_cancel_url: str = Field(default="http://localhost:5173/billing?checkout=cancel")
    billing_portal_return_url: str = Field(default="http://localhost:5173/billing")
    # Public base URL of THIS API (for EveryPay's customer_url / callback_url,
    # and the SSO redirect URI).
    api_public_base_url: str = Field(default="http://localhost:8000")

    # --- SSO (OIDC/SAML; ADR-0021) ---
    # Where the browser lands after a successful SSO login; the SPA reads the
    # issued token from the URL fragment there.
    sso_post_login_url: str = Field(default="http://localhost:5173/sso/callback")
    # Where to bounce on an SSO error (SPA login page).
    sso_error_url: str = Field(default="http://localhost:5173/login")

    @property
    def sso_redirect_uri(self) -> str:
        """The OIDC redirect/callback URI registered with the IdP."""
        return f"{self.api_public_base_url.rstrip('/')}{self.api_v1_prefix}/auth/sso/callback"

    @property
    def everypay_configured(self) -> bool:
        return bool(
            self.everypay_api_username and self.everypay_api_secret and self.everypay_account_name
        )

    @property
    def active_billing_provider(self) -> str:
        """Resolve `billing_provider` to a concrete 'stripe'|'everypay'|'none'."""
        choice = self.billing_provider
        if choice == "stripe":
            return "stripe" if self.stripe_secret_key else "none"
        if choice == "everypay":
            return "everypay" if self.everypay_configured else "none"
        if choice == "none":
            return "none"
        # auto
        if self.stripe_secret_key:
            return "stripe"
        if self.everypay_configured:
            return "everypay"
        return "none"

    @property
    def billing_enabled(self) -> bool:
        return self.active_billing_provider != "none"

    def stripe_price_for(self, plan_key: str) -> str | None:
        return {"starter": self.stripe_price_starter, "pro": self.stripe_price_pro}.get(plan_key)

    def stripe_meter_for(self, metric: str) -> str | None:
        return {"upload": self.stripe_meter_upload}.get(metric)

    # --- Secret encryption (ADR-0016) ---
    # KEK for app-level secret sealing (keyvault.py). `local` (default) derives
    # the key from `secret_key`; set `kek_key` (base64 32 bytes, BYOK) for
    # production, or wire a cloud KMS behind the keyvault seam later.
    kek_provider: str = Field(default="local")  # local | env(BYOK) | (future) kms
    kek_key: str | None = Field(default=None)  # base64-encoded 32-byte KEK

    # --- Data residency / region-pinning (ADR-0022) ---
    # `service_region` is THIS deployment's data plane (e.g. "eu", "us"). New
    # tenants are pinned to `default_tenant_region` (defaults to service_region).
    # When `enforce_region_pinning` is on, an authenticated request for a tenant
    # pinned to a DIFFERENT region is refused (421) — a belt-and-braces backstop
    # behind the load balancer's region routing. Off by default = single-region,
    # byte-identical behaviour.
    service_region: str = Field(default="eu")
    default_tenant_region: str | None = Field(default=None)
    enforce_region_pinning: bool = Field(default=False)

    @property
    def tenant_region(self) -> str:
        return self.default_tenant_region or self.service_region

    # --- Background-queue SLO (observability) ---
    # The queue is "degraded" when the oldest ready-but-unprocessed job is older
    # than this (worker falling behind), or the dead-letter depth exceeds the
    # threshold. Surfaced on /health/queue (503 when breached) + /metrics.
    queue_slo_max_pending_age_seconds: int = Field(default=900)  # 15 min
    queue_dlq_alert_threshold: int = Field(default=0)  # any dead job alerts

    # --- Rate limiting (ADR-0015) ---
    # First-line abuse + brute-force guard. PER-PROCESS fixed-window counters, so
    # with N replicas the effective global ceiling is N × the limit (documented
    # tradeoff; a precise global limit is the shared-store scale path). Set a
    # limit <= 0 to disable that tier. `/auth/*` gets the stricter tier.
    rate_limit_enabled: bool = Field(default=True)
    rate_limit_per_min: int = Field(default=300)  # general API, per token/IP
    rate_limit_auth_per_min: int = Field(default=20)  # /auth/*, per client IP

    # --- CORS ---
    # Comma-separated list of allowed origins for the SPA.
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def structured_logs(self) -> bool:
        # Explicit override wins; otherwise JSON logs in production only.
        return self.log_json if self.log_json is not None else self.is_production

    @model_validator(mode="after")
    def _validate_production(self) -> Settings:
        """Fail fast when a production deployment still carries an insecure/dev
        default. Dev and test (environment != 'production') are unaffected, so
        zero-config local startup keeps working. This turns a silent security
        footgun (booting prod with the dev signing key) into a boot-time crash."""
        if self.environment != "production":
            return self
        problems: list[str] = []
        if self.secret_key == INSECURE_SECRET_KEY:
            problems.append("secret_key is still the insecure dev default (set SECRET_KEY)")
        if self.is_sqlite:
            problems.append("database_url points at SQLite (set a Postgres DATABASE_URL)")
        if self.kek_provider == "env" and not self.kek_key:
            problems.append("kek_provider=env but kek_key is unset (set KEK_KEY, base64 32 bytes)")
        if "*" in self.cors_origin_list:
            problems.append("cors_origins allows '*' with credentials (set explicit origins)")
        if problems:
            raise ValueError("Insecure production configuration:\n  - " + "\n  - ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
