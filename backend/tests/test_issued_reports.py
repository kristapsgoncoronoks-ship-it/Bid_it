"""Issuing reports: summary/turnover, receivables (paid/unpaid/overdue) + aging,
partners/turnover, output-VAT summary, plus payment recording and CSV export."""

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


async def _activate(auth_client):
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})


def _inv(buyer, number_price, *, issue, due, scheme="standard", rate="21"):
    return {
        "buyer_name": buyer,
        "buyer_vat_number": f"VAT-{buyer[:3].upper()}",
        "issue_date": issue,
        "due_date": due,
        "vat_scheme": scheme,
        "lines": [
            {
                "description": "Service",
                "quantity": "1",
                "unit_price": number_price,
                "vat_rate": rate,
            }
        ],
    }


async def _seed(auth_client):
    # Acme: two invoices (one paid, one overdue). Globex: one open (future due).
    a1 = (
        await auth_client.post(
            "/api/v1/issued", json=_inv("Acme", "1000.00", issue="2026-01-10", due="2026-02-10")
        )
    ).json()
    a2 = (
        await auth_client.post(
            "/api/v1/issued", json=_inv("Acme", "500.00", issue="2026-02-01", due="2026-03-03")
        )
    ).json()
    g1 = (
        await auth_client.post(
            "/api/v1/issued", json=_inv("Globex", "200.00", issue="2026-03-05", due="2099-01-01")
        )
    ).json()
    return a1, a2, g1


@pytest.mark.asyncio
async def test_payment_recording_and_derived_status(auth_client):
    await _activate(auth_client)
    a1, a2, g1 = await _seed(auth_client)

    # a1 total = 1210.00 (1000 + 21%). Pay it fully.
    assert a1["total"] == "1210.00"
    paid = await auth_client.patch(
        f"/api/v1/issued/{a1['id']}/payment",
        json={"amount_paid": "1210.00", "paid_date": "2026-02-05"},
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "paid"
    assert paid.json()["outstanding"] == "0.00"

    # Overpayment is refused.
    bad = await auth_client.patch(
        f"/api/v1/issued/{a2['id']}/payment", json={"amount_paid": "999999"}
    )
    assert bad.status_code == 400

    # a2 is past its due date and unpaid → 'overdue'.
    a2got = (await auth_client.get(f"/api/v1/issued/{a2['id']}")).json()
    assert a2got["status"] == "overdue"

    # Partial payment on the NOT-yet-due invoice (g1, total 242.00) → 'partial'.
    # (An overdue invoice that is partly paid stays 'overdue' — the actionable
    # state takes precedence — so partial is exercised on a current invoice.)
    part = await auth_client.patch(
        f"/api/v1/issued/{g1['id']}/payment", json={"amount_paid": "100.00"}
    )
    assert part.json()["status"] == "partial"
    assert part.json()["outstanding"] == "142.00"  # 242.00 - 100.00


@pytest.mark.asyncio
async def test_summary_report_totals_and_series(auth_client):
    await _activate(auth_client)
    await _seed(auth_client)
    rep = (await auth_client.get("/api/v1/issued/reports/summary")).json()
    assert rep["count"] == 3
    assert rep["currency"] == "EUR"
    # net = 1000 + 500 + 200 = 1700; gross = 1210 + 605 + 242 = 2057
    assert rep["net"] == "1700.00"
    assert rep["gross"] == "2057.00"
    periods = {p["period"]: p for p in rep["series"]}
    assert periods["2026-01"]["gross"] == "1210.00"
    assert periods["2026-02"]["count"] == 1


@pytest.mark.asyncio
async def test_receivables_paid_unpaid_overdue_and_aging(auth_client):
    await _activate(auth_client)
    a1, a2, g1 = await _seed(auth_client)
    await auth_client.patch(
        f"/api/v1/issued/{a1['id']}/payment",
        json={"amount_paid": "1210.00", "paid_date": "2026-02-05"},
    )

    rep = (await auth_client.get("/api/v1/issued/reports/receivables")).json()
    buckets = {b["status"]: b for b in rep["statuses"]}
    assert buckets["paid"]["count"] == 1
    assert buckets["overdue"]["count"] == 1  # a2: due 2026-03-03, unpaid, past today(2026-07-20)
    assert buckets["open"]["count"] == 1  # g1: due 2099
    # Outstanding excludes the paid invoice.
    assert rep["total_outstanding"] == "847.00"  # 605 (a2) + 242 (g1)
    assert rep["overdue_outstanding"] == "605.00"
    # a2 is 90+ days past due as of the fixed test clock.
    aging = {b["label"]: b for b in rep["aging"]}
    assert aging["90+"]["outstanding"] == "605.00"
    # DSO proxy over the one settled invoice: 2026-01-10 → 2026-02-05 = 26 days.
    assert rep["avg_days_to_pay"] == 26.0


@pytest.mark.asyncio
async def test_partners_turnover_sorted_desc(auth_client):
    await _activate(auth_client)
    await _seed(auth_client)
    rep = (await auth_client.get("/api/v1/issued/reports/partners")).json()
    partners = rep["partners"]
    assert [p["partner"] for p in partners] == ["Acme", "Globex"]  # 1815 gross vs 242
    acme = partners[0]
    assert acme["count"] == 2
    assert acme["net"] == "1500.00"
    assert acme["gross"] == "1815.00"
    assert acme["last_invoice"] == "2026-02-01"


@pytest.mark.asyncio
async def test_vat_summary_by_rate_and_scheme(auth_client):
    await _activate(auth_client)
    # One standard 21% invoice and one reverse-charge (0 output VAT) invoice.
    await auth_client.post(
        "/api/v1/issued", json=_inv("Acme", "1000.00", issue="2026-01-10", due="2026-02-10")
    )
    await auth_client.post(
        "/api/v1/issued",
        json=_inv("Zeta", "400.00", issue="2026-01-11", due="2026-02-11", scheme="reverse_charge"),
    )
    rep = (await auth_client.get("/api/v1/issued/reports/vat")).json()
    by_scheme = {r["scheme"]: r for r in rep["by_scheme"]}
    assert by_scheme["standard"]["vat"] == "210.00"
    assert by_scheme["reverse_charge"]["vat"] == "0.00"
    assert rep["total_vat"] == "210.00"  # only the standard scheme accrues output VAT


@pytest.mark.asyncio
async def test_reports_csv_export(auth_client):
    await _activate(auth_client)
    await _seed(auth_client)
    csv = await auth_client.get("/api/v1/issued/reports/partners?format=csv")
    assert csv.status_code == 200
    assert csv.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv.headers["content-disposition"]
    body = csv.text
    assert "partner,vat_number,invoices" in body
    assert "Acme" in body


@pytest.mark.asyncio
async def test_reports_partners_csv_export_is_formula_injection_safe(auth_client):
    """WO-36/R9: `issued.py::_csv_safe` used to be a 4th independent copy of the
    formula-injection mitigation `app/core/csv_safety.py::sanitize_cell` centralizes
    elsewhere (deliberately left untouched by WO-29/R1). It was already correct
    before this WO's refactor to delegate to the shared helper — this is a
    refactor-safety proof, not a red-then-green vulnerability fix — and it matters
    because `partner` is sourced straight from the buyer-supplied, unconstrained
    `buyer_name` field (`schemas/issued.py::IssuedInvoiceCreate.buyer_name`), the
    same CSV-injection shape R1 fixed on the sibling exporters."""
    await _activate(auth_client)
    payload = "=cmd|'/c calc'!A1"
    await auth_client.post(
        "/api/v1/issued", json=_inv(payload, "100.00", issue="2026-01-10", due="2026-02-10")
    )
    await auth_client.post(
        "/api/v1/issued", json=_inv("Acme", "50.00", issue="2026-01-11", due="2026-02-11")
    )
    csv = await auth_client.get("/api/v1/issued/reports/partners?format=csv")
    assert csv.status_code == 200
    body = csv.text
    # The malicious buyer name is quote-prefixed so it can never be evaluated as a
    # formula on open in Excel/Sheets/LibreOffice.
    assert "'" + payload in body
    assert payload not in body.replace("'" + payload, "")
    # A normal buyer name in the same export is left byte-for-byte untouched.
    assert "\r\nAcme,VAT-ACM," in body
    assert "'Acme" not in body


@pytest.mark.asyncio
async def test_reports_require_module(auth_client):
    # Issuing module off → reports are gated.
    r = await auth_client.get("/api/v1/issued/reports/summary")
    assert r.status_code == 403
