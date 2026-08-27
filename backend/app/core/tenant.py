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

from contextlib import contextmanager
from contextvars import ContextVar, Token
from functools import lru_cache

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, with_loader_criteria

from app.models.agreed_price import SupplierAgreedPrice
from app.models.approval import ApprovalPolicy, ApprovalStep
from app.models.archived_invoice import ArchivedInvoice
from app.models.audit import AuditEvent
from app.models.automation import AutomationRule, AutomationRuleVersion, AutomationRun
from app.models.bank_import import BankLine, BankStatement
from app.models.billing_payment import BillingPayment
from app.models.budget import BudgetTarget
from app.models.calendar_token import CalendarFeedToken
from app.models.capture_acknowledgement import CaptureAcknowledgement
from app.models.capture_field_memory import CaptureFieldMemory
from app.models.costing import CostCenter, Department, Project
from app.models.crm import CustomerNote, OfferStageEvent
from app.models.currency import Currency
from app.models.customer import Customer, CustomerContact
from app.models.customer_portal_token import CustomerPortalToken
from app.models.document import Document
from app.models.document_template import OrgTemplate
from app.models.document_version import DocumentVersion
from app.models.dunning_policy import DunningPolicy
from app.models.email_intake import EmailIntake, InboundInvoice
from app.models.email_message import EmailMessage
from app.models.email_token import EmailToken
from app.models.expense import (
    ExpenseComment,
    ExpenseItem,
    ExpensePolicy,
    ExpenseReport,
    ExpenseTransaction,
    ReimbursementBatch,
)
from app.models.expense_approval import ExpenseApprovalPolicy, ExpenseApprovalStep
from app.models.extraction_field import ExtractionField
from app.models.extraction_run import ExtractionRun
from app.models.inbound_channel_health import InboundChannelHealth
from app.models.invitation import Invitation
from app.models.invoice import Invoice
from app.models.invoice_collab import InvoiceAttachment, InvoiceComment
from app.models.invoice_project_split import InvoiceProjectSplit
from app.models.issued_invoice import IssuedInvoice, IssuedInvoiceAttachment
from app.models.issuer import IssuerProfile
from app.models.job import Job
from app.models.membership import Membership
from app.models.module import OrgModule
from app.models.next_action import ActionDismissal, OrgDeadline
from app.models.partner import Partner, PartnerDocument
from app.models.payment import Payment
from app.models.payment_run import PaymentRun
from app.models.project_assignment import ProjectAssignment
from app.models.project_link import ProjectCostEntry, ProjectDocument
from app.models.project_offer import InvoicingPlanRow, ProjectOffer
from app.models.receipt import Receipt
from app.models.recurring_invoice import RecurringInvoice
from app.models.retention import LegalHold, RetentionPolicy
from app.models.session import Session as SessionModel
from app.models.sso import SsoConnection
from app.models.supplier_payment import SupplierPayment
from app.models.tax_code import TaxCode
from app.models.transport.checklist_rule import VatChecklistRule
from app.models.transport.contract_term import VatSupplierContractTerm
from app.models.transport.customer_lifecycle import VatCountryActivation, VatCustomerLifecycle
from app.models.transport.excise_rate import VatExciseRate
from app.models.transport.extraction_baseline import FuelExtractionBaseline
from app.models.transport.fee_rate import VatFeeRate
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.lock import VatClaimedInvoice
from app.models.transport.note_override import VatNoteInvoiceOverride
from app.models.transport.off_invoice_rebate import VatOffInvoiceRebate
from app.models.transport.overcharge import VatOverchargeClaim
from app.models.transport.receipt_control import VatReceiptControl, VatSupplierCadence
from app.models.transport.receipt_waiver import VatReceiptWaiver
from app.models.transport.reliability_threshold import VatReliabilityThreshold
from app.models.transport.supplier_registration import SupplierVatRegistration
from app.models.transport.tie_out import FuelTieOutExpectation
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine
from app.models.usage import UsageCounter
from app.models.user import User
from app.models.vendor import Vendor
from app.models.vendor_change_request import VendorChangeRequest
from app.models.webhook import WebhookDelivery, WebhookEndpoint

# Every model that carries an `org_id`. Remaining child tables (LineItem,
# IssuedInvoiceLine) have no org_id and are reached only via an already-scoped
# parent. ExpenseItem now carries a denormalised org_id (Slice 2b) so it is
# scoped directly here rather than trusting the report join.
TENANT_MODELS = (
    ArchivedInvoice,
    AutomationRule,
    AutomationRuleVersion,
    AutomationRun,
    SupplierAgreedPrice,
    ProjectDocument,
    ProjectCostEntry,
    InvoiceProjectSplit,
    OrgTemplate,
    ProjectOffer,
    InvoicingPlanRow,
    ProjectAssignment,
    CalendarFeedToken,
    OrgDeadline,
    ActionDismissal,
    Vendor,
    VendorChangeRequest,
    Invoice,
    User,
    Invitation,
    Membership,
    IssuedInvoice,
    IssuedInvoiceAttachment,
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
    CaptureAcknowledgement,
    InboundChannelHealth,
    Receipt,
    Document,
    DocumentVersion,
    ApprovalPolicy,
    ApprovalStep,
    InvoiceComment,
    InvoiceAttachment,
    ReimbursementBatch,
    ExpensePolicy,
    ExpenseApprovalPolicy,
    ExpenseApprovalStep,
    Customer,
    CustomerContact,
    CustomerNote,
    CustomerPortalToken,
    OfferStageEvent,
    BankStatement,
    BankLine,
    SupplierPayment,
    PaymentRun,
    DunningPolicy,
    CaptureFieldMemory,
    VatRefundClaim,
    VatRefundClaimLine,
    FuelExtractionBaseline,
    FuelTransaction,
    FuelTieOutExpectation,
    SupplierVatRegistration,
    VatChecklistRule,
    VatClaimedInvoice,
    VatCountryActivation,
    VatCustomerLifecycle,
    VatExciseRate,
    VatFeeRate,
    VatReliabilityThreshold,
    VatNoteInvoiceOverride,
    VatOffInvoiceRebate,
    VatOverchargeClaim,
    VatReceiptControl,
    VatReceiptWaiver,
    VatSupplierCadence,
    VatSupplierContractTerm,
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


# (client_ip, session_id) for the request being served — the "from where" half
# of audit attribution, set next to the actor in deps and read by audit.record.
# Defaults to (None, None) outside a request (worker jobs, scripts), which is
# also the truthful value there: a purge job has no location worth inventing.
_request_ctx: ContextVar[tuple[str | None, str | None]] = ContextVar(
    "request_ctx", default=(None, None)
)


def set_request_context(ip: str | None, session_id: str | None) -> None:
    _request_ctx.set((ip, session_id))


def get_request_context() -> tuple[str | None, str | None]:
    return _request_ctx.get()


def _scope_criteria(model, org: str):
    """The tenant-visibility predicate for one model under the current org.

    Every tenant model scopes by its own `org_id` — except `User` (B1.5): a
    person can belong to several orgs, so `users.org_id` is only the ACTIVE-ORG
    pointer (repointed by org-switching), never a membership assertion. The
    users table is therefore scoped by membership EXISTENCE in the current org
    — a member whose active org is elsewhere stays visible to their other orgs
    (SCIM roster, reimbursement payees, approver resolution, GDPR scans), while
    a non-member remains at zero rows. Membership *status* is deliberately not
    part of the predicate: a suspended member is still this org's data (SCIM
    must list them as inactive; erasure must reach them) — access control is
    the live-membership gate in deps, not row visibility.
    """
    if model is User:
        return User.id.in_(select(Membership.user_id).where(Membership.org_id == org))
    return model.org_id == org


# --------------------------------------------------------------------------- #
# The recycle bin (docs/design/deletion-and-archive.md)
#
# A record in the client's bin must disappear from EVERYTHING — lists, totals,
# aging, duplicate detection, exports, VAT figures — until it is restored or
# purged. 19 query sites across 11 modules read the invoice table, and a bin that
# depends on each of them remembering is a bin that quietly makes the numbers
# wrong: today a mistaken delete is loud, but a half-applied bin is silent.
#
# So it is enforced the same way tenancy is: one criterion added to every SELECT,
# which no query can forget. `include_deleted()` is the deliberate opt-out, for
# the Trash screen, restore, and the purge — the only three things that have any
# business seeing a binned record.
# --------------------------------------------------------------------------- #

# Models carrying `deleted_at`. Keep in step with the migrations; the guard test
# fails if a model grows the column without being registered here.
SOFT_DELETE_MODELS = (
    Invoice,
    # WO-M (owner decision 2026-08-15 — the bin extends to all entities):
    ExpenseReport,
    ExpenseTransaction,
    RecurringInvoice,
    IssuedInvoiceAttachment,
)

_include_deleted: ContextVar[bool] = ContextVar("include_deleted", default=False)


@contextmanager
def include_deleted():
    """Show binned records for the duration of this block.

    Deliberately a narrow, explicit scope rather than a flag someone sets and
    forgets: the Trash screen, a restore and the purge are the whole list of
    things that should ever see a deleted row.
    """
    token = _include_deleted.set(True)
    try:
        yield
    finally:
        _include_deleted.reset(token)


@lru_cache(maxsize=512)
def _tenant_options(org: str, models: tuple) -> tuple:
    """The 80 tenant criteria for one org, built ONCE.

    These objects are immutable and read-only during compilation, so sharing one
    tuple across every statement for an org is safe — and rebuilding them per
    query was measured at ~1.6 ms of pure Python on every SELECT the process
    issues, which at ~5-7 scoped selects per request is the dominant cost of a
    request against Postgres.

    Cached on the org id (a bind parameter inside the criteria, so the compiled-
    statement cache key is unaffected either way). Bounded, because an unbounded
    cache keyed by tenant is a slow memory leak in a multi-tenant process.

    NOT the lambda form of `with_loader_criteria`: SQLAlchemy caches the lambda's
    analysed closure, which would risk baking one tenant's org id in permanently.
    That is a cross-tenant leak, not an optimisation.

    `models` is a parameter rather than a read of the module global, so the model
    registry is part of the cache KEY. Reading the global instead would freeze it
    at first use: `tests/test_tenancy_parity.py` neutralises layer 2 by patching
    `TENANT_MODELS` to `()` and asserting the leak is then detected, and against a
    cache that ignored the patch the guard would stay silently on — a self-test
    that can no longer fail, which is worse than no self-test.
    """
    return tuple(
        with_loader_criteria(model, _scope_criteria(model, org), include_aliases=True)
        for model in models
    )


@lru_cache(maxsize=8)
def _deleted_options(models: tuple) -> tuple:
    """The soft-delete criteria. No per-request input, so this is effectively
    built once — keyed on the registry for the same reason as above."""
    return tuple(
        with_loader_criteria(model, model.deleted_at.is_(None), include_aliases=True)
        for model in models
    )


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_scope(orm_execute_state) -> None:
    if not orm_execute_state.is_select:
        return  # writes are guarded by loading the row scoped first
    if orm_execute_state.is_relationship_load or orm_execute_state.is_column_load:
        # A relationship load already carries these criteria: they propagate from
        # the parent statement (`propagate_to_loaders` defaults True), so adding
        # them again appended a SECOND identical predicate per hop — three copies
        # two hops down — for no behavioural gain. A column load (refresh /
        # expired attribute) discards loader criteria entirely, so the work was
        # wasted outright. SQLAlchemy's own `do_orm_execute` docs say not to add
        # options on either.
        return

    options = () if _include_deleted.get() else _deleted_options(SOFT_DELETE_MODELS)

    org = _current_org.get()
    if org is not None:  # None = unscoped context (bootstrap / operator)
        options = _tenant_options(org, TENANT_MODELS) + options

    if options:
        orm_execute_state.statement = orm_execute_state.statement.options(*options)


# --------------------------------------------------------------------------- #
# Postgres Row-Level Security backstop (ADR-0004, defence layer 3)
#
# The ORM guard above scopes SELECTs the app issues; RLS enforces isolation at
# the DATABASE, so even a raw query, a bug above the ORM, or an unregistered
# model cannot cross tenants. RLS reads the current org from a per-transaction
# GUC, `app.current_org`, which we keep in sync with the ContextVar here.
#
# Policy contract (see the migration): when `app.current_org` is UNSET *or the
# empty string* the policy passes all rows — matching the app's "org is None
# ⇒ intentionally unscoped" semantics for bootstrap / platform-operator /
# worker-claim paths. When it is SET to a real org id, rows are restricted to
# that org. The scoped path (a normal authenticated request) always sets it,
# so that is where RLS bites.
#
# WHY both NULL and '': a per-transaction `set_config(..., true)` (`SET
# LOCAL`) never restores a custom GUC to SQL NULL once ANY transaction on that
# physical connection has set it — COMMIT, RESET, and an explicit
# `set_config(name, NULL, true)` all leave it at `''` instead, for the rest of
# that connection's life (confirmed empirically on Postgres 16; see ADR-0028
# and `tests/test_rls_connection_reuse.py`). So a connection a prior request
# scoped is NEVER NULL again — only a virgin connection is. Every RLS policy
# therefore treats both as "unscoped" (WO-27); relying on NULL alone silently
# hid rows for any unscoped/re-authenticating request on a warmed, reused
# connection, which is every connection under any real pool/load.
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
