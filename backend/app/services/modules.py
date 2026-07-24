"""Platform module registry + per-org activation.

The platform is modular: a registry of capabilities, some `core` (always on),
some activatable add-ons the user turns on. `issuing` (EU invoice issuing) is an
add-on that also requires a complete issuer profile before it can be used.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.module import OrgModule


@dataclass(frozen=True)
class Module:
    key: str
    name: str
    description: str
    core: bool = False  # core modules are always enabled
    default: bool = False  # default enablement for non-core modules
    requires_issuer: bool = False


MODULES: tuple[Module, ...] = (
    Module(
        "analytics",
        "Analytics & benchmarking",
        "Spend dashboards and supplier benchmarking.",
        core=True,
    ),
    Module(
        "intake",
        "Invoice intake & OCR",
        "Upload PDF/XML/CSV, OCR, e-invoice recognition.",
        core=True,
    ),
    Module("fx", "FX & ECB rates", "Convert foreign invoices at ECB reference rates.", core=True),
    Module("validation", "Data validation", "AI and/or human validation of invoices.", core=True),
    Module(
        "issuing",
        "Invoice issuing (EU e-invoicing)",
        "Issue EN 16931-compliant invoices as a PDF with embedded Factur-X XML.",
        core=False,
        default=False,
        requires_issuer=True,
    ),
    Module(
        "expenses",
        "Employee expenses",
        "Employees submit receipted expense reports; managers approve and reimburse.",
        core=False,
        default=False,
    ),
    Module(
        "email_intake",
        "Email invoice intake",
        "Forward invoices to a dedicated address; attachments are auto-parsed into a review inbox.",
        core=False,
        default=False,
    ),
    Module(
        "budget",
        "Monthly budgeting",
        "Set a monthly limit per category and track received-invoice spend against it (household / personal budgeting).",
        core=False,
        default=False,
    ),
)
MODULES_BY_KEY = {m.key: m for m in MODULES}


async def enabled_keys(db: AsyncSession, org_id: str) -> set[str]:
    rows: dict[str, bool] = dict(
        (
            await db.execute(
                select(OrgModule.key, OrgModule.enabled).where(OrgModule.org_id == org_id)
            )
        )
        .tuples()
        .all()
    )
    out: set[str] = set()
    for m in MODULES:
        if m.core or rows.get(m.key, m.default):
            out.add(m.key)
    return out


async def is_enabled(db: AsyncSession, org_id: str, key: str) -> bool:
    m = MODULES_BY_KEY.get(key)
    if m is None:
        return False
    if m.core:
        return True
    row = await db.scalar(
        select(OrgModule.enabled).where(OrgModule.org_id == org_id, OrgModule.key == key)
    )
    return row if row is not None else m.default


async def require_enabled(db: AsyncSession, org_id: str, key: str) -> None:
    """Raise 403 if the module isn't active. The shared module-gate used by the
    feature routes (was a copy-pasted `_guard` in each)."""
    if not await is_enabled(db, org_id, key):
        m = MODULES_BY_KEY.get(key)
        name = m.name if m else key
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"The {name} module is not activated.")


async def set_enabled(db: AsyncSession, org_id: str, key: str, enabled: bool) -> None:
    row = await db.scalar(select(OrgModule).where(OrgModule.org_id == org_id, OrgModule.key == key))
    if row is None:
        db.add(OrgModule(org_id=org_id, key=key, enabled=enabled))
    else:
        row.enabled = enabled
    await db.commit()
