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

from sqlalchemy import event, text
from sqlalchemy.orm import Session, with_loader_criteria

from app.models.audit import AuditEvent
from app.models.billing_payment import BillingPayment
from app.models.budget import BudgetTarget
from app.models.costing import CostCenter, Department, Project
from app.models.currency import Currency
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.email_intake import EmailIntake, InboundInvoice
from app.models.email_message import EmailMessage
from app.models.email_token import EmailToken
from app.models.expense import ExpenseComment, ExpenseItem, ExpenseReport, ExpenseTransaction
from app.models.extraction_field import ExtractionField
from app.models.extraction_run import ExtractionRun
from app.models.invitation import Invitation
from app.models.invoice import Invoice
from app.models.issued_invoice import IssuedInvoice
from app.models.issuer import IssuerProfile
from app.models.job import Job
from app.models.module import OrgModule
from app.models.partner import Partner, PartnerDocument
from app.models.payment import Payment
from app.models.receipt import Receipt
from app.models.recurring_invoice import RecurringInvoice
from app.models.retention import LegalHold, RetentionPolicy
from app.models.session import Session as SessionModel
from app.models.sso import SsoConnection
from app.models.tax_code import TaxCode
from app.models.usage import UsageCounter
from app.models.user import User
from app.models.vendor import Vendor
from app.models.webhook import WebhookDelivery, WebhookEndpoint

# Every model that carries an `org_id`. Remaining child tables (LineItem,
# IssuedInvoiceLine) have no org_id and are reached only via an already-scoped
# parent. ExpenseItem now carries a denormalised org_id (Slice 2b) so it is
# scoped directly here rather than trusting the report join.
TENANT_MODELS = (
    Vendor,
    Invoice,
    User,
    Invitation,
    IssuedInvoice,
    OrgModule,
    IssuerProfile,
    ExpenseReport,
    ExpenseItem,
    ExpenseTransaction,
    ExpenseComment,
    EmailIntake,
    InboundInvoice,
    EmailToken,
    BudgetTarget,
    AuditEvent,
    EmailMessage,
    Partner,
    PartnerDocument,
    RecurringInvoice,
    Payment,
    Job,
    UsageCounter,
    WebhookEndpoint,
    WebhookDelivery,
    BillingPayment,
    RetentionPolicy,
    LegalHold,
    SessionModel,
    SsoConnection,
    Department,
    CostCenter,
    Project,
    TaxCode,
    Currency,
    ExtractionRun,
    ExtractionField,
    Receipt,
    Document,
    DocumentVersion,
)

# None = unscoped (bootstrap / platform-operator); a string = scope to that org.
_current_org: ContextVar[str | None] = ContextVar("current_org", default=None)
# The acting user (id, email) for audit attribution; (None, None) = system/anon.
_current_actor: ContextVar[tuple[str | None, str | None]] = ContextVar(
    "current_actor", default=(None, None)
)


def set_current_org(org_id: str | None) -> Token:
    return _current_org.set(org_id)


def get_current_org() -> str | None:
    return _current_org.get()


def reset_current_org(token: Token) -> None:
    _current_org.reset(token)


def set_current_actor(actor_id: str | None, actor_email: str | None) -> None:
    _current_actor.set((actor_id, actor_email))


def get_current_actor() -> tuple[str | None, str | None]:
    return _current_actor.get()


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


# --------------------------------------------------------------------------- #
# Postgres Row-Level Security backstop (ADR-0004, defence layer 3)
#
# The ORM guard above scopes SELECTs the app issues; RLS enforces isolation at
# the DATABASE, so even a raw query, a bug above the ORM, or an unregistered
# model cannot cross tenants. RLS reads the current org from a per-transaction
# GUC, `app.current_org`, which we keep in sync with the ContextVar here.
#
# Policy contract (see the migration): when `app.current_org` is UNSET the policy
# passes all rows — matching the app's "org is None ⇒ intentionally unscoped"
# semantics for bootstrap / platform-operator / worker-claim paths. When it is
# SET, rows are restricted to that org. The scoped path (a normal authenticated
# request) always sets it, so that is where RLS bites.
# --------------------------------------------------------------------------- #

# `set_config(name, value, is_local=true)` == `SET LOCAL`: scoped to the current
# transaction and auto-reset on commit/rollback, so it is safe under pooling.
_RLS_SET = text("SELECT set_config('app.current_org', :org, true)")


@event.listens_for(Session, "after_begin")
def _sync_rls_org(session, transaction, connection) -> None:
    """On each new transaction, mirror the ContextVar org into the DB GUC so RLS
    restricts to it. Postgres-only; a no-op elsewhere. Covers worker jobs and any
    post-commit transaction within a request (where the org is already known)."""
    if connection.dialect.name != "postgresql":
        return
    org = _current_org.get()
    if org is not None:
        connection.execute(_RLS_SET, {"org": org})


async def apply_db_tenant(db) -> None:
    """Explicitly set the RLS GUC on the CURRENT transaction (Postgres-only).

    Needed once per request after the caller is authenticated: the request's first
    transaction typically begins during the unscoped user lookup (org not yet
    known), so `after_begin` set nothing — this closes that gap for the rest of
    that transaction. Idempotent with the event hook."""
    org = _current_org.get()
    if org is None or db.bind is None or db.bind.dialect.name != "postgresql":
        return
    await db.execute(_RLS_SET, {"org": org})


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
        actor_token = _current_actor.set((None, None))
        try:
            await self.app(scope, receive, send)
        finally:
            _current_org.reset(token)
            _current_actor.reset(actor_token)
