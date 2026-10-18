"""Bound intake before Starlette spools forms or buffers JSON.

The historical class/module name remains compatible. Limits now cover every
supported upload, not only issued attachments. No request-body queue is kept.
"""

import asyncio
import errno
import re
from dataclasses import dataclass

from fastapi import HTTPException, status
from fastapi.exception_handlers import http_exception_handler
from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings
from app.services import filesec


async def upload_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Translate only FastAPI's wrapped multipart staging capacity failure.

    FastAPI converts parser I/O errors to a generic 400 before middleware can
    catch them. Keep every other HTTP error on the framework's existing path.
    The scope marker is internal; a client cannot supply it in a header.
    """
    cause = exc.__cause__
    if (
        request.scope.get("bidit.upload_admitted")
        and exc.status_code == 400
        and exc.detail == "There was an error parsing the body"
        and isinstance(cause, OSError)
        and cause.errno in {errno.ENOSPC, errno.EDQUOT}
    ):
        return JSONResponse(
            {
                "detail": "Upload storage is full. Check upload status before retrying. "
                "If this continues, ask your administrator to restore storage capacity.",
                "code": "upload_storage_full",
            },
            status_code=503,
            headers={"Retry-After": "1", "Cache-Control": "no-store"},
        )
    return await http_exception_handler(request, exc)


@dataclass(frozen=True)
class UploadPolicy:
    max_files: int = 1
    purpose: str | None = None
    encoded: bool = False

    @property
    def budget(self) -> int:
        return filesec.upload_body_budget(self.max_files, self.purpose, encoded=self.encoded)

    @property
    def detail(self) -> str:
        if (
            self.max_files == 1
            and self.budget
            == filesec.max_bytes(self.purpose) + filesec.RESOURCE_BYTES["multipart_overhead"]
        ):
            return filesec.too_large_message(self.purpose)
        return "Upload request too large. Send fewer or smaller files in each request."


# Full path matches, including the configurable API prefix. File counts come
# from the same owners as route/schema validation; future upload routes must be
# classified (test_upload_ingress inventories the generated API contract).
_SINGLE = (
    (r"/invoices/upload", None),
    (r"/invoices/[^/]+/attachments", None),
    (r"/masters/projects/[^/]+/documents", None),
    (r"/issuer/logo", "logo"),
    (r"/issued/[^/]+/attachments", None),
    (r"/reconciliation/import", None),
    (r"/expenses/import/bank-statement", None),
    (r"/expenses/receipt-scan", "receipt"),
    (r"/expenses/[^/]+/items/[^/]+/receipt", "receipt"),
    (r"/transport/customers/[^/]+/documents", None),
    (r"/transport/statements", None),
    (r"/transport/estimate", None),
)


class _MultipartCounter:
    """Observe headers/counts only; never buffer file data or create a spool.

    Reuse the installed parser's framing/filename semantics. Run ahead of the
    real form parser so excessive parts/headers in a chunk never reach it.
    """

    def __init__(self, boundary: bytes, max_files: int):
        self.max_files = max_files
        self.files = 0
        self.header_bytes = 0
        self.field = bytearray()
        self.value = bytearray()
        self.is_file = False
        self.parser = MultipartParser(
            boundary,
            callbacks={
                "on_part_begin": self.part_begin,
                "on_header_field": self.header_field,
                "on_header_value": self.header_value,
                "on_header_end": self.header_end,
                "on_headers_finished": self.headers_finished,
            },
        )

    def part_begin(self):
        self.is_file = False

    def _header(self, target: bytearray, data: bytes, start: int, end: int):
        self.header_bytes += end - start
        if self.header_bytes > filesec.RESOURCE_BYTES["multipart_overhead"]:
            raise HTTPException(413, "Upload headers too large. Use shorter names and fewer parts.")
        target.extend(data[start:end])

    def header_field(self, data: bytes, start: int, end: int):
        self._header(self.field, data, start, end)

    def header_value(self, data: bytes, start: int, end: int):
        self._header(self.value, data, start, end)

    def header_end(self):
        if self.field.lower() == b"content-disposition":
            _, options = parse_options_header(bytes(self.value))
            self.is_file = b"filename" in options
        self.field.clear()
        self.value.clear()

    def headers_finished(self):
        if self.is_file:
            self.files += 1
            if self.files > self.max_files:
                raise HTTPException(422, f"Attach at most {self.max_files} files per request.")


class AttachmentBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app
        prefix = re.escape(settings.api_v1_prefix)
        rules = [(path, UploadPolicy(purpose=purpose)) for path, purpose in _SINGLE]
        rules.extend(
            [
                (r"/invoices/upload/batch", UploadPolicy(filesec.MAX_BATCH_FILES)),
                (r"/email/inbound/mailgun", UploadPolicy(filesec.MAX_INBOUND_ATTACHMENTS)),
                (r"/email/inbound", UploadPolicy(filesec.MAX_INBOUND_ATTACHMENTS, encoded=True)),
            ]
        )
        self.rules = [(re.compile(prefix + path + r"/?$"), policy) for path, policy in rules]
        self.active = 0

    def policy_for(self, path: str) -> UploadPolicy | None:
        return next((policy for pattern, policy in self.rules if pattern.fullmatch(path)), None)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        policy = self.policy_for(scope["path"])
        if policy is None:
            return await self.app(scope, receive, send)
        budget = policy.budget
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = 0  # actual received bytes remain authoritative
        if declared > budget:
            return await JSONResponse(
                {"detail": policy.detail}, status_code=413, headers={"Cache-Control": "no-store"}
            )(scope, receive, send)
        # There is no await between this check and increment: atomic on this
        # ASGI worker's event loop. It is deliberately NOT a fleet-wide limit.
        if self.active >= settings.upload_max_active_requests:
            return await JSONResponse(
                {
                    "detail": "Upload capacity is busy. Wait for current uploads to finish, then retry.",
                    "code": "upload_busy",
                },
                status_code=503,
                headers={"Retry-After": "1", "Cache-Control": "no-store"},
            )(scope, receive, send)
        self.active += 1
        scope["bidit.upload_admitted"] = True
        try:
            loop = asyncio.get_running_loop()
            body_deadline = loop.time() + settings.upload_body_timeout_seconds
            body_complete = False
            content_type, options = parse_options_header(headers.get(b"content-type", b""))
            boundary = options.get(b"boundary")
            observer = (
                _MultipartCounter(boundary, policy.max_files)
                if content_type == b"multipart/form-data" and boundary is not None
                else None
            )
            received = 0

            async def bounded_receive():
                nonlocal received, body_complete
                # ASGI consumers may poll for disconnect after receiving the
                # final body. Those waits and subsequent handler work are not
                # uploads and must not inherit a body deadline.
                if body_complete:
                    return await receive()
                try:
                    remaining = body_deadline - loop.time()
                    if remaining <= 0:
                        raise TimeoutError
                    message = await asyncio.wait_for(
                        receive(), timeout=min(settings.upload_body_idle_seconds, remaining)
                    )
                except TimeoutError as exc:
                    raise HTTPException(
                        status.HTTP_408_REQUEST_TIMEOUT,
                        "Upload timed out. Use a stable connection. Check upload status before retrying.",
                        headers={"Cache-Control": "no-store"},
                    ) from exc
                if message["type"] == "http.request":
                    body = message.get("body", b"")
                    received += len(body)
                    if received > budget:
                        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, policy.detail)
                    if observer is not None:
                        try:
                            observer.parser.write(body)
                        except MultipartParseError as exc:
                            raise HTTPException(400, "Invalid multipart data.") from exc
                    body_complete = not message.get("more_body", False)
                return message

            return await self.app(scope, bounded_receive, send)
        finally:
            self.active -= 1
