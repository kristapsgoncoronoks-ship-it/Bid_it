"""Audit 2026-09-05, P2 batch 3 — the database keeps the promises (SQLite side).

DB-007/008/009  cascades that erased records are RESTRICT
DB-011          the six financial state columns are closed sets (CHECK)
DB-013          `invoices.amount_paid` is verified against the supplier ledger
DB-017          the issuer link is the composite tenant-safe key
BE-014          `issuer.lock` never commits inside the caller's transaction
BE-020          the inbound webhook is bounded where it is parsed

The composite SET NULL semantics (DB-017/018) are Postgres-only and live in
`test_db017_db018_composite_set_null_pg.py`. The migration's pre-flight gate
is exercised here on a bare table so the refusal is a tested behaviour, not a
comment.
"""

from __future__ import annotations

import base64
import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models import Base
from app.models.billing_payment import BillingPayment
from app.models.expense import ExpenseReport, ReimbursementBatch
from app.models.invoice import Invoice
from app.models.issued_invoice import IssuedInvoice
from app.models.payment_run import PaymentRun
from app.models.supplier_payment import SupplierPayment
from app.models.transport.vat_claim import VatRefundClaim
from app.models.user import User
from app.models.vendor import Vendor
from app.schemas.email_intake import MAX_ATTACHMENT_BASE64_CHARS, MAX_INBOUND_ATTACHMENTS
from app.services import audit, integrity, issuer

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic" / "versions" / "c4d6e8f0a2b4_db_integrity_batch3.py"


async def _org_and_user(auth_client, db_session) -> tuple[str, str]:
    me = (await auth_client.get("/api/v1/auth/me")).json()
    user = await db_session.scalar(select(User).where(User.email == "owner@acme.io"))
    return me["organization"]["id"] if "organization" in me else user.org_id, user.id


async def _enforce_foreign_keys(db_session) -> None:
    """The app engine switches SQLite's foreign-key enforcement on per
    connection (`app.core.database`); the suite's in-memory engine does not
    (QA-011, logged with this batch), so a delete test must switch it on for
    the connection it runs on — outside a transaction, or SQLite ignores it."""
    await db_session.commit()
    conn = await db_session.connection()
    await conn.execute(text("PRAGMA foreign_keys=ON"))
    assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar() == 1


def _invoice(org_id: str, vendor_id: str, **extra) -> Invoice:
    return Invoice(
        org_id=org_id,
        vendor_id=vendor_id,
        invoice_number=extra.pop("invoice_number", "INV-1"),
        issue_date=date(2026, 5, 1),
        due_date=date(2026, 6, 1),
        currency="EUR",
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("21.00"),
        total=Decimal("121.00"),
        **extra,
    )


# --------------------------------------------------------------------------- #
# DB-007/008/009 — RESTRICT
# --------------------------------------------------------------------------- #


def test_the_five_cascades_are_restrict_in_the_model_layer():
    """Structural: the metadata says RESTRICT for exactly the five links the
    finding named, so a model edit cannot quietly bring a cascade back."""
    expected = {
        ("audit_events", "org_id", "organizations"),
        ("archived_invoices", "org_id", "organizations"),
        ("invoices", "vendor_id", "vendors"),
        ("expense_reports", "employee_id", "users"),
        ("expense_transactions", "employee_id", "users"),
    }
    seen = set()
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            key = (table.name, fk.parent.name, fk.column.table.name)
            if key in expected:
                assert fk.ondelete == "RESTRICT", f"{key} is {fk.ondelete}, expected RESTRICT"
                seen.add(key)
    assert seen == expected


@pytest.mark.asyncio
async def test_a_vendor_with_invoices_cannot_be_hard_deleted(auth_client, db_session):
    org_id, _ = await _org_and_user(auth_client, db_session)
    vendor = Vendor(org_id=org_id, name="Perf Fuels OU")
    db_session.add(vendor)
    await db_session.flush()
    db_session.add(_invoice(org_id, vendor.id))
    await _enforce_foreign_keys(db_session)
    vendor_id = vendor.id

    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        await db_session.execute(text("DELETE FROM vendors WHERE id = :id"), {"id": vendor_id})
        await db_session.commit()
    await db_session.rollback()
    still_there = await db_session.scalar(
        text("SELECT COUNT(*) FROM vendors WHERE id = :id"), {"id": vendor_id}
    )
    assert still_there == 1


@pytest.mark.asyncio
async def test_an_organisation_with_an_audit_trail_cannot_be_hard_deleted(auth_client, db_session):
    org_id, _ = await _org_and_user(auth_client, db_session)
    await audit.record(db_session, "invoice.create", org_id=org_id, target_type="invoice")
    await _enforce_foreign_keys(db_session)

    # Registering already wrote memberships and settings that cascade; the audit
    # trail is what must refuse, and it does — the whole statement fails.
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        await db_session.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": org_id})
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_a_user_with_expense_reports_cannot_be_hard_deleted(auth_client, db_session):
    org_id, user_id = await _org_and_user(auth_client, db_session)
    db_session.add(
        ExpenseReport(org_id=org_id, employee_id=user_id, employee_name="Owner", title="Trip")
    )
    await _enforce_foreign_keys(db_session)

    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        await db_session.commit()
    await db_session.rollback()


# --------------------------------------------------------------------------- #
# DB-011 — CHECKs on the six financial state columns
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_every_financial_state_column_refuses_a_value_outside_its_set(
    auth_client, db_session
):
    org_id, user_id = await _org_and_user(auth_client, db_session)
    entity = await issuer.get_or_create(db_session, org_id)
    rows = [
        PaymentRun(org_id=org_id, status="bogus"),
        ReimbursementBatch(org_id=org_id, status="bogus"),
        ExpenseReport(
            org_id=org_id, employee_id=user_id, employee_name="Owner", title="T", status="bogus"
        ),
        VatRefundClaim(
            org_id=org_id,
            entity_id=entity.id,
            refund_country="DE",
            ref_period="2026-Q1",
            status="bogus",
        ),
        IssuedInvoice(
            org_id=org_id,
            issue_date=date(2026, 5, 1),
            currency="EUR",
            buyer_name="Site crew BV",
            seller_json="{}",
            subtotal=Decimal("100.00"),
            tax_total=Decimal("21.00"),
            total=Decimal("121.00"),
            lifecycle="paid",  # derived, never stored — exactly the seed's old value
        ),
        BillingPayment(
            org_id=org_id,
            provider="everypay",
            reference="ref-1",
            order_reference="ord-1",
            plan_key="starter",
            amount_eur=Decimal("29.99"),
            state="bogus",
        ),
    ]
    for row in rows:
        db_session.add(row)
        with pytest.raises(IntegrityError, match="ck_"):
            await db_session.flush()
        await db_session.rollback()
        # The workspace's own rows survive the rollback of the bad insert.
        entity = await issuer.get_or_create(db_session, org_id)


def test_the_check_sets_match_the_constants_the_code_writes():
    """The migration's sets and the models' CHECK expressions name the same
    values, so the constants, the constraint and the pre-flight cannot drift."""
    mig = _migration()
    checks = {
        c.name: str(c.sqltext)
        for table in Base.metadata.tables.values()
        for c in table.constraints
        if getattr(c, "name", None) and str(c.name).startswith("ck_")
    }
    for table, column, name, allowed in mig.STATE_SETS:
        assert name in checks, f"{table}.{column} has no {name} in the model"
        for value in allowed:
            assert f"'{value}'" in checks[name], f"{name} lacks {value!r}"


def _migration():
    spec = importlib.util.spec_from_file_location("_mig_batch3", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_migration_preflight_names_the_offending_values():
    """On a bare table holding one good and two bad rows the gate reports the
    bad values with their counts — and nothing for a clean table."""
    mig = _migration()
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE payment_runs (id INTEGER PRIMARY KEY, status TEXT)"))
        conn.execute(
            text("INSERT INTO payment_runs (status) VALUES ('open'), ('bogus'), ('bogus'), (NULL)")
        )
        found = mig.offending_values(conn, "payment_runs", "status", ("open", "paid"))
        assert found == [("None", 1), ("bogus", 2)]
        conn.execute(text("DELETE FROM payment_runs WHERE status IS NULL OR status = 'bogus'"))
        assert mig.offending_values(conn, "payment_runs", "status", ("open", "paid")) == []


# --------------------------------------------------------------------------- #
# DB-013 — the AP ledger is verified
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_supplier_invoice_cache_that_disagrees_with_its_ledger_is_a_finding(
    auth_client, db_session
):
    org_id, _ = await _org_and_user(auth_client, db_session)
    vendor = Vendor(org_id=org_id, name="Perf Fuels OU")
    db_session.add(vendor)
    await db_session.flush()
    inv = _invoice(org_id, vendor.id, amount_paid=Decimal("50.00"))
    db_session.add(inv)
    await db_session.commit()

    report = await integrity.verify_ledger(db_session, org_id)
    bad = [i for i in report.issues if i.kind == "supplier_invoice_ledger"]
    assert len(bad) == 1 and bad[0].entity_id == inv.id and bad[0].problem == "mismatch"
    assert "50.00" in bad[0].detail and "0.00" in bad[0].detail

    db_session.add(
        SupplierPayment(
            org_id=org_id, invoice_id=inv.id, amount=Decimal("50.00"), paid_on=date(2026, 6, 1)
        )
    )
    await db_session.commit()
    report = await integrity.verify_ledger(db_session, org_id)
    assert not [i for i in report.issues if i.kind == "supplier_invoice_ledger"]
    assert report.ok >= 1


# --------------------------------------------------------------------------- #
# BE-014 — issuer.lock never commits
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_locking_a_workspace_with_no_issuer_creates_one_without_committing(
    auth_client, db_session, monkeypatch
):
    org_id, _ = await _org_and_user(auth_client, db_session)
    # Registration may seed a default issuer; the case under test is the
    # workspace that has NONE when the first issue transaction locks.
    await db_session.execute(text("DELETE FROM issuer_profiles WHERE org_id = :o"), {"o": org_id})
    await db_session.commit()

    async def _no_commit():
        raise AssertionError("issuer.lock committed inside the caller's transaction")

    monkeypatch.setattr(db_session, "commit", _no_commit)
    prof = await issuer.lock(db_session, org_id)
    assert prof.org_id == org_id and prof.is_default
    assert db_session.in_transaction()
    await db_session.rollback()
    # Rolled back with the caller: nothing was committed behind their back.
    assert (
        await db_session.scalar(
            select(issuer.IssuerProfile).where(issuer.IssuerProfile.org_id == org_id)
        )
        is None
    )


# --------------------------------------------------------------------------- #
# BE-020 — the inbound webhook is bounded
# --------------------------------------------------------------------------- #


def _inbound(n_attachments: int, size: int = 4) -> dict:
    blob = base64.b64encode(b"%PDF" * (size // 4 or 1)).decode()
    return {
        "to": "x@invoices.invoiceiq.test",
        "attachments": [
            {"filename": f"inv-{i}.pdf", "content_base64": blob} for i in range(n_attachments)
        ],
    }


@pytest.mark.asyncio
async def test_the_inbound_webhook_refuses_too_many_or_too_large_attachments(client, monkeypatch):
    monkeypatch.setattr(settings, "inbound_email_secret", "be020-secret")
    headers = {"X-Inbound-Secret": "be020-secret"}

    too_many = _inbound(MAX_INBOUND_ATTACHMENTS + 1)
    r = await client.post("/api/v1/email/inbound", json=too_many, headers=headers)
    assert r.status_code == 422, r.text

    body = _inbound(1)
    body["attachments"][0]["content_base64"] = "A" * (MAX_ATTACHMENT_BASE64_CHARS + 4)
    r = await client.post("/api/v1/email/inbound", json=body, headers=headers)
    assert r.status_code == 422, r.text

    # Within bounds the schema passes and the request reaches tenant resolution
    # (401 here: no such recipient token — the bound, not the payload, is under test).
    r = await client.post(
        "/api/v1/email/inbound", json=_inbound(MAX_INBOUND_ATTACHMENTS), headers=headers
    )
    assert r.status_code == 401, r.text


def test_the_attachment_cap_is_the_upload_cap_in_base64():
    assert MAX_ATTACHMENT_BASE64_CHARS == ((settings.max_upload_mb * 1024 * 1024 + 2) // 3) * 4
    assert 1 < MAX_INBOUND_ATTACHMENTS <= 50
