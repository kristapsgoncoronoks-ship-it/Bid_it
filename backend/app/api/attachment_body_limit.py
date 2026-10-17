"""Bound issued-attachment multipart bytes before Starlette spools the form."""

import re

from fastapi import HTTPException, status
from starlette.responses import JSONResponse

from app.core.config import settings
from app.services import filesec


class AttachmentBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app
        self.path = re.compile(re.escape(settings.api_v1_prefix) + r"/issued/[^/]+/attachments/?$")

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not self.path.fullmatch(scope["path"])
        ):
            return await self.app(scope, receive, send)
        budget = filesec.max_bytes() + filesec.RESOURCE_BYTES["multipart_overhead"]
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = 0
        detail = filesec.too_large_message() + " (including a bounded multipart envelope)"
        if declared > budget:
            return await JSONResponse(
                {"detail": detail}, status_code=status.HTTP_413_CONTENT_TOO_LARGE
            )(scope, receive, send)
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > budget:
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail)
            return message

        return await self.app(scope, bounded_receive, send)
