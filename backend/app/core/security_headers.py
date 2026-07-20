"""Security-response-header middleware (defense-in-depth for TLS deployments).

Behind Cloudflare + an nginx origin, the browser always speaks HTTPS. This adds
the standard hardening headers to every API response, and — when the request is
effectively HTTPS (either the ASGI scheme is https after uvicorn's
``--proxy-headers`` rewrite, or ``X-Forwarded-Proto: https`` is present) and HSTS
is enabled — a **Strict-Transport-Security** header so browsers refuse plain HTTP.

Pure-ASGI so it composes with the tenant-scope middleware and adds no per-request
Python object overhead beyond a header rewrite.
"""
from __future__ import annotations

from app.core.config import settings

_STATIC_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"cross-origin-opener-policy", b"same-origin"),
]


def _is_https(scope) -> bool:
    if scope.get("scheme") == "https":
        return True
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-proto" and value.split(b",")[0].strip() == b"https":
            return True
    return False


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        add = list(_STATIC_HEADERS)
        if settings.hsts_enabled and _is_https(scope):
            add.append((
                b"strict-transport-security",
                f"max-age={settings.hsts_max_age}; includeSubDomains; preload".encode(),
            ))

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {h[0].lower() for h in headers}
                for name, value in add:
                    if name not in present:
                        headers.append((name, value))
            await send(message)

        await self.app(scope, receive, send_wrapper)
