"""Transport routes slice 2 — the CUSTOMER lifecycle and per-country
activation ladder over HTTP (WO-77; the WO-73 / R44 services).

WHY THESE ROUTES EXIST AT ALL
-----------------------------
`customer_lifecycle.py`'s own module docstring records the gap this closes
verbatim: *"Role enforcement is structural at the (future) route layer …
no `api/routes/transport/*` route exists yet, so the admin-click requirement
lands as the documented intent for that route's permission."* Every
transition here is that explicit admin click — never a side effect of
ingestion, claim building or the close — and each is audited old→new by the
service (§4.16).

Thin controllers ONLY (engineering-rules §3), the WO-76 pattern: parse →
structural permission gate → call the already-gated service → shape the
response. Refusals are the SERVICE's own (`not_a_prospect`,
`lifecycle_transition_invalid`, `invalid_country`, `country_not_requested`,
`country_transition_invalid`, `entity_not_found`, `module_not_enabled`) and
reach the client through the one `app.main` handler (§4.20).

AUTHORIZATION: router-level `VAT_READ`; every transition overrides to
`VAT_WRITE`. Activating a customer does not submit or lock anything — it
governs whether a FUTURE submission may proceed (the R44 gate reads this
state inside `lock.submit_claim`, which is itself VAT_SUBMIT-gated). No new
permission member (§10).

THE GATE STAYS IN THE SERVICE: these routes only move state; the fail-CLOSED
`enforce_activation` predicate is untouched and still runs inside
`submit_claim`. Reading the lifecycle blocks nothing and changes nothing.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, status

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core import authz
from app.models.transport.claimant_document import DOC_KINDS, VatClaimantDocument
from app.models.transport.customer_lifecycle import VatCountryActivation, VatCustomerLifecycle
from app.schemas.transport_admin import (
    ActivationIn,
    ClaimantDocumentListOut,
    ClaimantDocumentOut,
    CountryActivationOut,
    ExpiringDocumentListOut,
    ExpiringDocumentOut,
    LifecycleOut,
)
from app.services import documents, filesec
from app.services.transport import claimant_documents
from app.services.transport import customer_lifecycle as lifecycle_svc

# Structural authorization (ADR-0024): router-level VAT_READ; every
# transition declares VAT_WRITE per-route below.
router = APIRouter(
    prefix="/transport/customers",
    tags=["transport"],
    dependencies=[Depends(require_perm(authz.Permission.VAT_READ))],
)
_WRITE = [Depends(require_perm(authz.Permission.VAT_WRITE))]


def _country_out(row: VatCountryActivation) -> CountryActivationOut:
    return CountryActivationOut(id=row.id, country=row.country, status=row.status)


def _lifecycle_out(
    entity_id: str,
    lifecycle: VatCustomerLifecycle | None,
    countries: list[VatCountryActivation],
) -> LifecycleOut:
    return LifecycleOut(
        entity_id=entity_id,
        id=lifecycle.id if lifecycle is not None else None,
        status=lifecycle.status if lifecycle is not None else None,
        countries=[_country_out(c) for c in countries],
    )


async def _overview(db, org_id: str, entity_id: str) -> LifecycleOut:
    lifecycle, countries = await lifecycle_svc.lifecycle_overview(db, org_id, entity_id)
    return _lifecycle_out(entity_id, lifecycle, countries)


@router.get("/{entity_id}/lifecycle", response_model=LifecycleOut)
async def get_lifecycle(entity_id: str, current: CurrentUser, db: DbSession):
    """The customer's lifecycle status + every country-activation row. A
    `null` status means "never onboarded" — a meaningful absence the R44 gate
    treats exactly like not-active, so it is reported, not 404'd; the 404
    keys off the ENTITY (§4.4, opaque cross-tenant)."""
    return await _overview(db, current.org_id, entity_id)


@router.post("/{entity_id}/prospect", response_model=LifecycleOut, dependencies=_WRITE)
async def add_prospect(entity_id: str, current: CurrentUser, db: DbSession):
    """`(none) → prospect` (F1) — idempotent, and it NEVER downgrades a real
    client of any status: an existing row is returned unchanged and audits
    nothing."""
    await lifecycle_svc.add_prospect(db, current.org_id, entity_id)
    await db.commit()
    return await _overview(db, current.org_id, entity_id)


@router.post("/{entity_id}/promote", response_model=LifecycleOut, dependencies=_WRITE)
async def promote_prospect(entity_id: str, current: CurrentUser, db: DbSession):
    """`prospect → pending` — the onboarding handoff. Any other state refuses
    409 `not_a_prospect`: promotion and activation are separate deliberate
    steps, never skippable."""
    await lifecycle_svc.promote_prospect(db, current.org_id, entity_id)
    await db.commit()
    return await _overview(db, current.org_id, entity_id)


@router.post("/{entity_id}/activation", response_model=LifecycleOut, dependencies=_WRITE)
async def set_activation(entity_id: str, body: ActivationIn, current: CurrentUser, db: DbSession):
    """F1's `pending ↔ active` toggle — the explicit admin click that opens
    (or closes) the customer to submissions. An illegal edge (from prospect,
    from inactive, or an idempotent repeat) refuses 409
    `lifecycle_transition_invalid`."""
    await lifecycle_svc.set_activation(db, current.org_id, entity_id, body.active)
    await db.commit()
    return await _overview(db, current.org_id, entity_id)


@router.post("/{entity_id}/inactive", response_model=LifecycleOut, dependencies=_WRITE)
async def set_inactive(entity_id: str, current: CurrentUser, db: DbSession):
    """`active → inactive` — the churn edge / commercial off-switch: the R44
    gate refuses the next submission the moment this lands, while every
    historical claim is untouched. Terminal in this slice (no re-onboarding
    edge is harvested — recorded in the service, never invented here)."""
    await lifecycle_svc.set_inactive(db, current.org_id, entity_id)
    await db.commit()
    return await _overview(db, current.org_id, entity_id)


@router.post(
    "/{entity_id}/countries/{country}/request", response_model=LifecycleOut, dependencies=_WRITE
)
async def request_country(entity_id: str, country: str, current: CurrentUser, db: DbSession):
    """`(none) → requested` — the first half of the linear country ladder
    (request and receive the power of attorney). Idempotent and never a
    downgrade; a malformed code refuses 422 `invalid_country`."""
    await lifecycle_svc.request_country(db, current.org_id, entity_id, country)
    await db.commit()
    return await _overview(db, current.org_id, entity_id)


@router.post(
    "/{entity_id}/countries/{country}/activation",
    response_model=LifecycleOut,
    dependencies=_WRITE,
)
async def set_country_activation(
    entity_id: str, country: str, body: ActivationIn, current: CurrentUser, db: DbSession
):
    """`requested ↔ active` (F3's explicit admin click). Activating a country
    that was never requested refuses 409 `country_not_requested` — the
    harvested ladder is linear, and the request step is where the country's
    document set gets gathered; any other illegal edge is 409
    `country_transition_invalid`."""
    await lifecycle_svc.set_country_activation(db, current.org_id, entity_id, country, body.active)
    await db.commit()
    return await _overview(db, current.org_id, entity_id)


# --------------------------------------------------------------------------- #
# Claimant documents (WO-AB, G2.10 slice 2)
#
# These live on the CUSTOMER router rather than a new one because a held
# document is an attribute of the claimant, evaluated per claim — the same
# grain as the lifecycle rows above. The `check_type="document"` checklist
# rules read exactly what these routes write.
# --------------------------------------------------------------------------- #


# A signed instrument that a human scanned — deliberately NOT the invoice set.
# CSV/XML/JSON are data files; letting one satisfy "a valid power of attorney is
# on file" would make a legal-document rule pass on a spreadsheet.
CLAIMANT_DOC_KINDS = frozenset({"pdf", "png", "jpeg"})


def _document_out(row: VatClaimantDocument) -> ClaimantDocumentOut:
    return ClaimantDocumentOut(
        id=row.id,
        entity_id=row.entity_id,
        kind=row.kind,
        country=row.country,
        sha256=row.sha256,
        size=row.size,
        mime=row.mime,
        filename=row.filename,
        valid_from=row.valid_from,
        valid_until=row.valid_until,
        uploaded_by=row.uploaded_by,
    )


def _parse_day(value: str | None, field: str) -> date | None:
    """A date arrives as a STRING from a multipart form. Parsed with
    `date.fromisoformat`, refused with the field's own name — never silently
    dropped, which would store "no expiry" for a document the operator
    believed they had dated."""
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"'{value}' is not a valid {field} (expected YYYY-MM-DD)",
        )


@router.get("/{entity_id}/documents", response_model=ClaimantDocumentListOut)
async def list_claimant_documents(entity_id: str, current: CurrentUser, db: DbSession):
    """Every document held for this claimant, newest first within each kind.
    Read-only and commits nothing."""
    rows = await claimant_documents.list_documents(db, current.org_id, entity_id)
    return ClaimantDocumentListOut(
        entity_id=entity_id,
        documents=[_document_out(r) for r in rows],
        kinds=list(DOC_KINDS),
    )


@router.post("/{entity_id}/documents", response_model=ClaimantDocumentOut, dependencies=_WRITE)
async def upload_claimant_document(
    entity_id: str,
    current: CurrentUser,
    db: DbSession,
    file: UploadFile,
    kind: str = Form(description=f"One of: {', '.join(DOC_KINDS)}"),
    country: str | None = Form(
        default=None,
        description="ISO-2 refund country for a country-scope document "
        "(a power of attorney); omit for a customer-scope one.",
    ),
    valid_from: str | None = Form(default=None, description="YYYY-MM-DD"),
    valid_until: str | None = Form(
        default=None,
        description="YYYY-MM-DD. Omitted means NO STATED EXPIRY, which is not "
        "the same as expired — the checklist reads it as permanently valid.",
    ),
):
    """Register one held document: security gate, store the bytes, record the
    ownership and validity.

    The dates are parsed BEFORE the file is read, for the same reason
    `upload_statement` resolves its entity first: a caller whose expiry is
    malformed should be refused without this process storing bytes on their
    behalf.

    The bytes go through `documents.store` — the one binary choke point — so
    the object also lands in the Slice 5d registry; this route never writes
    storage directly.
    """
    starts = _parse_day(valid_from, "valid_from")
    ends = _parse_day(valid_until, "valid_until")

    content = await file.read()
    if len(content) > filesec.max_bytes():
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, filesec.too_large_message())
    try:
        filesec.check(file.filename or "document.pdf", content, allowed=CLAIMANT_DOC_KINDS)
    except filesec.FileRejected as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc))

    sha, size = await documents.store(
        "claimant-documents",
        current.org_id,
        content,
        file.content_type,
        db=db,
        filename=file.filename,
        uploaded_by=current.email,
    )
    row = await claimant_documents.record(
        db,
        current.org_id,
        entity_id,
        kind=kind,
        sha256=sha,
        size=size,
        country=country,
        mime=file.content_type,
        filename=file.filename,
        valid_from=starts,
        valid_until=ends,
        uploaded_by=current.email,
    )
    await db.commit()
    return _document_out(row)


@router.delete(
    "/{entity_id}/documents/{document_id}",
    response_model=ClaimantDocumentOut,
    dependencies=_WRITE,
)
async def delete_claimant_document(
    entity_id: str, document_id: str, current: CurrentUser, db: DbSession
):
    """Remove one held document. The BYTES are deliberately left in object
    storage: they are content-addressed and may be shared with another row or
    another owner, so deleting them here could unmake a document this
    workspace still holds. What is removed is the claim to hold it."""
    row = await claimant_documents.remove(db, current.org_id, document_id)
    await db.commit()
    return _document_out(row)


@router.get("/documents/expiring", response_model=ExpiringDocumentListOut)
async def expiring_claimant_documents(
    current: CurrentUser,
    db: DbSession,
    within_days: int = Query(default=claimant_documents.EXPIRY_HORIZON_DAYS, ge=0, le=365),
):
    """§3.E's expiry chase board, workspace-wide. Already-lapsed documents are
    IN the list with a negative `days_left` — see `ExpiringDocumentOut`."""
    today = date.today()
    rows = await claimant_documents.expiring(db, current.org_id, within_days=within_days)
    return ExpiringDocumentListOut(
        within_days=within_days,
        documents=[
            ExpiringDocumentOut(
                id=r.id,
                entity_id=r.entity_id,
                kind=r.kind,
                country=r.country,
                filename=r.filename,
                # Narrowed by the query's own `is_not(None)` filter; mypy
                # cannot see through it.
                valid_until=r.valid_until,  # type: ignore[arg-type]
                days_left=(r.valid_until - today).days,  # type: ignore[operator]
            )
            for r in rows
        ],
    )
