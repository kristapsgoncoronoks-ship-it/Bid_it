"""Sales customer master CRUD (the billing counterparty for issued invoices).

Gated behind the issuing module. Viewing needs ISSUED_READ; managing needs
ISSUED_WRITE. Delete is a soft delete (is_active=False) so existing invoice links
survive.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core.authz import Permission as _P
from app.models.customer import Customer, CustomerContact
from app.schemas.customer import CustomerCreate, CustomerOut, CustomerUpdate
from app.services import audit, crm, modules, portal

# Structural authorization (ADR-0024): every customer route needs at least
# ISSUED_READ; the mutating routes declare ISSUED_WRITE per-route below.
router = APIRouter(
    prefix="/customers",
    tags=["customers"],
    dependencies=[Depends(require_perm(_P.ISSUED_READ))],
)
_WRITE = [Depends(require_perm(_P.ISSUED_WRITE))]

_SCALAR_FIELDS = (
    "name",
    "legal_name",
    "vat_number",
    "registration_number",
    "email",
    "phone",
    "address_line1",
    "address_line2",
    "city",
    "postal_code",
    "country",
    "ship_address_line1",
    "ship_address_line2",
    "ship_city",
    "ship_postal_code",
    "ship_country",
    "payment_terms_days",
    "default_currency",
    "notes",
    "is_active",
)


async def _guard(db: DbSession, org_id: str) -> None:
    await modules.require_enabled(db, org_id, "issuing")


async def _load(db: DbSession, org_id: str, cid: str) -> Customer:
    c = await db.scalar(
        select(Customer)
        .where(Customer.id == cid, Customer.org_id == org_id)
        .options(selectinload(Customer.contacts))
    )
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    return c


@router.get("", response_model=list[CustomerOut])
async def list_customers(
    current: CurrentUser, db: DbSession, include_inactive: bool = Query(default=False)
):
    await _guard(db, current.org_id)
    filters = [Customer.org_id == current.org_id]
    if not include_inactive:
        filters.append(Customer.is_active.is_(True))
    rows = await db.scalars(
        select(Customer)
        .where(*filters)
        .options(selectinload(Customer.contacts))
        .order_by(Customer.name.asc())
    )
    return [CustomerOut.model_validate(c) for c in rows]


@router.post(
    "",
    response_model=CustomerOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=_WRITE,
)
async def create_customer(body: CustomerCreate, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    c = Customer(org_id=current.org_id, **body.model_dump(exclude={"contacts"}))
    c.contacts = [CustomerContact(org_id=current.org_id, **ct.model_dump()) for ct in body.contacts]
    db.add(c)
    await db.commit()
    await db.refresh(c, attribute_names=["contacts"])
    return CustomerOut.model_validate(c)


@router.get("/{customer_id}", response_model=CustomerOut)
async def get_customer(customer_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    return CustomerOut.model_validate(await _load(db, current.org_id, customer_id))


@router.patch("/{customer_id}", response_model=CustomerOut, dependencies=_WRITE)
async def update_customer(
    customer_id: str, body: CustomerUpdate, current: CurrentUser, db: DbSession
):
    await _guard(db, current.org_id)
    c = await _load(db, current.org_id, customer_id)
    fields = body.model_fields_set
    for key in _SCALAR_FIELDS:
        if key in fields:
            setattr(c, key, getattr(body, key))
    if body.contacts is not None:  # replace the whole contact list
        c.contacts = [
            CustomerContact(org_id=current.org_id, **ct.model_dump()) for ct in body.contacts
        ]
    await db.commit()
    await db.refresh(c, attribute_names=["contacts"])
    return CustomerOut.model_validate(c)


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=_WRITE)
async def delete_customer(customer_id: str, current: CurrentUser, db: DbSession):
    """Soft delete — deactivate the customer (invoice history keeps its link)."""
    await _guard(db, current.org_id)
    c = await _load(db, current.org_id, customer_id)
    c.is_active = False
    await db.commit()


# --- CRM light (WO-H): notes, lifecycle, the derived timeline ---------------


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class NoteOut(BaseModel):
    id: str
    body: str
    created_by: str | None
    created_at: str


class LifecycleIn(BaseModel):
    lifecycle: str


def _note_out(n) -> NoteOut:
    return NoteOut(
        id=n.id, body=n.body, created_by=n.created_by, created_at=n.created_at.isoformat()
    )


def _raise_crm(exc: crm.CrmError) -> None:
    if isinstance(exc, crm.NotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("/{customer_id}/notes", response_model=list[NoteOut])
async def list_customer_notes(customer_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    try:
        return [_note_out(n) for n in await crm.list_notes(db, current.org_id, customer_id)]
    except crm.CrmError as exc:
        _raise_crm(exc)


@router.post(
    "/{customer_id}/notes",
    response_model=NoteOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=_WRITE,
)
async def add_customer_note(customer_id: str, body: NoteIn, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    try:
        note = await crm.add_note(
            db, current.org_id, customer_id, body=body.body, created_by=current.email
        )
    except crm.CrmError as exc:
        _raise_crm(exc)
    await audit.record(
        db,
        "customer.note_add",
        target_type="customer",
        target_id=customer_id,
        meta={"note_id": note.id},
    )
    await db.commit()
    await db.refresh(note)
    return _note_out(note)


@router.delete(
    "/{customer_id}/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_WRITE,
)
async def delete_customer_note(customer_id: str, note_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    try:
        destroyed = await crm.delete_note(db, current.org_id, customer_id, note_id)
    except crm.CrmError as exc:
        _raise_crm(exc)
    await audit.record(
        db,
        "customer.note_delete",
        target_type="customer",
        target_id=customer_id,
        meta=destroyed,
    )
    await db.commit()


@router.put("/{customer_id}/lifecycle", response_model=CustomerOut, dependencies=_WRITE)
async def set_customer_lifecycle(
    customer_id: str, body: LifecycleIn, current: CurrentUser, db: DbSession
):
    await _guard(db, current.org_id)
    try:
        customer, prior = await crm.set_lifecycle(db, current.org_id, customer_id, body.lifecycle)
    except crm.CrmError as exc:
        _raise_crm(exc)
    await audit.record(
        db,
        "customer.lifecycle_set",
        target_type="customer",
        target_id=customer_id,
        meta={"lifecycle": customer.lifecycle, "prior": prior},
    )
    await db.commit()
    await db.refresh(customer, attribute_names=["contacts"])
    return CustomerOut.model_validate(customer)


# --- Client portal link management (WO-I). The token is a capability the
# workspace HANDS OUT; issuing/regenerating/revoking are audited like every
# credential event, and the portal path itself is assembled client-side.


class PortalLinkOut(BaseModel):
    token: str
    path: str
    created_at: str


def _link_out(t) -> PortalLinkOut:
    return PortalLinkOut(
        token=t.token, path=f"/portal/{t.token}", created_at=t.created_at.isoformat()
    )


@router.get("/{customer_id}/portal-link", response_model=PortalLinkOut, dependencies=_WRITE)
async def get_portal_link(customer_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    try:
        row = await portal.get_or_create_link(
            db, current.org_id, customer_id, created_by=current.email
        )
    except portal.NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await audit.record(
        db,
        "customer.portal_link_issue",
        target_type="customer",
        target_id=customer_id,
        meta={"token_id": row.id},
    )
    await db.commit()
    await db.refresh(row)
    return _link_out(row)


@router.post(
    "/{customer_id}/portal-link/regenerate", response_model=PortalLinkOut, dependencies=_WRITE
)
async def regenerate_portal_link(customer_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    try:
        row = await portal.regenerate_link(
            db, current.org_id, customer_id, created_by=current.email
        )
    except portal.NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await audit.record(
        db,
        "customer.portal_link_regenerate",
        target_type="customer",
        target_id=customer_id,
        meta={"token_id": row.id},
    )
    await db.commit()
    await db.refresh(row)
    return _link_out(row)


@router.delete(
    "/{customer_id}/portal-link", status_code=status.HTTP_204_NO_CONTENT, dependencies=_WRITE
)
async def revoke_portal_link(customer_id: str, current: CurrentUser, db: DbSession):
    await _guard(db, current.org_id)
    try:
        revoked = await portal.revoke_link(db, current.org_id, customer_id)
    except portal.NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    await audit.record(
        db,
        "customer.portal_link_revoke",
        target_type="customer",
        target_id=customer_id,
        meta={"revoked": revoked},
    )
    await db.commit()


@router.get("/{customer_id}/timeline")
async def customer_timeline(customer_id: str, current: CurrentUser, db: DbSession):
    """The DERIVED activity stream — notes plus what the system already
    knows (offers, projects, invoices, emails), newest first, never curated."""
    await _guard(db, current.org_id)
    try:
        return {"events": await crm.timeline(db, current.org_id, customer_id)}
    except crm.CrmError as exc:
        _raise_crm(exc)
