"""Application settings, loaded from environment / .env.

Twelve-factor: every deployment-specific value comes from the environment. The
defaults are safe for local development only.
"""

from __future__ import annotations

import logging
from decimal import Decimal
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
    # PERF-017 (audit 2026-09-05): the third cyclic-collector threshold — how
    # many generation-1 collections pass before a full pass over everything
    # the process built after startup (the startup heap itself is frozen,
    # PERF-016). CPython's default is 10; this service runs 100, applied at
    # the end of startup with the freeze. Measured 2026-09-06 (three shape
    # runs each at scale 1200, `docs/perf/GC-PAUSE-2026-09-06.md`): at 10 the
    # whole-window read's large p95 was 105–109 ms against a p50 of ~65 (the
    # ~40 ms residual pass, about every third request); at 100 it is 69–74 ms
    # and the p95 growth ratio equals the p50 ratio — for +1–3 MB of peak RSS
    # on 180 MB. `GC_GEN2_THRESHOLD=10` restores the interpreter default; unset
    # in the environment means this default, None means leave the interpreter
    # alone (the harness prints the thresholds and peak RSS of every run).
    gc_gen2_threshold: int | None = Field(default=100, ge=1)
    # PERF-009 (audit 2026-09-05): the integrity routes verify synchronously up
    # to this many references (documents to re-hash, ledger rows to compare,
    # version slots to walk); above it they queue the matching background job
    # and answer 202, so one admin click can no longer hold a worker for the
    # length of a large tenant's object store. 500 re-hashes of typical
    # receipt-sized objects finish well inside a request; the knob exists so
    # an operator can lower it on a slow store without a release.
    integrity_sync_limit: int = Field(default=500, ge=1)
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
    # ARCH-002 (audit 2026-09-05): `local` is a per-PROCESS-HOST directory. It is
    # correct on the single-VPS stack, where backend and worker mount ONE
    # shared volume (docker-compose.hostinger.yml sets this true), and wrong on
    # any deployment with replicas on different hosts (N disjoint document
    # stores, uploads that "vanish" on the next request). Production with
    # `local` and no acknowledgement logs a startup warning — not a refusal:
    # the live VPS runs exactly that shape by design (`production_warnings`).
    storage_local_shared: bool = Field(default=False)
    storage_s3_bucket: str = Field(default="invoiceiq-documents")
    storage_s3_endpoint_url: str = Field(
        default=""
    )  # e.g. http://minio:9000 for MinIO; blank = AWS
    storage_s3_region: str = Field(default="")
    storage_s3_prefix: str = Field(default="")  # optional key prefix within the bucket

    # --- Auth ---
    # MUST be overridden in production (openssl rand -hex 32).
    # `secret_key` is the application secret and, with the default
    # kek_provider=local, the source of the local KEK: rotating it destroys
    # every sealed tenant secret (SEC-KEK-001). Do NOT rotate it merely to
    # rotate JWT signatures — that is what `jwt_signing_key` is for.
    secret_key: str = Field(default=INSECURE_SECRET_KEY)
    # SEC-JWT-001 / FLASK-P2-01 (reference integration R3, 2026-09-07): the
    # JWT signing purpose separated from the app/KEK secret. Unset keeps the
    # historical contract exactly (JWTs use `secret_key`). Once set, NEW
    # internal JWTs (access tokens, OIDC state) sign only with this key;
    # `jwt_signing_key_fallbacks` are VERIFY-ONLY previous keys for a staged
    # rotation — keep the list short (max 3) and remove a key once the
    # longest internal-token lifetime (24 h) has passed. A key believed
    # compromised never goes into the fallbacks: drop it and revoke sessions.
    # FIRST ENABLEMENT IS ITSELF A ROTATION: tokens minted before were signed
    # with `secret_key`, which leaves the verification list the moment this is
    # set — list the current SECRET_KEY as a fallback for 24 h or every session
    # and in-flight SSO login dies at the deploy.
    # Env: JWT_SIGNING_KEY=…  JWT_SIGNING_KEY_FALLBACKS='["old-key"]' (JSON list).
    jwt_signing_key: str | None = Field(default=None)
    jwt_signing_key_fallbacks: list[str] = Field(default_factory=list, max_length=3)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 24h

    @property
    def active_jwt_signing_key(self) -> str:
        """The ONLY key new internal JWTs are signed with."""
        return self.jwt_signing_key or self.secret_key

    @property
    def jwt_verification_keys(self) -> tuple[str, ...]:
        """Active key first, then the verify-only fallbacks, in order."""
        return (self.active_jwt_signing_key, *self.jwt_signing_key_fallbacks)

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
    # `POST /email/inbound` and MUST present the shared secret (header
    # `X-Inbound-Secret` or a `secret` body field). The secret is MANDATORY:
    # production refuses to boot without it (`_validate_production`), and the
    # endpoint fails CLOSED (401) whenever it is unset — an unset secret must
    # never silently open a document-injection door into a tenant's review inbox.
    # Generate one: python -c "import secrets;print(secrets.token_urlsafe(32))"
    inbound_email_domain: str = Field(default="in.invoiceiq.app")
    inbound_email_secret: str | None = Field(default=None)

    # Mailgun inbound-parse ADAPTER (E1.6): a second, provider-native webhook
    # (`POST /email/inbound/mailgun`) that maps Mailgun's own multipart payload
    # (recipient/sender/attachment-N + a timestamp/token/signature HMAC) onto the
    # SAME `email_intake.process_attachment` pipeline the generic JSON endpoint
    # above uses. `mailgun_signing_key` is the account's inbound-routes signing
    # key (Mailgun dashboard → Sending → Webhooks). Unlike `inbound_email_secret`
    # this is OPTIONAL and NOT enforced by `_validate_production` — using Mailgun
    # specifically is a per-deployment choice, not a baseline requirement — but
    # the route itself still fails CLOSED (401) whenever it is unset, exactly
    # like an unset `inbound_email_secret` does for the generic path.
    mailgun_signing_key: str | None = Field(default=None)

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
    # PROD-009: the whole-workspace export is built into a temporary file one
    # table at a time, and the build ABORTS the moment the file crosses this —
    # so it bounds the worker's disk as well as its memory. Storing it is what
    # bounds memory: `core.storage` is content-addressed and its `put` takes
    # bytes, so the finished zip is read once on the worker and once more on
    # the API pod that serves the download (EXPORT-001).
    #
    # The default is sized from the SMALLEST pod that must hold one, not from
    # what a big tenant would like: `deploy/k8s/36-worker-lanes.yaml` gives the
    # general lane 512Mi and `30-backend.yaml` gives the API 768Mi, against a
    # ~200MB interpreter baseline. A 512MB default — the first draft's — would
    # have been OOM-killed at roughly half the ceiling it was supposed to
    # enforce, which is exactly the crash-shaped failure it exists to replace
    # (PROD-009 review, D1). Raise it together with those limits, in the
    # ConfigMap, never on its own.
    workspace_export_max_bytes: int = Field(default=128 * 1024 * 1024)
    # Optional ClamAV daemon for malware scanning. When enabled, a scan failure
    # fails CLOSED (the file is rejected). When disabled (default), type
    # validation + EICAR detection still apply.
    clamav_enabled: bool = Field(default=False)
    clamav_host: str = Field(default="127.0.0.1")
    clamav_port: int = Field(default=3310)
    clamav_unix_socket: str | None = Field(default=None)
    # ARCH-003/BE-007 (audit 2026-09-05): clamd's sockets had NO timeout — a hung
    # daemon parked the scan for ever. Fail closed after this many seconds.
    clamav_timeout_seconds: float = Field(default=20.0)
    # STIR-P2-01 (Stirling-PDF, reference integration 2026-09-07): every
    # pytesseract call launches a native Tesseract process with NO time bound,
    # so one pathological page (a huge blank scan, a corrupt bitmap) could pin
    # an OCR worker for ever. Bound one page/image invocation. Long multi-page
    # captures stay valid: the bound is per page and the job lease is renewed
    # independently while the worker owns it (STIR-P1-01).
    ocr_process_timeout_seconds: float = Field(default=120.0, gt=0, le=300)

    # BE-024 (audit 2026-09-05, found by the R6 panels): the DEFAULT per-job
    # deadline, for every kind that does not declare its own on `@jobs.handler`.
    # STIR-P1-01 made a slow job stop looking like a crashed one by renewing its
    # lease; the mirror-image cost is that a HUNG job renews its lease for ever
    # and no watchdog can see it. 30 minutes is deliberately far above every
    # handler that is not explicitly long (the slowest of those in the perf
    # harness and the suite finish in seconds), so this bound frees a wedged
    # worker without ever truncating honest work. Kinds that legitimately run
    # longer — the exports and the integrity sweeps — say so at registration.
    job_deadline_seconds: float = Field(default=1800.0, gt=0, le=86400)

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
    # WO-AD: the Business tier (§2a, 2026-08-15) had no price-id slot, so the SPA
    # offered a checkout that could only 502. Unset → Business reports as not
    # purchasable and is never offered; set → it checks out like the others.
    stripe_price_business: str | None = Field(default=None)
    # WO-AD: DECISIONS §2 decided "enable Stripe Tax" but the checkout session
    # never asked for it. OFF by default even with a live key — turning on tax
    # collection is a filing commitment the owner makes explicitly, not a side
    # effect of configuring a secret.
    stripe_automatic_tax: bool = Field(default=False)
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
        return {
            "starter": self.stripe_price_starter,
            "pro": self.stripe_price_pro,
            "business": self.stripe_price_business,
        }.get(plan_key)

    def plan_purchasable(self, plan_key: str, price_eur: int | None) -> bool:
        """WO-AD: can the SPA offer this plan? Free/default plans are always
        switchable. A PRICED plan is purchasable only if the active provider can
        actually sell it — for Stripe that means a configured price id; for
        EveryPay (amount-based) or no provider (in-app switch), any listed price.
        A priced plan the provider cannot sell must never be offered: the
        checkout could only fail."""
        if not price_eur:
            return True
        if self.active_billing_provider == "stripe":
            return self.stripe_price_for(plan_key) is not None
        return True

    def stripe_meter_for(self, metric: str) -> str | None:
        return {"upload": self.stripe_meter_upload}.get(metric)

    # --- Dogfood subscription billing (H1.6 / ADR-0013, WO-48) ---
    # A FALLBACK path that invoices InvoiceIQ's own paying tenants through the
    # platform's OWN accounts-receivable module (issuer profile, gap-free
    # numbering, PDF/XML, send, dunning — all pre-existing) instead of a payment
    # provider, so revenue is never blocked on Stripe/EveryPay credentials
    # (docs/DECISIONS-NEEDED.md §2). `platform_org_id` names WHICH of the
    # platform's own organizations — created via the ordinary signup flow, like
    # any tenant — issues the invoices; unset (the default) makes the whole
    # feature an inert no-op. Nothing here invents the operator's legal name/
    # VAT/address: that is entered through the SAME `/issuers` screen every
    # tenant already uses for their own AR.
    platform_org_id: str | None = Field(default=None)
    # VAT rate applied to our own subscription invoice lines. Defaults to 0% —
    # asserts no VAT treatment. The correct rate/scheme is a seller-of-record
    # VAT decision still owner-blocked (docs/DECISIONS-NEEDED.md §2); this is a
    # placeholder, trivially corrected (env override) once that decision lands.
    platform_subscription_vat_rate: Decimal = Field(default=Decimal("0"))

    @property
    def dogfood_billing_enabled(self) -> bool:
        """H1.6: the AR-module fallback runs only when a platform org is
        configured AND no live payment provider is active — so a tenant is
        never invoiced twice once Stripe/EveryPay goes live."""
        return bool(self.platform_org_id) and not self.billing_enabled

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

    # --- Worker liveness (the container probe) ---
    # The worker loop touches this file every tick and every lease heartbeat
    # while a job runs; `python -m app.worker_probe` exits non-zero when the
    # file is older than the age below — a process whose loop is wedged. This
    # is LIVENESS of one process, deliberately not the queue SLO: a backlog is
    # a fleet condition `/health/queue` pages on, and restarting every replica
    # on a backlog would only lose the in-flight jobs (worker_probe.py).
    worker_liveness_path: str = Field(default="/tmp/invoiceiq-worker-liveness")  # noqa: S108
    worker_liveness_max_age_seconds: int = Field(default=180, gt=0)

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

    def production_warnings(self) -> list[str]:
        """Configuration that BOOTS but deserves a line in the startup log — the
        cases the validator deliberately does not refuse because a live
        deployment runs them on purpose. Logged by the API's lifespan and the
        worker's main; empty outside production."""
        if self.environment != "production":
            return []
        warnings: list[str] = []
        if (self.storage_backend or "local").lower() == "local" and not self.storage_local_shared:
            warnings.append(
                "STORAGE_BACKEND=local in production: document bytes live in a directory "
                f"on this host ({self.storage_local_path}). Correct only when every "
                "backend and worker replica mounts the SAME volume — set "
                "STORAGE_LOCAL_SHARED=true to acknowledge that, or use STORAGE_BACKEND=s3 "
                "(ARCH-002)."
            )
        return warnings

    @model_validator(mode="after")
    def _validate_production(self) -> Settings:
        """Fail fast when a production deployment still carries an insecure/dev
        default. Dev and test (environment != 'production') are unaffected, so
        zero-config local startup keeps working. This turns a silent security
        footgun (booting prod with the dev signing key) into a boot-time crash.

        `inbound_email_secret` is required UNCONDITIONALLY in production (there
        is no boot-time feature flag for email intake — activation is per-org DB
        state the validator cannot see), so the simpler, stricter rule applies:
        no secret, no production boot. Without it, anyone who guesses a 64-bit
        inbound address token could inject documents into a tenant's review
        inbox (risk S-5); the webhook endpoint independently fails closed too."""
        if self.environment != "production":
            return self
        problems: list[str] = []
        if self.secret_key == INSECURE_SECRET_KEY:
            problems.append("secret_key is still the insecure dev default (set SECRET_KEY)")
        if self.active_jwt_signing_key == INSECURE_SECRET_KEY:
            problems.append(
                "JWT signing still uses the insecure dev default (set JWT_SIGNING_KEY or SECRET_KEY)"
            )
        if INSECURE_SECRET_KEY in self.jwt_signing_key_fallbacks:
            problems.append("jwt_signing_key_fallbacks contains the insecure dev default")
        if self.active_jwt_signing_key in self.jwt_signing_key_fallbacks:
            problems.append("jwt_signing_key_fallbacks repeats the active signing key")
        if len(set(self.jwt_signing_key_fallbacks)) != len(self.jwt_signing_key_fallbacks):
            problems.append("jwt_signing_key_fallbacks contains duplicate keys")
        if self.is_sqlite:
            problems.append("database_url points at SQLite (set a Postgres DATABASE_URL)")
        if self.kek_provider == "env" and not self.kek_key:
            problems.append("kek_provider=env but kek_key is unset (set KEK_KEY, base64 32 bytes)")
        if "*" in self.cors_origin_list:
            problems.append("cors_origins allows '*' with credentials (set explicit origins)")
        if not self.inbound_email_secret:
            problems.append("inbound_email_secret is unset (set INBOUND_EMAIL_SECRET)")
        if problems:
            raise ValueError("Insecure production configuration:\n  - " + "\n  - ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def log_production_warnings(log: logging.Logger, current: Settings | None = None) -> int:
    """Emit `production_warnings()` on `log` at WARNING; returns how many. The
    one call every process entrypoint (API lifespan, worker main) makes, so a
    configuration the validator tolerates is still SEEN in the startup log."""
    lines = (current or settings).production_warnings()
    for line in lines:
        log.warning(line)
    return len(lines)
