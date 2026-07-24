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

import re
from urllib.parse import quote

from app.core.config import settings

# Anything outside this set is replaced in the ASCII `filename` fallback — in
# particular quotes, backslashes and every control char (CR/LF included), so a
# crafted upload name can never break out of the header or inject a second one.
_CD_UNSAFE = re.compile(r"[^A-Za-z0-9_.\- ]")


def content_disposition(
    filename: str | None, *, fallback: str = "download", disposition: str = "attachment"
) -> str:
    """Build a header-injection-safe ``Content-Disposition`` value.

    User-controlled filenames (uploaded attachments, user-set batch/run
    references) must never be interpolated raw into this header: a ``"`` closes
    the quoted value early and a CR/LF splits the response. This emits a
    sanitised ASCII ``filename`` fallback plus an RFC 5987 ``filename*`` that
    carries the full UTF-8 name percent-encoded (both injection-proof).
    """
    raw = (filename or "").strip()[:200] or fallback
    ascii_name = _CD_UNSAFE.sub("_", raw).strip() or fallback
    star = quote(raw, safe="")
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{star}"

_STATIC_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"geolocation=(), camera=(), microphone=(), payment=()"),
]

# The API returns JSON only — a locked-down CSP costs nothing and blocks any
# reflected/stored HTML from executing. It is NOT applied to the interactive API
# docs (Swagger/ReDoc/OpenAPI), which need to load their own assets.
_API_CSP = b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
_DOCS_PREFIXES = ("/docs", "/redoc", "/openapi.json")


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
        if not scope.get("path", "").startswith(_DOCS_PREFIXES):
            add.append((b"content-security-policy", _API_CSP))
        if settings.hsts_enabled and _is_https(scope):
            add.append(
                (
                    b"strict-transport-security",
                    f"max-age={settings.hsts_max_age}; includeSubDomains; preload".encode(),
                )
            )

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {h[0].lower() for h in headers}
                for name, value in add:
                    if name not in present:
                        headers.append((name, value))
            await send(message)

        await self.app(scope, receive, send_wrapper)
