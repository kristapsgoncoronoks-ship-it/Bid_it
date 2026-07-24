from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.vendor import Vendor
from app.schemas.vendor import VendorCreate, VendorOut, VendorUpdate

router = APIRouter(prefix="/vendors", tags=["vendors"])


async def get_or_create_vendor(db: DbSession, org_id: str, name: str) -> Vendor:
    name = name.strip()
    vendor = await db.scalar(select(Vendor).where(Vendor.org_id == org_id, Vendor.name == name))
    if vendor is None:
        vendor = Vendor(org_id=org_id, name=name)
        db.add(vendor)
        await db.flush()
    return vendor


@router.get("", response_model=list[VendorOut])
async def list_vendors(current: CurrentUser, db: DbSession) -> list[Vendor]:
    # Vendors feed pickers/filters; a defensive cap bounds the response without
    # loading an unbounded tenant table into memory.
    rows = await db.scalars(
        select(Vendor).where(Vendor.org_id == current.org_id).order_by(Vendor.name).limit(1000)
    )
    return list(rows)


@router.post("", response_model=VendorOut, status_code=status.HTTP_201_CREATED)
async def create_vendor(body: VendorCreate, current: CurrentUser, db: DbSession) -> Vendor:
    existing = await db.scalar(
        select(Vendor).where(Vendor.org_id == current.org_id, Vendor.name == body.name.strip())
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Vendor already exists")
    vendor = Vendor(
        org_id=current.org_id,
        name=body.name.strip(),
        tax_id=body.tax_id,
        country=body.country.upper() if body.country else None,
        category=body.category,
        iban=body.iban.replace(" ", "").upper() if body.iban else None,
        bic=body.bic.upper() if body.bic else None,
    )
    db.add(vendor)
    await db.commit()
    await db.refresh(vendor)
    return vendor


@router.patch("/{vendor_id}", response_model=VendorOut)
async def update_vendor(
    vendor_id: str, body: VendorUpdate, current: CurrentUser, db: DbSession
) -> Vendor:
    vendor = await db.scalar(
        select(Vendor).where(Vendor.org_id == current.org_id, Vendor.id == vendor_id)
    )
    if vendor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vendor not found")
    fields = body.model_dump(exclude_unset=True)
    if "iban" in fields:
        vendor.iban = fields["iban"].replace(" ", "").upper() if fields["iban"] else None
    if "bic" in fields:
        vendor.bic = fields["bic"].upper() if fields["bic"] else None
    for key in ("tax_id", "category"):
        if key in fields:
            setattr(vendor, key, fields[key])
    if "country" in fields:
        vendor.country = fields["country"].upper() if fields["country"] else None
    await db.commit()
    await db.refresh(vendor)
    return vendor
