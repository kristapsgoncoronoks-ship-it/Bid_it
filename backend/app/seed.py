"""Seed a demo tenant with realistic invoices so the dashboard has data.

Run: `python -m app.seed`  (idempotent — clears the demo org first).
Login: demo@invoiceiq.app / demo1234
"""
from __future__ import annotations

import asyncio
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, select

from app.core.database import SessionLocal, engine
from app.models import Base
from app.models.invoice import Invoice, InvoiceStatus, LineItem
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.models.vendor import Vendor
from app.core.security import hash_password

DEMO_EMAIL = "demo@invoiceiq.app"

VENDORS = [
    ("AWS", "US", "cloud"),
    ("Shell Fleet", "NL", "fuel"),
    ("Staples", "US", "office"),
    ("Lufthansa", "DE", "travel"),
    ("Slack", "US", "software"),
    ("DHL", "DE", "logistics"),
    ("WeWork", "GB", "rent"),
]
CATEGORIES = ["cloud", "fuel", "office", "travel", "software", "logistics", "rent", "consulting"]
_CENTS = Decimal("0.01")


def _q(v: Decimal) -> Decimal:
    return v.quantize(_CENTS, rounding=ROUND_HALF_UP)


async def seed() -> None:
    rng = random.Random(42)  # deterministic demo data
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        # Reset demo org
        existing = await db.scalar(select(User).where(User.email == DEMO_EMAIL))
        if existing:
            await db.execute(delete(Organization).where(Organization.id == existing.org_id))
            await db.commit()

        org = Organization(name="Demo Logistics Ltd")
        db.add(org)
        await db.flush()

        db.add(
            User(
                org_id=org.id,
                email=DEMO_EMAIL,
                name="Demo Owner",
                hashed_password=hash_password("demo1234"),
                role=UserRole.owner,
            )
        )

        vendors = []
        for name, country, cat in VENDORS:
            v = Vendor(org_id=org.id, name=name, country=country, category=cat)
            db.add(v)
            vendors.append(v)
        await db.flush()

        today = date(2026, 7, 1)
        count = 0
        for month_back in range(12):
            month_start = today - timedelta(days=30 * month_back)
            for _ in range(rng.randint(4, 9)):
                vendor = rng.choice(vendors)
                n_lines = rng.randint(1, 4)
                subtotal = Decimal("0")
                tax_total = Decimal("0")
                items = []
                for _ in range(n_lines):
                    qty = Decimal(rng.randint(1, 20))
                    unit = _q(Decimal(rng.uniform(10, 900)))
                    amount = _q(qty * unit)
                    rate = Decimal(rng.choice([0, 9, 21]))
                    tax = _q(amount * rate / Decimal("100"))
                    subtotal += amount
                    tax_total += tax
                    items.append(
                        LineItem(
                            description=f"{vendor.category.title()} service",
                            category=rng.choice(CATEGORIES) if rng.random() < 0.3 else vendor.category,
                            quantity=qty,
                            unit_price=unit,
                            amount=amount,
                            tax_rate=rate,
                        )
                    )
                issue = month_start - timedelta(days=rng.randint(0, 20))
                status = rng.choices(
                    [InvoiceStatus.paid, InvoiceStatus.pending, InvoiceStatus.overdue],
                    weights=[6, 3, 1],
                )[0]
                count += 1
                db.add(
                    Invoice(
                        org_id=org.id,
                        vendor_id=vendor.id,
                        invoice_number=f"INV-{2026}-{count:04d}",
                        issue_date=issue,
                        due_date=issue + timedelta(days=30),
                        currency="EUR",
                        status=status,
                        subtotal=_q(subtotal),
                        tax_amount=_q(tax_total),
                        total=_q(subtotal + tax_total),
                        line_items=items,
                    )
                )
        await db.commit()
        print(f"Seeded '{org.name}' with {count} invoices across {len(vendors)} vendors.")
        print(f"Login: {DEMO_EMAIL} / demo1234")


if __name__ == "__main__":
    asyncio.run(seed())
