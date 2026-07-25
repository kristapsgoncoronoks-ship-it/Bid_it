"""Sales customer master CRUD (the billing counterparty for issued invoices).

Gated behind the issuing module. Viewing needs ISSUED_READ; managing needs
ISSUED_WRITE. Delete is a soft delete (is_active=False) so existing invoice links
survive.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DbSession, require_perm
from app.core.authz import Permission as _P
from app.models.customer import Customer, CustomerContact
from app.schemas.customer import CustomerCreate, CustomerOut, CustomerUpdate
from app.services import modules

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
