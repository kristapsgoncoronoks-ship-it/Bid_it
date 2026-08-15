"""Structured application error model.

WHY THIS EXISTS
---------------
The service layer must not import the web framework to signal a business failure
(a "not found", a "conflict", a "forbidden"). Coupling a service to
`fastapi.HTTPException` makes it un-unit-testable without HTTP and leaks the web
layer downward (see the boundary test, tests/test_boundaries.py). `AppError` is
the framework-agnostic vocabulary services raise instead; the single handler in
`app.main` maps it to the API's error shape at the boundary.

WIRE CONTRACT (unchanged, deliberately)
---------------------------------------
The client-facing error body stays FastAPI-native so the existing SPA
(`frontend/src/lib/api.ts` reads `.detail`) and the whole test suite keep
working:

    {"detail": "<human message>", "code": "<stable machine code>"}

`detail` is the human message (what the SPA shows). `code` is the NEW additive
field: a stable, machine-readable slug clients can branch on instead of matching
prose. Plain `HTTPException`s raised in routes keep working and simply omit
`code`. Every error response also carries the `X-Request-ID` header (set by the
request-context middleware) for support correlation.

Raise these from services; let routes raise `HTTPException` for purely
HTTP-shaped concerns (query validation, method guards) where no domain code is
warranted. See docs/architecture/engineering-rules.md § Error handling.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for a domain/service error that maps to an HTTP response.

    `status` is the HTTP status code, `code` a stable machine slug, `message` the
    human-readable detail. Subclasses set sensible defaults; every attribute can
    still be overridden per raise-site.
    """

    status: int = 400
    code: str = "error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status
        # Optional response headers (e.g. WWW-Authenticate on a 401), so an
        # AppError-rendered auth failure is wire-identical to the HTTPException
        # path except for the additive `code` field.
        self.headers = headers

    def extra(self) -> dict:
        """Additional top-level keys to merge into the error body.

        For a refusal the client must ACT on rather than merely display — "here
        is the warning you have to show and re-submit" — prose in `detail` is not
        enough. Subclasses override; the handler in `app.main` merges the result
        without knowing what any particular error is about, and cannot overwrite
        `detail`/`code`. Empty by default, so every existing error is byte-
        identical on the wire.
        """
        return {}


class NotFoundError(AppError):
    status = 404
    code = "not_found"


class ConflictError(AppError):
    """State conflict — duplicate, stale write, illegal transition (409)."""

    status = 409
    code = "conflict"


class ValidationError(AppError):
    """Semantic validation the request schema can't express (422)."""

    status = 422
    code = "validation_error"


class PermissionError(AppError):  # noqa: A001 — deliberate domain name; shadows builtin only in this module
    status = 403
    code = "forbidden"
