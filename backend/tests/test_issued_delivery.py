"""Invoice delivery + collections: inline/bulk PDF, penalty (late interest),
email sending, reminders (single + bulk overdue), and the outbox history."""

import pytest

ISSUER = {
    "legal_name": "InvoiceIQ Demo BV",
    "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "iban": "NL91ABNA0417164300",
    "bic": "ABNANL2A",
    "email": "billing@invoiceiq.test",
}


async def _activate(auth_client, **issuer_extra):
    await auth_client.put("/api/v1/issuer", json={**ISSUER, **issuer_extra})
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})


def _inv(buyer, price, *, issue, due, email=None, penalty=None):
    body = {
        "buyer_name": buyer,
        "issue_date": issue,
        "due_date": due,
        "lines": [
            {"description": "Service", "quantity": "1", "unit_price": price, "vat_rate": "0"}
        ],
    }
    if email is not None:
        body["buyer_email"] = email
    if penalty is not None:
        body["penalty_rate"] = penalty
    return body


@pytest.mark.asyncio
async def test_penalty_accrues_only_when_marked_and_overdue(auth_client):
    await _activate(auth_client)
    # Overdue (due 2026-01-01), €1000 net / 0% VAT, 12% p.a. penalty.
    inv = (
        await auth_client.post(
            "/api/v1/issued",
            json=_inv("Acme", "1000.00", issue="2025-12-01", due="2026-01-01", penalty="12"),
        )
    ).json()
    assert float(inv["penalty_rate"]) == 12
    got = (await auth_client.get(f"/api/v1/issued/{inv['id']}")).json()
    assert got["status"] == "overdue"
    assert got["days_overdue"] > 0
    # 1000 * 12% * days/365 — positive and consistent with the formula.
    expected = round(1000 * 0.12 * got["days_overdue"] / 365, 2)
    assert abs(float(got["penalty_accrued"]) - expected) < 0.01

    # An invoice WITHOUT a penalty rate never accrues, even when overdue.
    nop = (
        await auth_client.post(
            "/api/v1/issued", json=_inv("Beta", "500.00", issue="2025-12-01", due="2026-01-01")
        )
    ).json()
    assert nop["penalty_rate"] is None
    assert float(nop["penalty_accrued"]) == 0


@pytest.mark.asyncio
async def test_penalty_rate_inherits_issuer_default(auth_client):
    await _activate(auth_client, default_penalty_rate="8")
    inv = (
        await auth_client.post(
            "/api/v1/issued", json=_inv("Acme", "100.00", issue="2026-06-01", due="2026-06-15")
        )
    ).json()
    assert float(inv["penalty_rate"]) == 8


@pytest.mark.asyncio
async def test_pdf_inline_and_period_zip(auth_client):
    pytest.importorskip("reportlab")
    pytest.importorskip("pypdf")
    await _activate(auth_client)
    a = (
        await auth_client.post(
            "/api/v1/issued", json=_inv("Acme", "100.00", issue="2026-03-10", due="2026-04-10")
        )
    ).json()
    await auth_client.post(
        "/api/v1/issued", json=_inv("Beta", "200.00", issue="2026-03-20", due="2026-04-20")
    )
    await auth_client.post(
        "/api/v1/issued", json=_inv("Gamma", "300.00", issue="2026-06-01", due="2026-07-01")
    )

    # Inline disposition for browser viewing.
    inline = await auth_client.get(f"/api/v1/issued/{a['id']}/pdf?inline=1")
    assert inline.status_code == 200
    assert inline.headers["content-disposition"].startswith("inline")

    # ZIP of just the March invoices.
    zip_res = await auth_client.get("/api/v1/issued/export.zip?from=2026-03-01&to=2026-03-31")
    assert zip_res.status_code == 200
    assert zip_res.headers["content-type"] == "application/zip"
    import io
    import zipfile

    zf = zipfile.ZipFile(io.BytesIO(zip_res.content))
    assert len(zf.namelist()) == 2  # only the two March invoices
    assert all(n.endswith(".pdf") for n in zf.namelist())

    # Empty period → 404.
    empty = await auth_client.get("/api/v1/issued/export.zip?from=2020-01-01&to=2020-12-31")
    assert empty.status_code == 404


@pytest.mark.asyncio
async def test_send_invoice_records_outbox(auth_client):
    pytest.importorskip("reportlab")
    pytest.importorskip("pypdf")
    await _activate(auth_client)
    inv = (
        await auth_client.post(
            "/api/v1/issued",
            json=_inv("Acme", "100.00", issue="2026-06-01", due="2026-06-30", email="ap@acme.io"),
        )
    ).json()

    r = await auth_client.post(f"/api/v1/issued/{inv['id']}/send", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["message"]["to_email"] == "ap@acme.io"
    assert body["message"]["kind"] == "invoice"
    assert body["delivered"] is False  # no SMTP configured → recorded only

    # Outbox history shows it.
    log = (await auth_client.get("/api/v1/issued/emails")).json()
    assert len(log) == 1 and log[0]["subject"].startswith("Invoice ")


@pytest.mark.asyncio
async def test_send_without_recipient_is_rejected(auth_client):
    pytest.importorskip("reportlab")
    await _activate(auth_client)
    inv = (
        await auth_client.post(
            "/api/v1/issued", json=_inv("Acme", "100.00", issue="2026-06-01", due="2026-06-30")
        )
    ).json()
    r = await auth_client.post(f"/api/v1/issued/{inv['id']}/send", json={})
    assert r.status_code == 400
    # But an override recipient works.
    ok = await auth_client.post(f"/api/v1/issued/{inv['id']}/send", json={"to_email": "x@acme.io"})
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_reminder_single_and_bulk_overdue(auth_client):
    await _activate(auth_client)
    # Two overdue with email, one overdue WITHOUT email, one paid.
    o1 = (
        await auth_client.post(
            "/api/v1/issued",
            json=_inv(
                "Acme",
                "100.00",
                issue="2025-12-01",
                due="2026-01-01",
                email="a@acme.io",
                penalty="10",
            ),
        )
    ).json()
    (
        await auth_client.post(
            "/api/v1/issued",
            json=_inv("Beta", "200.00", issue="2025-12-01", due="2026-01-01", email="b@beta.io"),
        )
    ).json()
    (
        await auth_client.post(
            "/api/v1/issued", json=_inv("Gamma", "300.00", issue="2025-12-01", due="2026-01-01")
        )
    ).json()  # no email

    # Single reminder increments the dunning counter.
    r = await auth_client.post(f"/api/v1/issued/{o1['id']}/reminder", json={})
    assert r.status_code == 200
    got = (await auth_client.get(f"/api/v1/issued/{o1['id']}")).json()
    assert got["reminder_count"] == 1
    assert got["last_reminder_at"] is not None

    # Bulk run (dunning ladder, Phase 16): the manual reminder above already
    # advanced o1 to the top level, so the ladder does NOT re-send to it (no daily
    # storm). Only Beta (never reminded) gets one; Gamma has no email.
    bulk = (await auth_client.post("/api/v1/issued/reminders/run")).json()
    assert bulk["sent"] == 1  # Beta only
    assert bulk["skipped_no_email"] == 1  # Gamma
    assert bulk["skipped"] == 1  # o1 already at its ladder level

    # Reminder on a PAID invoice is rejected.
    paid = (
        await auth_client.post(
            "/api/v1/issued",
            json=_inv("Delta", "50.00", issue="2026-06-01", due="2026-06-30", email="d@d.io"),
        )
    ).json()
    await auth_client.patch(f"/api/v1/issued/{paid['id']}/payment", json={"amount_paid": "50.00"})
    rej = await auth_client.post(f"/api/v1/issued/{paid['id']}/reminder", json={})
    assert rej.status_code == 400


@pytest.mark.asyncio
async def test_receivables_report_includes_penalty(auth_client):
    await _activate(auth_client)
    await auth_client.post(
        "/api/v1/issued",
        json=_inv("Acme", "1000.00", issue="2025-12-01", due="2026-01-01", penalty="12"),
    )
    rep = (await auth_client.get("/api/v1/issued/reports/receivables")).json()
    assert float(rep["penalty_accrued"]) > 0


@pytest.mark.asyncio
async def test_delivery_endpoints_require_module(auth_client):
    # Module off → send / reminders / export gated.
    for path in ["/api/v1/issued/export.zip", "/api/v1/issued/emails"]:
        r = await auth_client.get(path)
        assert r.status_code == 403
    r = await auth_client.post("/api/v1/issued/reminders/run")
    assert r.status_code == 403
