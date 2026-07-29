"""Vendor master-data business logic (WO-2) — the payment-redirection control.

`Vendor.iban` becomes the creditor account of a real SEPA credit transfer, so
this module is the single write path for vendors and enforces, in order:

1. **Format** — every IBAN/BIC passes `core.bank_id` (ISO 13616 + MOD-97)
   before it is stored OR even recorded on a change request; a malformed
   account number never enters the system in any state.
2. **The fraud-safety invariant** (Fleet Fuel BA §3.B / R23): on an EXISTING
   vendor the protected fields (`iban`, `tax_id`) are never written directly.
   A change lands as a `pending` VendorChangeRequest; a SECOND user holding
   SETTINGS_MANAGE approves it (maker ≠ checker → 403 `maker_is_checker`).
   A first-time capture (stored value empty) applies directly — there is no
   established payment route to redirect yet — and a BRAND-NEW vendor created
   already carrying an iban/tax_id lands `provisional` (a payment run refuses
   it unless explicitly confirmed; see services/payment_run.py).
3. **Optimistic concurrency** — `version` mirrors the invoice-review pattern:
   a stale client version is 409 `stale_version`; `None` opts out.
4. **Audit** — every mutation records old→new in the same transaction; an
   IBAN appears in audit meta ONLY masked (last 4 + length, `bank_id.mask_iban`)
   because audit rows are immutable and broadly readable.

Cross-tenant reads stay opaque: a foreign vendor/request id is a plain 404
(`NotFoundError`), indistinguishable from a nonexistent id. Services raise
`AppError`; routes map. Note: Vendor has no company-registration-number column
today — when one is added it MUST join `PROTECTED_FIELDS`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import bank_id
from app.core.errors import AppError, ConflictError, NotFoundError, ValidationError
from app.models.document import Document
from app.models.vendor import VENDOR_ACTIVE, VENDOR_PROVISIONAL, Vendor
from app.models.vendor_change_request import (
    CR_APPROVED,
    CR_PENDING,
    CR_REJECTED,
    VendorChangeRequest,
)
from app.schemas.vendor import VendorCreate, VendorUpdate
from app.services import audit

# The fields the second-approver workflow protects on an existing vendor.
PROTECTED_FIELDS = ("iban", "tax_id")


def _masked(field: str, value: str | None) -> str | None:
    """Audit-safe rendering of a protected value: IBANs are masked to their
    last 4 characters + length; tax ids are public identifiers and stay full."""
    return bank_id.mask_iban(value) if field == "iban" else value


async def get_or_create_vendor(db: AsyncSession, org_id: str, name: str) -> Vendor:
    """Resolve a vendor by exact stripped name, creating a bare (active) row if
    absent — the invoice-capture path. Flushes; caller commits."""
    name = name.strip()
    vendor = await db.scalar(select(Vendor).where(Vendor.org_id == org_id, Vendor.name == name))
    if vendor is None:
        vendor = Vendor(org_id=org_id, name=name)
        db.add(vendor)
        await db.flush()
    return vendor


async def get_by_name(db: AsyncSession, org_id: str, name: str) -> Vendor | None:
    """Exact-match, read-only lookup by name (the same `org_id, name` key
    `get_or_create_vendor` matches on) — unlike that function, this NEVER
    creates a row. Added for the transport vertical's note→invoice resolution
    (`app.services.transport.invoice_match`, G2.4): checking whether a
    supplier is a registered vendor must not have the side effect of
    registering one — ADR-P3 rule 2 ("reads the core through services, never
    a raw model join") is exactly why this lives here rather than a
    cross-domain `select(Vendor)` inside `services/transport/`."""
    return await db.scalar(
        select(Vendor).where(Vendor.org_id == org_id, Vendor.name == name.strip())
    )


async def list_vendors(db: AsyncSession, org_id: str) -> list[Vendor]:
    """Vendors feed pickers/filters; a defensive cap bounds the response without
    loading an unbounded tenant table into memory."""
    rows = await db.scalars(
        select(Vendor).where(Vendor.org_id == org_id).order_by(Vendor.name).limit(1000)
    )
    return list(rows)


async def _load(db: AsyncSession, org_id: str, vendor_id: str) -> Vendor:
    vendor = await db.scalar(select(Vendor).where(Vendor.org_id == org_id, Vendor.id == vendor_id))
    if vendor is None:
        # Opaque 404 (invariant §4.4): a cross-tenant id and a nonexistent id
        # are indistinguishable — object-id guessing yields zero information.
        raise NotFoundError("Vendor not found")
    return vendor


def _normalize(field: str, value: str | None) -> str | None:
    """Boundary normalization + format validation. Applied before storing AND
    before recording a change request — an invalid IBAN/BIC never enters the
    system even as a pending value."""
    if value is None:
        return None
    if field == "iban":
        return bank_id.assert_iban(value)
    if field == "bic":
        return bank_id.assert_bic(value)
    if field == "country":
        return value.upper()
    stripped = value.strip()
    return stripped or None


async def create_vendor(db: AsyncSession, org_id: str, body: VendorCreate) -> Vendor:
    """Create a vendor. One carrying captured bank/tax identity at birth lands
    `provisional` — nobody has independently verified that identity yet, so a
    payment run refuses it until explicitly confirmed. Flushes + audits; caller
    commits (audit persists atomically with the row, invariant §4.16)."""
    name = body.name.strip()
    existing = await db.scalar(select(Vendor).where(Vendor.org_id == org_id, Vendor.name == name))
    if existing is not None:
        raise ConflictError("Vendor already exists", code="vendor_exists")
    iban = _normalize("iban", body.iban)
    bic = _normalize("bic", body.bic)
    tax_id = _normalize("tax_id", body.tax_id)
    vendor = Vendor(
        org_id=org_id,
        name=name,
        tax_id=tax_id,
        country=_normalize("country", body.country),
        category=_normalize("category", body.category),
        iban=iban,
        bic=bic,
        status=VENDOR_PROVISIONAL if (iban or tax_id) else VENDOR_ACTIVE,
    )
    db.add(vendor)
    await db.flush()
    await audit.record(
        db,
        audit.A.VENDOR_CREATE,
        target_type="vendor",
        target_id=vendor.id,
        meta={
            "name": vendor.name,
            "status": vendor.status,
            "iban": _masked("iban", vendor.iban),
            "tax_id": vendor.tax_id,
            "country": vendor.country,
        },
    )
    return vendor


async def _open_request(
    db: AsyncSession, org_id: str, vendor_id: str, field: str
) -> VendorChangeRequest | None:
    return await db.scalar(
        select(VendorChangeRequest).where(
            VendorChangeRequest.org_id == org_id,
            VendorChangeRequest.vendor_id == vendor_id,
            VendorChangeRequest.field == field,
            VendorChangeRequest.status == CR_PENDING,
        )
    )


async def update_vendor(
    db: AsyncSession,
    org_id: str,
    vendor_id: str,
    body: VendorUpdate,
    *,
    actor_id: str,
    actor_email: str | None,
) -> tuple[Vendor, list[VendorChangeRequest]]:
    """Update a vendor. Non-protected fields apply immediately (audited with
    old→new). A CHANGE to a protected field with a stored value does NOT write —
    it creates a pending VendorChangeRequest for a second approver. Returns the
    (possibly partially updated) vendor and any change requests created.
    Flushes + audits; caller commits."""
    vendor = await _load(db, org_id, vendor_id)
    if body.version is not None and body.version != vendor.version:
        raise ConflictError(
            f"This vendor was changed by someone else (your version {body.version}, "
            f"current {vendor.version}). Reload and re-apply your changes.",
            code="stale_version",
        )
    fields = body.model_dump(exclude_unset=True)
    fields.pop("version", None)
    source_document_id = fields.pop("source_document_id", None)
    if source_document_id is not None:
        doc = await db.scalar(
            select(Document).where(Document.org_id == org_id, Document.id == source_document_id)
        )
        if doc is None:
            raise ValidationError(
                "source_document_id does not reference a document in this workspace.",
                code="invalid_source_document",
            )

    changed: dict[str, dict[str, str | None]] = {}
    requests: list[VendorChangeRequest] = []
    for field, raw in fields.items():
        new_value = _normalize(field, raw)
        old_value = getattr(vendor, field)
        if new_value == old_value:
            continue  # no-op — neither a write nor a request
        if field in PROTECTED_FIELDS and old_value:
            # The fraud-safety invariant: an ESTABLISHED protected value is
            # never overwritten in-request. Record the request; a second
            # approver applies it. One open request per (vendor, field).
            if await _open_request(db, org_id, vendor.id, field) is not None:
                raise ConflictError(
                    f"A pending change request for '{field}' already exists on "
                    f"vendor '{vendor.name}'. Approve or reject it first.",
                    code="duplicate_change_request",
                )
            req = VendorChangeRequest(
                org_id=org_id,
                vendor_id=vendor.id,
                field=field,
                old_value=old_value,
                new_value=new_value,
                status=CR_PENDING,
                requested_by=actor_id,
                requested_by_email=actor_email,
                source_document_id=source_document_id,
            )
            db.add(req)
            await db.flush()
            await audit.record(
                db,
                audit.A.VENDOR_CHANGE_REQUEST,
                target_type="vendor_change_request",
                target_id=req.id,
                meta={
                    "vendor_id": vendor.id,
                    "field": field,
                    "old": _masked(field, old_value),
                    "new": _masked(field, new_value),
                    "source_document_id": source_document_id,
                },
            )
            requests.append(req)
        else:
            # Non-protected field, or the FIRST capture of a protected one
            # (nothing stored yet ⇒ no established payment route to redirect).
            setattr(vendor, field, new_value)
            changed[field] = {
                "old": _masked(field, old_value),
                "new": _masked(field, new_value),
            }

    if changed:
        vendor.version += 1
        await audit.record(
            db,
            audit.A.VENDOR_UPDATE,
            target_type="vendor",
            target_id=vendor.id,
            meta={"changes": changed},
        )
    await db.flush()
    return vendor, requests


async def list_change_requests(
    db: AsyncSession, org_id: str, *, status: str | None = CR_PENDING
) -> list[tuple[VendorChangeRequest, str]]:
    """Change requests (newest first) with their vendor's name, optionally
    filtered by status (None = all)."""
    stmt = (
        select(VendorChangeRequest, Vendor.name)
        .join(Vendor, Vendor.id == VendorChangeRequest.vendor_id)
        .where(VendorChangeRequest.org_id == org_id, Vendor.org_id == org_id)
        .order_by(VendorChangeRequest.requested_at.desc())
        .limit(500)
    )
    if status is not None:
        stmt = stmt.where(VendorChangeRequest.status == status)
    rows = await db.execute(stmt)
    return [(req, name) for req, name in rows.all()]


async def pending_change_count(db: AsyncSession, org_id: str, *, exclude_requester_id: str) -> int:
    """Pending protected-field change requests awaiting a decision, excluding the
    caller's own (maker≠checker: `approve_change` refuses `maker_is_checker`, so
    their own requests are not actionable by them — §4.8). Canonical read for the
    composed home dashboard (WO-16)."""
    return int(
        await db.scalar(
            select(func.count()).where(
                VendorChangeRequest.org_id == org_id,
                VendorChangeRequest.status == CR_PENDING,
                VendorChangeRequest.requested_by != exclude_requester_id,
            )
        )
        or 0
    )


async def pending_requests_for(
    db: AsyncSession, org_id: str, vendor_ids: list[str]
) -> dict[str, list[VendorChangeRequest]]:
    """Open protected-field requests grouped by vendor id (batch — one query)."""
    if not vendor_ids:
        return {}
    rows = await db.scalars(
        select(VendorChangeRequest).where(
            VendorChangeRequest.org_id == org_id,
            VendorChangeRequest.vendor_id.in_(vendor_ids),
            VendorChangeRequest.status == CR_PENDING,
        )
    )
    grouped: dict[str, list[VendorChangeRequest]] = {}
    for req in rows:
        grouped.setdefault(req.vendor_id, []).append(req)
    return grouped


async def _load_request(db: AsyncSession, org_id: str, request_id: str) -> VendorChangeRequest:
    req = await db.scalar(
        select(VendorChangeRequest).where(
            VendorChangeRequest.org_id == org_id, VendorChangeRequest.id == request_id
        )
    )
    if req is None:
        raise NotFoundError("Change request not found")  # opaque, §4.4
    return req


async def approve_change(
    db: AsyncSession,
    org_id: str,
    request_id: str,
    *,
    approver_id: str,
    approver_email: str | None,
    note: str | None = None,
) -> tuple[VendorChangeRequest, Vendor]:
    """Apply a pending protected-field change. Segregation of duties (§4.8):
    the requester can NEVER approve their own request — 403 `maker_is_checker` —
    regardless of role, because a single compromised account must not be able to
    both plant and activate a new payee account. Flushes + audits; caller
    commits."""
    req = await _load_request(db, org_id, request_id)
    if req.status != CR_PENDING:
        raise ConflictError(
            f"This change request is already {req.status}.", code="change_request_decided"
        )
    if req.requested_by == approver_id:
        raise AppError(
            "You requested this change; a different approver must apply it.",
            code="maker_is_checker",
            status=403,
        )
    vendor = await _load(db, org_id, req.vendor_id)
    value = req.new_value
    # Defence in depth: re-validate at apply time (the stored request could
    # predate a rule fix, or have been altered below the app).
    if req.field == "iban" and value is not None:
        value = bank_id.assert_iban(value)
    setattr(vendor, req.field, value)
    vendor.version += 1
    now = datetime.now(UTC)
    req.status = CR_APPROVED
    req.decided_by = approver_id
    req.decided_by_email = approver_email
    req.decided_at = now
    req.decision_note = note
    await audit.record(
        db,
        audit.A.VENDOR_CHANGE_APPROVE,
        target_type="vendor",
        target_id=vendor.id,
        meta={
            "request_id": req.id,
            "field": req.field,
            "old": _masked(req.field, req.old_value),
            "new": _masked(req.field, value),
            "requested_by": req.requested_by,
        },
    )
    await db.flush()
    return req, vendor


async def reject_change(
    db: AsyncSession,
    org_id: str,
    request_id: str,
    *,
    approver_id: str,
    approver_email: str | None,
    note: str | None,
) -> VendorChangeRequest:
    """Refuse a pending change: the stored value stays untouched; the reason is
    recorded on the request and in the audit trail. Rejection is deliberately
    NOT maker-checked — withdrawing one's own captured value applies nothing and
    is safe. Flushes + audits; caller commits."""
    req = await _load_request(db, org_id, request_id)
    if req.status != CR_PENDING:
        raise ConflictError(
            f"This change request is already {req.status}.", code="change_request_decided"
        )
    req.status = CR_REJECTED
    req.decided_by = approver_id
    req.decided_by_email = approver_email
    req.decided_at = datetime.now(UTC)
    req.decision_note = note
    await audit.record(
        db,
        audit.A.VENDOR_CHANGE_REJECT,
        target_type="vendor_change_request",
        target_id=req.id,
        meta={"vendor_id": req.vendor_id, "field": req.field, "note": note},
    )
    await db.flush()
    return req
