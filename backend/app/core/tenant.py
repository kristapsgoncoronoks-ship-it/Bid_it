"""Defense-in-depth tenant isolation (application-level row security).

Per-route `org_id` filters are the first line of defence; this is the second.
A request-scoped `ContextVar` holds the caller's org, and a `do_orm_execute`
hook automatically ANDs `org_id == <current org>` onto every SELECT that touches
a tenant-scoped model — so even a query that forgets to filter cannot return
another tenant's rows.

Portable (SQLite + Postgres). For belt-and-braces at the database layer,
Postgres RLS is the recommended additional hardening (see docs/SECURITY note).

Scoping is bypassed (context = None) for:
  • bootstrap / unauthenticated paths (register, login, accept-invite),
  • platform-operator routes that intentionally read across tenants.
"""
from __future__ import annotations

from contextvars import ContextVar, Token

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from app.models.invitation import Invitation
from app.models.invoice import Invoice
from app.models.issued_invoice import IssuedInvoice
from app.models.issuer import IssuerProfile
from app.models.module import OrgModule
from app.models.user import User
from app.models.vendor import Vendor

# Every model that carries an `org_id`. Child tables (LineItem, IssuedInvoiceLine)
# have no org_id and are reached only via an already-scoped parent.
TENANT_MODELS = (Vendor, Invoice, User, Invitation, IssuedInvoice, OrgModule, IssuerProfile)

# None = unscoped (bootstrap / platform-operator); a string = scope to that org.
_current_org: ContextVar[str | None] = ContextVar("current_org", default=None)


def set_current_org(org_id: str | None) -> Token:
    return _current_org.set(org_id)


def get_current_org() -> str | None:
    return _current_org.get()


def reset_current_org(token: Token) -> None:
    _current_org.reset(token)


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_scope(orm_execute_state) -> None:
    if not orm_execute_state.is_select:
        return  # writes are guarded by loading the row scoped first
    org = _current_org.get()
    if org is None:
        return  # unscoped context (bootstrap / operator)
    orm_execute_state.statement = orm_execute_state.statement.options(
        *[
            with_loader_criteria(model, model.org_id == org, include_aliases=True)
            for model in TENANT_MODELS
        ]
    )


class TenantScopeMiddleware:
    """Pure-ASGI middleware that bounds the tenant context to a single request.

    Sets the context to None at the start of each request and restores it on the
    way out, so a value set during one request can never leak into another.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = _current_org.set(None)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_org.reset(token)
