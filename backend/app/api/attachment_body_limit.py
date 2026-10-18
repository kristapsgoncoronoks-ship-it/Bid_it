"""Bound intake before Starlette spools forms or buffers JSON.

The historical class/module name remains compatible. Limits now cover every
supported upload, not only issued attachments. No request-body queue is kept.
"""

import re
from dataclasses import dataclass

from fastapi import HTTPException, status
from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header
from starlette.responses import JSONResponse

from app.core.config import settings
from app.services import filesec


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
        try:
            content_type, options = parse_options_header(headers.get(b"content-type", b""))
            boundary = options.get(b"boundary")
            observer = (
                _MultipartCounter(boundary, policy.max_files)
                if content_type == b"multipart/form-data" and boundary
                else None
            )
            received = 0

            async def bounded_receive():
                nonlocal received
                message = await receive()
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
                return message

            return await self.app(scope, bounded_receive, send)
        finally:
            self.active -= 1
