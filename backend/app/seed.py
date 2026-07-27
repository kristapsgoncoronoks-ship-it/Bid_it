"""Seed a demo tenant with realistic invoices so the dashboard has data.

Run: `python -m app.seed`  (idempotent — clears the demo org first).
Login: demo@invoiceiq.app / demo1234
"""

from __future__ import annotations

import asyncio
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete

from app.core.database import SessionLocal, engine
from app.core.security import hash_password
from app.models import Base
from app.models.invoice import Invoice, InvoiceStatus, LineItem
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.models.vendor import Vendor
from app.services import memberships

DEMO_EMAIL = "demo@invoiceiq.app"

# Extra tenants (name, plan, status) so the platform operator view isn't lonely.
EXTRA_TENANTS = [
    ("Baltic Haulage OÜ", "starter", "active"),
    ("Nordic Freight AB", "pro", "active"),
    ("Adria Logistik d.o.o.", "trial", "suspended"),
]
# Every organization name this seed owns — cleared on reset so re-seeding is idempotent.
DEMO_ORG_NAMES = ["Demo Logistics Ltd", *(t[0] for t in EXTRA_TENANTS)]

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


# Codes that MATCH the cost-allocation master seeded in `_seed_costing`, so the
# Slice-2 backfill resolves the demo invoices' free-text tags to real links.
_DEMO_CC_CODES = ("CC-1000", "CC-1100", "CC-2000", "CC-3000")
_DEMO_DEPT_CODES = ("OPS", "FIN", "SALES")


async def seed() -> None:
    rng = random.Random(42)  # deterministic demo data
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        # Reset every org this seed owns. Deleting the org cascades to its rows
        # (FK ON); the explicit user delete also clears any orphan left by an
        # older run that predates FK enforcement, so re-seeding is idempotent.
        await db.execute(delete(Organization).where(Organization.name.in_(DEMO_ORG_NAMES)))
        await db.execute(delete(User).where(User.email == DEMO_EMAIL))
        await db.commit()

        org = Organization(name="Demo Logistics Ltd")
        db.add(org)
        await db.flush()

        owner = User(
            org_id=org.id,
            email=DEMO_EMAIL,
            name="Demo Owner",
            hashed_password=hash_password("demo1234"),
            role=UserRole.owner,
            is_platform_admin=True,  # so the demo shows the operator view
            is_expense_approver=True,  # the owner approves expenses by default
        )
        db.add(owner)
        org.plan = "pro"

        # A few extra tenants so the platform operator view isn't lonely.
        extra_owners: list[tuple[str, User]] = []
        for i, (tname, plan, tstatus) in enumerate(EXTRA_TENANTS):
            t = Organization(name=tname, plan=plan, status=tstatus)
            db.add(t)
            await db.flush()
            extra = User(
                org_id=t.id,
                email=f"owner{i}@{tname.split()[0].lower()}.test",
                name="Owner",
                hashed_password=hash_password("demo1234"),
                role=UserRole.owner,
                is_expense_approver=True,
            )
            db.add(extra)
            extra_owners.append((t.id, extra))

        # Membership dual-write (Slice 6b/6d): get_current_user requires a LIVE
        # membership in the caller's active org, so a seeded user WITHOUT one can
        # authenticate but is then rejected on every request ("logged in, thrown
        # straight out"). Mirror what register() does for real signups.
        await db.flush()
        await memberships.ensure(
            db,
            org_id=org.id,
            user_id=owner.id,
            role=UserRole.owner,
            is_expense_approver=True,
            email=owner.email,
            name=owner.name,
        )
        for tid, u in extra_owners:
            await memberships.ensure(
                db,
                org_id=tid,
                user_id=u.id,
                role=UserRole.owner,
                is_expense_approver=True,
                email=u.email,
                name=u.name,
            )

        vendors = []
        for name, country, cat in VENDORS:
            v = Vendor(org_id=org.id, name=name, country=country, category=cat)
            db.add(v)
            vendors.append(v)
        await db.flush()

        # AP invoices (both the domestic loop below and the FX loop further down)
        # are collected here so they can be driven through the REAL review →
        # approval → payment-run workflow after they're flushed (R3): the legacy
        # `status` set below is not what cash-position/payment-runs/dashboard
        # read — they read `workflow_state`, which must never diverge from it.
        ap_invoices: list[Invoice] = []

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
                            description=f"{(vendor.category or '').title()} service",
                            category=rng.choice(CATEGORIES)
                            if rng.random() < 0.3
                            else vendor.category,
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
                ap_inv = Invoice(
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
                    # Free-text cost-allocation tags (the master-table links are
                    # resolved from these by the Slice-2 backfill below).
                    cost_center=_DEMO_CC_CODES[count % len(_DEMO_CC_CODES)],
                    department=_DEMO_DEPT_CODES[count % len(_DEMO_DEPT_CODES)],
                    line_items=items,
                )
                db.add(ap_inv)
                ap_invoices.append(ap_inv)
        # --- FX: seed ECB rates + a few foreign-currency invoices ---
        from app.services import fx

        await fx.ensure_seed_rates(db, today)

        vendor_by_name = {v.name: v for v in vendors}
        foreign = [
            ("AWS", "USD", Decimal("12500.00"), date(2026, 5, 15)),
            ("Slack", "USD", Decimal("4200.00"), date(2026, 4, 10)),
            ("AWS", "USD", Decimal("9800.00"), date(2026, 6, 5)),
            ("WeWork", "GBP", Decimal("8600.00"), date(2026, 3, 20)),
            ("WeWork", "GBP", Decimal("7300.00"), date(2026, 6, 12)),
            ("Lufthansa", "CHF", Decimal("5400.00"), date(2026, 5, 2)),
        ]
        for vname, ccy, amount, issue in foreign:
            fx_vendor = vendor_by_name.get(vname)
            if fx_vendor is None:
                continue
            resolved = await fx.resolve_rate(db, ccy, issue)
            if resolved is None:
                continue
            # Stated rate ~2.5% WORSE than ECB (a lower rate ⇒ fewer foreign units
            # per EUR ⇒ more EUR paid) — the classic bank FX markup to recover.
            stated = (resolved.rate * Decimal("0.975")).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP
            )
            total_eur = _q(amount / stated)
            count += 1
            fx_inv = Invoice(
                org_id=org.id,
                vendor_id=fx_vendor.id,
                invoice_number=f"INV-{2026}-{count:04d}",
                issue_date=issue,
                due_date=issue + timedelta(days=30),
                currency=ccy,
                status=InvoiceStatus.paid,
                subtotal=amount,
                tax_amount=Decimal("0"),
                total=amount,
                total_eur=total_eur,
                fx_rate=stated,
                fx_source="stated",
                line_items=[
                    LineItem(
                        description=f"{(fx_vendor.category or '').title()} service ({ccy})",
                        category=fx_vendor.category,
                        quantity=Decimal("1"),
                        unit_price=amount,
                        amount=amount,
                        tax_rate=Decimal("0"),
                    )
                ],
            )
            db.add(fx_inv)
            ap_invoices.append(fx_inv)

        # --- Issued invoices (accounts-receivable) so the issuing reports have data ---
        issued = await _seed_issued(db, org.id, rng)
        # --- Partners with pre-invoicing workflow + penalty invoicing ---
        partner_ct = await _seed_partners(db, org.id)
        # --- Cost-allocation master data (Slice 1) ---
        costing_ct = await _seed_costing(db, org.id)
        # --- Slice 2: resolve the invoices' free-text tags to master links ---
        from app.services import costing, currencies, payments, tax_codes

        await db.flush()  # SessionLocal has autoflush off; make invoices visible
        linked = await costing.backfill_invoice_links(db, org.id)
        # --- Slice 3c: seed the AR payment ledger from the settled invoices ---
        ledger = await payments.backfill_ledger(db, org.id)
        # --- Slice 4a / 5a: seed the tenant's reference catalogues ---
        tax_ct = await tax_codes.seed_standard(db, org.id)
        cur_ct = await currencies.seed_standard(db, org.id)
        # --- R3: drive every AP invoice through the REAL review/approval/
        # payment-run workflow so `workflow_state` (what cash-position/payment
        # -runs/dashboard read) never diverges from the legacy `status` (what
        # the Invoices list badge reads) — see docs/plan/plan-a/wo/WO-28-R3.md.
        ap_workflow = await _drive_ap_workflow(db, org.id, owner, ap_invoices, rng)

        await db.commit()
        print(f"Seeded '{org.name}' with {count} invoices across {len(vendors)} vendors.")
        print(f"Issued {issued} outbound invoices (paid/overdue/open mix).")
        print(f"Seeded {ledger} AR payment-ledger entries (Slice 3c).")
        print(f"Seeded {tax_ct} tax codes (Slice 4a), {cur_ct} currencies (Slice 5a).")
        print(f"Created {partner_ct} partners (workflow + penalty demo).")
        print(
            f"Created {costing_ct} cost-allocation master records (departments/cost-centers/projects)."
        )
        print(
            f"Linked invoices to master data (Slice 2 backfill): "
            f"{linked['cost_center']} cost-center, {linked['department']} department."
        )
        print(
            f"Drove {len(ap_invoices)} AP invoices through the real review->approval "
            f"workflow across {ap_workflow['_payment_runs']} payment run(s) "
            f"(workflow_state mix: {ap_workflow})."
        )
        print(f"Login: {DEMO_EMAIL} / demo1234")


async def _seed_costing(db, org_id: str) -> int:
    """A small, realistic cost-allocation master set for the demo tenant:
    departments, cost centers rolled up to them, and a couple of projects."""
    from app.models.costing import CostCenter, Department, Project

    deps = {
        "OPS": "Operations",
        "FIN": "Finance",
        "SALES": "Sales & Marketing",
    }
    dep_ids: dict[str, str] = {}
    for code, name in deps.items():
        d = Department(org_id=org_id, code=code, name=name)
        db.add(d)
        await db.flush()
        dep_ids[code] = d.id

    cost_centers = [
        ("CC-1000", "Fleet & Logistics", "OPS"),
        ("CC-1100", "Warehouse", "OPS"),
        ("CC-2000", "Accounting", "FIN"),
        ("CC-3000", "Field Sales", "SALES"),
    ]
    for code, name, dep in cost_centers:
        db.add(CostCenter(org_id=org_id, code=code, name=name, department_id=dep_ids[dep]))

    projects = [
        ("PRJ-BALTIC", "Baltic Expansion 2026", "active"),
        ("PRJ-ERP", "ERP Migration", "active"),
        ("PRJ-2025Q4", "Q4-2025 Cost Review", "closed"),
    ]
    for code, name, status in projects:
        db.add(Project(org_id=org_id, code=code, name=name, status=status))

    await db.flush()
    return len(deps) + len(cost_centers) + len(projects)


async def _seed_issued(db, org_id: str, rng: random.Random) -> int:
    """Create a spread of ISSUED invoices — several partners, a paid/overdue/open
    mix — plus a complete issuer profile and the enabled issuing module, so the
    invoice-reports surface is populated in the demo."""
    import json

    from app.models.issued_invoice import IssuedInvoice, IssuedInvoiceLine
    from app.services import issuer as issuer_svc
    from app.services import modules as modules_svc
    from app.services import vat as vat_svc

    profile = await issuer_svc.get_or_create(db, org_id)
    profile.legal_name = "Demo Logistics Ltd"
    profile.vat_number = "EE100200300"
    profile.registration_number = "EE-16000000"
    profile.address_line1 = "Tartu mnt 10"
    profile.city = "Tallinn"
    profile.postal_code = "10145"
    profile.country = "EE"
    profile.email = "billing@demologistics.test"
    profile.iban = "EE471000001020145685"
    profile.bic = "EEUHEE2X"
    profile.payment_terms_days = 30
    profile.default_penalty_rate = Decimal("8")  # 8% p.a. default late-payment interest
    await db.flush()
    await modules_svc.set_enabled(db, org_id, "issuing", True)

    partners = [
        ("Meridian Freight OÜ", "EE100111222", "EE", "ap@meridianfreight.test"),
        ("Baltic Cold Chain AS", "EE100333444", "EE", "invoices@balticcold.test"),
        ("Vilnius Logistics UAB", "LT100555666", "LT", "finance@vilniuslog.test"),
        ("Riga Port Services SIA", "LV40103777888", "LV", "ap@rigaport.test"),
        ("Helsinki Haul Oy", "FI29999999", "FI", "laskut@helsinkihaul.test"),
    ]
    today = date(2026, 7, 1)
    schemes = ["standard", "standard", "standard", "reverse_charge", "intra_eu"]
    count = 0
    for m in range(6):  # ~6 months of history
        month_start = today - timedelta(days=30 * m)
        for _ in range(rng.randint(2, 4)):
            name, vat_no, country, buyer_email = rng.choice(partners)
            scheme = rng.choice(schemes)
            n_lines = rng.randint(1, 3)
            raw = [
                {
                    "description": rng.choice(
                        [
                            "Freight forwarding",
                            "Warehousing",
                            "Customs brokerage",
                            "Last-mile delivery",
                        ]
                    ),
                    "quantity": Decimal(rng.randint(1, 12)),
                    "unit": "C62",
                    "unit_price": _q(Decimal(rng.uniform(80, 1200))),
                    "vat_rate": Decimal("22") if scheme == "standard" else Decimal("0"),
                }
                for _ in range(n_lines)
            ]
            result = vat_svc.compute(raw, scheme)

            issue = month_start - timedelta(days=rng.randint(0, 18))
            due = issue + timedelta(days=30)
            number = f"{profile.invoice_prefix}{issue.year}-{profile.next_number:04d}"
            profile.next_number += 1

            # Settlement: older invoices mostly paid; recent ones open; some overdue.
            amount_paid = Decimal("0")
            paid_date = None
            roll = rng.random()
            if due < today and roll < 0.7:  # paid
                amount_paid = result.total
                paid_date = due - timedelta(days=rng.randint(-5, 20))
            elif roll < 0.85 and result.total > 0:  # partial
                amount_paid = _q(result.total * Decimal("0.4"))

            db.add(
                IssuedInvoice(
                    org_id=org_id,
                    number=number,
                    issue_date=issue,
                    due_date=due,
                    currency="EUR",
                    buyer_name=name,
                    buyer_email=buyer_email,
                    buyer_vat_number=vat_no,
                    buyer_country=country,
                    penalty_rate=profile.default_penalty_rate,  # inherit the 8% p.a. default
                    seller_json=json.dumps(issuer_svc.seller_snapshot(profile)),
                    vat_scheme=scheme,
                    note=vat_svc.SCHEME_NOTES.get(scheme),
                    subtotal=result.subtotal,
                    tax_total=result.tax_total,
                    total=result.total,
                    amount_paid=amount_paid,
                    paid_date=paid_date,
                    lines=[
                        IssuedInvoiceLine(
                            position=i + 1,
                            description=li["description"],
                            quantity=li["quantity"],
                            unit=li["unit"],
                            unit_price=li["unit_price"],
                            vat_rate=li["vat_rate"],
                            net_amount=li["net_amount"],
                        )
                        for i, li in enumerate(result.lines)
                    ],
                )
            )
            count += 1
    return count


async def _seed_partners(db, org_id: str) -> int:
    """Two partners demonstrating the pre-invoicing workflow + penalty invoicing:
    one fully signed with overdue invoices (penalty-ready), one awaiting signature."""
    import json
    from datetime import date, timedelta

    from app.models.issued_invoice import IssuedInvoice, IssuedInvoiceLine
    from app.models.partner import Partner, PartnerDocument
    from app.services import issuer as issuer_svc

    profile = await issuer_svc.get_or_create(db, org_id)

    # Partner A — contract + acceptance both signed → ready; penalty enabled.
    ready = Partner(
        org_id=org_id,
        name="Northwind Traders GmbH",
        email="ap@northwind.test",
        vat_number="DE311111111",
        country="DE",
        requires_contract=True,
        requires_acceptance=True,
        penalty_enabled=True,
        penalty_rate=Decimal("12"),
    )
    db.add(ready)
    await db.flush()
    db.add(
        PartnerDocument(
            org_id=org_id,
            partner_id=ready.id,
            kind="contract",
            title="Framework agreement 2026",
            status="signed",
            signed_by="J. Schmidt",
            signed_date=date(2026, 1, 15),
        )
    )
    db.add(
        PartnerDocument(
            org_id=org_id,
            partner_id=ready.id,
            kind="acceptance_act",
            title="Acceptance act — Q1 delivery",
            status="signed",
            signed_by="J. Schmidt",
            signed_date=date(2026, 2, 1),
        )
    )

    # Two overdue invoices linked to Partner A so a penalty invoice can be generated.
    for i, amount in enumerate([Decimal("4200.00"), Decimal("2600.00")], start=1):
        profile.next_number += 1
        number = f"{profile.invoice_prefix}2026-P{profile.next_number:03d}"
        issue = date(2026, 1, 10 + i)
        db.add(
            IssuedInvoice(
                org_id=org_id,
                partner_id=ready.id,
                kind="standard",
                number=number,
                issue_date=issue,
                due_date=issue + timedelta(days=30),
                currency="EUR",
                buyer_name=ready.name,
                buyer_email=ready.email,
                buyer_vat_number=ready.vat_number,
                buyer_country=ready.country,
                penalty_rate=Decimal("12"),
                seller_json=json.dumps(issuer_svc.seller_snapshot(profile)),
                vat_scheme="standard",
                subtotal=amount,
                tax_total=Decimal("0"),
                total=amount,
                lines=[
                    IssuedInvoiceLine(
                        position=1,
                        description="Freight forwarding",
                        quantity=Decimal("1"),
                        unit="C62",
                        unit_price=amount,
                        vat_rate=Decimal("0"),
                        net_amount=amount,
                    )
                ],
            )
        )

    # Partner B — documents added but UNSIGNED → invoicing blocked (shows the gate).
    pending = Partner(
        org_id=org_id,
        name="Contoso Baltic SIA",
        email="finance@contoso.test",
        vat_number="LV40199999999",
        country="LV",
        requires_contract=True,
        requires_acceptance=True,
        penalty_enabled=False,
    )
    db.add(pending)
    await db.flush()
    db.add(
        PartnerDocument(
            org_id=org_id,
            partner_id=pending.id,
            kind="contract",
            title="Framework agreement (draft)",
            status="draft",
        )
    )
    return 2


async def _drive_ap_workflow(
    db, org_id: str, owner: User, invoices: list[Invoice], rng: random.Random
) -> dict[str, int]:
    """Advance every seeded AP invoice through the REAL review -> approval ->
    payment-run endpoint FUNCTIONS (not a hand-set enum), so `workflow_state` —
    what cash-position/payment-runs/the dashboard actually read — never diverges
    from the legacy aging `status` the Invoices list badge reads (R3,
    docs/plan/plan-a/wo/WO-28-R3.md). These are plain `async def`s: FastAPI's
    Depends/Annotated machinery only activates through the real HTTP dispatcher,
    so calling them directly with `current=owner, db=db` runs the exact same
    body (audit records, webhook emit, best-effort mailer.send) with zero
    network I/O, since the fresh demo org has no webhook endpoints and
    `settings.smtp_enabled` defaults False.

    The demo owner (`is_platform_admin=True`) is the ONLY user in the demo org,
    so the payment-run maker<>checker control (WO-9) is satisfied via the SAME
    explicit, audited `override_sod=True` path a real single-admin org would
    use — never a silent bypass; every override is recorded to the audit log
    exactly as it would be for a live customer."""
    from app.api.routes import invoice_review
    from app.api.routes import payment_runs as payment_run_routes
    from app.schemas.approval import DecisionIn, SubmitIn, TransitionIn
    from app.schemas.payment_run import RunApprove, RunCreate, RunPay

    to_pay: list[Invoice] = []
    for inv in invoices:
        review = await invoice_review.submit(
            inv.id, SubmitIn(version=inv.version), current=owner, db=db
        )
        review = await invoice_review.approve(
            inv.id, DecisionIn(version=review.version), current=owner, db=db
        )
        if inv.status == InvoiceStatus.paid:
            # Full cycle: schedule now, settle via a real payment run below.
            await invoice_review.transition(
                inv.id,
                TransitionIn(version=review.version, target="scheduled_for_payment"),
                current=owner,
                db=db,
            )
            to_pay.append(inv)
        elif inv.status == InvoiceStatus.pending and rng.random() < 0.35:
            # Some pending invoices are already queued for payment (not yet
            # paid) so the payment-run candidate pool isn't empty either —
            # legacy `status` stays 'pending', which is not a contradiction:
            # "scheduled to be paid soon" is still "not yet paid".
            await invoice_review.transition(
                inv.id,
                TransitionIn(version=review.version, target="scheduled_for_payment"),
                current=owner,
                db=db,
            )
        # The rest of 'pending' and every 'overdue' invoice stays 'approved' —
        # an open payable; ap_status.status_of derives OVERDUE from the
        # (already-past) due_date at read time, never from a stored flag.

    # Settle every 'paid'-status invoice through a REAL payment run (creates the
    # AP ledger entries + run history, exactly like a customer's data) — batched
    # so the demo's Payment Runs screen shows several runs, not one giant one.
    n_runs = 0
    for start in range(0, len(to_pay), 7):
        batch = to_pay[start : start + 7]
        run = await payment_run_routes.create_run(
            RunCreate(invoice_ids=[i.id for i in batch], method="bank_transfer"),
            current=owner,
            db=db,
        )
        n_runs += 1
        run = await payment_run_routes.approve_run(
            run.id, RunApprove(version=run.version, override_sod=True), current=owner, db=db
        )
        await payment_run_routes.pay_run(
            run.id,
            RunPay(version=run.version, reference=f"SEED-RUN-{n_runs:02d}", override_sod=True),
            current=owner,
            db=db,
        )

    counts: dict[str, int] = {}
    for inv in invoices:
        counts[inv.workflow_state.value] = counts.get(inv.workflow_state.value, 0) + 1
    counts["_payment_runs"] = n_runs
    return counts


if __name__ == "__main__":
    asyncio.run(seed())
