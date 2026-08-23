"""The PUBLIC calendar feed (WO-B2): Google/Apple/Microsoft poll this URL.

Unauthenticated by design — the per-user secret token IS the credential
(PUBLIC_ROUTES-listed with that reason, the auth-token precedent). Tenant
resolution mirrors the email-intake webhook exactly: an UNSCOPED token
lookup, then the query runs under the resolved tenant's explicit scope,
and an unknown token 404s with nothing to enumerate.

Serves ONLY the token owner's own assignments — the same narrowing the
authenticated list applies to non-planners. No financial figures ever
appear in an event body (design rule, tested).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response

from app.api.deps import DbSession
from app.core.tenant import reset_current_org, set_current_org
from app.services import ics, scheduling

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("/feed/{token}.ics")
async def calendar_feed(token: str, db: DbSession):
    resolved = await scheduling.resolve_feed_token(db, token)
    if resolved is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown feed")
    org_id, user_id = resolved
    scope = set_current_org(org_id)
    try:
        rows, names = await scheduling.feed_rows(db, org_id, user_id)
        body = ics.render(rows, names)
    finally:
        reset_current_org(scope)
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={"Cache-Control": "private, max-age=300"},
    )
