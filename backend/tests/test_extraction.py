"""Extraction lineage (Slice 5b): a capture run is recorded at upload (method +
source), linked to the invoice on save, listed back per invoice; failed parses
are recorded too; and the composite link is tenant-safe at the DB."""

import io

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.models.extraction_run import ExtractionRun
from app.models.invoice import Invoice
from app.models.organization import Organization

_CSV = (
    "description,category,quantity,unit_price,tax_rate,vendor,invoice_number,issue_date\n"
    "Fuel,fuel,10,1.50,21,Shell,INV-CSV-1,2026-02-01\n"
    "Toll,fuel,2,5.00,21,Shell,INV-CSV-1,2026-02-01\n"
)


@pytest.mark.asyncio
async def test_upload_records_run_and_save_links_it(auth_client):
    files = {"file": ("lines.csv", io.BytesIO(_CSV.encode()), "text/csv")}
    up = (await auth_client.post("/api/v1/invoices/upload", files=files)).json()
    assert up["method"] == "csv"
    run_id = up["extraction_run_id"]
    assert run_id and up["draft"]["extraction_run_id"] == run_id

    saved = await auth_client.post("/api/v1/invoices", json=up["draft"])
    assert saved.status_code == 201, saved.text
    inv_id = saved.json()["id"]

    lineage = (await auth_client.get(f"/api/v1/invoices/{inv_id}/extraction")).json()
    assert len(lineage) == 1
    run = lineage[0]
    assert run["id"] == run_id
    assert run["method"] == "csv" and run["status"] == "saved"
    assert run["field_count"] == 2 and run["source_filename"] == "lines.csv"
    assert len(run["source_sha256"]) == 64


@pytest.mark.asyncio
async def test_manual_invoice_has_no_lineage(auth_client):
    body = {
        "vendor_name": "AWS",
        "invoice_number": "M-1",
        "issue_date": "2026-01-15",
        "currency": "EUR",
        "status": "pending",
        "line_items": [
            {
                "description": "x",
                "category": "cloud",
                "quantity": "1",
                "unit_price": "10",
                "tax_rate": "0",
            }
        ],
    }
    inv_id = (await auth_client.post("/api/v1/invoices", json=body)).json()["id"]
    lineage = (await auth_client.get(f"/api/v1/invoices/{inv_id}/extraction")).json()
    assert lineage == []


@pytest.mark.asyncio
async def test_failed_parse_records_failed_run(auth_client, db_session):
    # A .csv with a header but no usable columns → parser raises → 422 + failed run.
    bad = "foo,bar\n1,2\n"
    files = {"file": ("bad.csv", io.BytesIO(bad.encode()), "text/csv")}
    r = await auth_client.post("/api/v1/invoices/upload", files=files)
    assert r.status_code == 422
    run = await db_session.scalar(select(ExtractionRun).where(ExtractionRun.method == "failed"))
    assert run is not None and run.status == "failed" and run.invoice_id is None
    assert run.source_filename == "bad.csv" and run.note


@pytest.mark.asyncio
async def test_cross_tenant_lineage_blocked_at_db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sm() as db:
            a, b = Organization(name="A"), Organization(name="B")
            db.add_all([a, b])
            await db.flush()
            from datetime import date

            inv_a = Invoice(
                org_id=a.id, vendor_id=None, invoice_number="A-1", issue_date=date(2026, 1, 1)
            )
            # vendor_id is NOT NULL; give org A a vendor.
            from app.models.vendor import Vendor

            va = Vendor(org_id=a.id, name="V")
            db.add(va)
            await db.flush()
            inv_a.vendor_id = va.id
            db.add(inv_a)
            await db.flush()
            # org B run pointing at org A's invoice → composite FK violation.
            db.add(ExtractionRun(org_id=b.id, invoice_id=inv_a.id, method="csv", status="saved"))
            with pytest.raises(IntegrityError):
                await db.commit()
    finally:
        await engine.dispose()
