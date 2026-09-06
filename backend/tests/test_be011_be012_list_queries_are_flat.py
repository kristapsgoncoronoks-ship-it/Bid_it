"""BE-011 / BE-012 (audit 2026-09-05) — two list routes whose SQL grew with the rows.

- `GET /payment-runs` counted the invoices of each run with one query PER RUN
  (and loaded every invoice's vendor to do it), over an unbounded list.
- `GET /invoices/captures/review` read the field rows and probed for a
  duplicate invoice number once PER CAPTURE on the page.

Both now issue the same number of statements at 1× and at 4× the rows — the
property the PERF-002 dashboard test already holds for the home screen.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event, select

from app.models.invoice import Invoice
from app.schemas.invoice import InvoiceCreate, ParsedInvoiceDraft
from app.services import extraction
from tests.test_payment_runs import _make_invoice, _member, _schedule


class _Counter:
    def __init__(self, db_session):
        engine = db_session.bind
        self.engine = getattr(engine, "sync_engine", engine)
        self.statements: list[str] = []

    def _count(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._count)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._count)


async def _runs(auth_client, approver, *, start: int, n: int) -> None:
    for i in range(start, start + n):
        iid = await _make_invoice(auth_client, f"INV-RUN-{i}", "10")
        await _schedule(auth_client, approver, iid)
        r = await auth_client.post(
            "/api/v1/payment-runs", json={"invoice_ids": [iid], "method": "bank_transfer"}
        )
        assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_be011_payment_run_list_statement_count_does_not_grow_with_the_runs(
    auth_client, client, db_session
):
    approver = await _member(auth_client, client, "appr@acme.io", role="admin")
    await _runs(auth_client, approver, start=1, n=2)
    with _Counter(db_session) as c:
        r1 = await auth_client.get("/api/v1/payment-runs")
        assert r1.status_code == 200 and len(r1.json()) == 2
        small = len(c.statements)
    await _runs(auth_client, approver, start=3, n=6)
    with _Counter(db_session) as c:
        r2 = await auth_client.get("/api/v1/payment-runs")
        assert r2.status_code == 200 and len(r2.json()) == 8
        large = len(c.statements)
    assert small == large, f"/payment-runs issued {small} statements for 2 runs and {large} for 8"
    # The counts are still right — one invoice per run.
    assert {row["invoice_count"] for row in r2.json()} == {1}
    # And the list is bounded and pageable.
    page = await auth_client.get("/api/v1/payment-runs", params={"limit": 3, "offset": 2})
    assert page.status_code == 200 and len(page.json()) == 3
    assert (await auth_client.get("/api/v1/payment-runs", params={"limit": 0})).status_code == 422


async def _org_id(db_session) -> str:
    from app.models.organization import Organization

    return await db_session.scalar(select(Organization.id).where(Organization.name == "Acme"))


class _F:
    """The parser's FieldProvenance shape, minus what `record_fields` reads via getattr."""

    def __init__(self, field, value, low):
        self.field, self.value, self.low_confidence, self.confidence = field, value, low, 0.5
        self.status = "extracted"
        self.line_index = None


async def _captures(db_session, org_id: str, *, start: int, n: int, dup_number: str) -> None:
    for i in range(start, start + n):
        run = await extraction.record(
            db_session,
            org_id,
            filename=f"scan-{i}.pdf",
            sha256=f"{i:064x}",
            method="pdf-text",
            status="parsed",
        )
        run.draft_json = ParsedInvoiceDraft(
            draft=InvoiceCreate(
                vendor_name="Fictional Fuels OU",
                invoice_number=dup_number if i % 2 else f"NEW-{i}",
                issue_date="2026-05-01",
                currency="EUR",
                line_items=[
                    {"description": "Diesel", "quantity": "1", "unit_price": "10", "tax_rate": "0"}
                ],
            )
        ).model_dump_json()
        await extraction.record_fields(
            db_session,
            org_id,
            run.id,
            [_F("invoice_number", "x", True), _F("vendor_name", "y", False)],
        )
    await db_session.commit()


@pytest.mark.asyncio
async def test_be012_capture_review_queue_statement_count_does_not_grow_with_the_page(
    auth_client, db_session
):
    org_id = await _org_id(db_session)
    # One live invoice whose number the odd captures duplicate.
    await _make_invoice(auth_client, "DUP-1", "10")
    await _captures(db_session, org_id, start=1, n=2, dup_number="DUP-1")
    with _Counter(db_session) as c:
        r1 = await auth_client.get("/api/v1/invoices/captures/review", params={"page_size": 50})
        assert r1.status_code == 200 and r1.json()["total"] == 2
        small = len(c.statements)
    await _captures(db_session, org_id, start=3, n=6, dup_number="DUP-1")
    with _Counter(db_session) as c:
        r2 = await auth_client.get("/api/v1/invoices/captures/review", params={"page_size": 50})
        assert r2.status_code == 200 and r2.json()["total"] == 8
        large = len(c.statements)
    assert small == large, f"captures/review issued {small} statements for 2 rows and {large} for 8"
    items = r2.json()["items"]
    assert len(items) == 8
    # The per-row answers are unchanged: two fields, one low-confidence, dup flag per number.
    assert {(i["total_fields"], i["low_confidence_fields"]) for i in items} == {(2, 1)}
    assert sum(1 for i in items if i["duplicate_candidate"]) == 4
    assert all(i["duplicate_candidate"] == (i["invoice_number"] == "DUP-1") for i in items)
    assert await db_session.scalar(select(Invoice.id).where(Invoice.invoice_number == "DUP-1"))
