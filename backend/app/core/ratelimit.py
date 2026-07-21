"""In-process rate limiting (ADR-0015): a first-line abuse + brute-force guard.

A fixed-window counter per key. Two tiers, both configurable:
  • auth endpoints (/auth/*) — strict, keyed by client IP → slows credential
    stuffing / brute-force against login + register.
  • everything else — keyed by the bearer token (per API consumer) or IP.

Scope + honesty (see ADR-0015): this limiter is PER-PROCESS, so with N API
replicas the effective global ceiling is N × the limit. That is a deliberate
first-version choice — a coarse abuse guard with zero extra infrastructure. A
precise GLOBAL limit needs a shared store (Redis / Postgres) and is the scale
path, documented as such. Health/metrics probes are exempt.

The middleware runs in a single event loop per process, so a check-and-increment
with no `await` between is atomic — no lock needed.
"""
from __future__ import annotations

import hashlib
import time


class FixedWindowLimiter:
    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, tuple[float, int]] = {}

    def allow(self, key: str, *, now: float | None = None) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds). `limit <= 0` disables the tier."""
        if self.limit <= 0:
            return True, 0
        now = time.monotonic() if now is None else now
        start, count = self._hits.get(key, (now, 0))
        if now - start >= self.window:
            start, count = now, 0
        count += 1
        self._hits[key] = (start, count)
        if count > self.limit:
            return False, int(self.window - (now - start)) + 1
        # Opportunistic prune so the map can't grow unbounded under key churn.
        if len(self._hits) > 10_000:
            self._prune(now)
        return True, 0

    def _prune(self, now: float) -> None:
        self._hits = {k: v for k, v in self._hits.items() if now - v[0] < self.window}

    def reset(self) -> None:
        self._hits.clear()


# Module-level singletons, configured from settings at import; the middleware
# references these so tests can reset/adjust them deterministically.
from app.core.config import settings  # noqa: E402

general_limiter = FixedWindowLimiter(settings.rate_limit_per_min, 60)
auth_limiter = FixedWindowLimiter(settings.rate_limit_auth_per_min, 60)


def reset_all() -> None:
    general_limiter.reset()
    auth_limiter.reset()


# --- ASGI middleware -------------------------------------------------------

# Never rate-limit liveness/readiness/metrics probes — they run on a schedule
# from the LB / Prometheus and must not be throttled or the replica looks down.
_EXEMPT_PREFIXES = ("/health", "/metrics")


def _client_ip(scope) -> str:
    """Best client IP. Behind a trusted proxy (uvicorn ``--proxy-headers`` or an
    explicit X-Forwarded-For), the first hop is the real client; else the peer."""
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-for" and value:
            return value.split(b",")[0].strip().decode("latin-1") or "unknown"
    client = scope.get("client")
    return client[0] if client else "unknown"


def _bearer_key(scope) -> str | None:
    """A stable per-consumer key from the bearer token (hashed, never stored raw)."""
    for name, value in scope.get("headers", []):
        if name == b"authorization" and value[:7].lower() == b"bearer ":
            token = value[7:].strip()
            if token:
                return "t:" + hashlib.sha256(token).hexdigest()[:32]
    return None


def _too_many(retry_after: int):
    """A minimal 429 ASGI response (no framework objects — pure ASGI)."""
    body = b'{"detail":"Rate limit exceeded. Please retry later."}'
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"retry-after", str(max(1, retry_after)).encode()),
    ]

    async def _send_response(send):
        await send({"type": "http.response.start", "status": 429, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    return _send_response


class RateLimitMiddleware:
    """Coarse per-process abuse guard (ADR-0015). Two tiers:

    * ``/auth/*`` — strict, keyed by client IP (credential-stuffing / brute-force).
    * everything else — keyed by bearer token (per consumer) else client IP.

    Health/readiness/metrics probes are exempt. Disabled wholesale by
    ``rate_limit_enabled = False`` (or per-tier by a limit <= 0).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not settings.rate_limit_enabled:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path.startswith(_EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        # The auth tier keys on IP so a flood of *different* logins from one host
        # is still bounded (a per-token key would be useless pre-authentication).
        if "/auth/" in path:
            allowed, retry = auth_limiter.allow("ip:" + _client_ip(scope))
        else:
            key = _bearer_key(scope) or ("ip:" + _client_ip(scope))
            allowed, retry = general_limiter.allow(key)

        if not allowed:
            await _too_many(retry)(send)
            return

        await self.app(scope, receive, send)
