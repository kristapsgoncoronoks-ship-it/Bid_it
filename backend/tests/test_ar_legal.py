"""WO-K — the AR legal trio.

What must hold:
- **Art. 219**: a credit note carries the corrected invoice's NUMBER as a
  snapshot on the row, structurally on the PDF (a labelled column, not the
  editable note) and as the CII preceding-invoice reference (BT-25) — even
  though the free-text note could be edited away.
- **2011/7/EU**: an overdue EUR invoice with no contractual rate gets the
  ADVISORY statutory figure — (base + 8 pp) pro-rata by day, plus the €40
  Art. 6 recovery cost; a contractual `penalty_rate` replaces the whole
  statutory regime (no €40 on top); a non-EUR invoice states WHY there is
  no figure instead of guessing; nothing is ever booked.
- **MT940**: the SWIFT statement parses into the same normalized lines the
  other formats produce (D/C directions, reversal flip, statement currency,
  :86: description), the import route accepts it, and the unsupported-type
  message finally names every format that IS supported.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services import bank_statement, late_interest, pdf_ocr

pytestmark = pytest.mark.asyncio

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

TODAY = date.today()


async def _setup(auth_client):
    assert (await auth_client.put("/api/v1/issuer", json=ISSUER)).status_code == 200
    assert (
        await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    ).status_code == 200


async def _issued(auth_client, *, due_days_ago: int | None = None, **extra) -> dict:
    body = {
        "buyer_name": "Globex SARL",
        "buyer_email": "ap@globex.example",
        "issue_date": (TODAY - timedelta(days=60)).isoformat(),
        "lines": [
            {"description": "Consulting", "quantity": "1", "unit_price": "500.00", "vat_rate": "0"}
        ],
        **extra,
    }
    if due_days_ago is not None:
        body["due_date"] = (TODAY - timedelta(days=due_days_ago)).isoformat()
    r = await auth_client.post("/api/v1/issued", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


# --------------------------------------------------------------------------- #
# Art. 219 — the credit-note reference
# --------------------------------------------------------------------------- #


async def test_credit_note_carries_the_corrected_invoice_number(auth_client):
    await _setup(auth_client)
    inv = await _issued(auth_client)

    cn = await auth_client.post(
        f"/api/v1/issued/{inv['id']}/credit-note", json={"full": True, "note": "Goodwill credit"}
    )
    assert cn.status_code in (200, 201), cn.text
    cn_id = cn.json()["id"]

    detail = (await auth_client.get(f"/api/v1/issued/{cn_id}")).json()
    assert detail["doc_type"] == "credit_note"
    # The snapshot is on the row and the wire — not only inside editable text.
    assert detail["corrected_invoice_number"] == inv["number"]

    # The PDF prints it as a labelled column (extract the text layer and look).
    pdf = await auth_client.get(f"/api/v1/issued/{cn_id}/pdf")
    assert pdf.status_code == 200
    text, _method = pdf_ocr.extract_text(pdf.content)
    assert "CORRECTS" in text, "the credit-note PDF lost its Art. 219 reference label"
    assert inv["number"] in text

    # The CII XML carries BT-25 (InvoiceReferencedDocument) with the number.
    xml = await auth_client.get(f"/api/v1/issued/{cn_id}/xml")
    assert xml.status_code == 200
    body = xml.content.decode()
    assert "InvoiceReferencedDocument" in body
    assert inv["number"] in body
    # And it is typed as a credit note (UNTDID 381), as before.
    assert ">381<" in body


# --------------------------------------------------------------------------- #
# 2011/7/EU — the advisory statutory figure
# --------------------------------------------------------------------------- #


async def test_statutory_interest_math_and_the_40_euro(auth_client, db_session):
    await _setup(auth_client)
    inv = await _issued(auth_client, due_days_ago=73)  # 73/365 = a fifth of a year

    detail = (await auth_client.get(f"/api/v1/issued/{inv['id']}")).json()
    li = detail["late_interest"]
    assert li is not None and li["basis"] == "statutory"
    assert li["directive"] == "2011/7/EU"
    assert li["days_overdue"] == 73
    assert li["base_rate_configured"] is False
    # (default 2.15 + 8) % × 500.00 × 73/365 = 10.15
    assert li["rate_pp"] == "10.15"
    assert li["outstanding"] == "500.00"
    assert li["interest_eur"] == "10.15"
    assert li["recovery_cost_eur"] == "40.00"
    assert li["total_eur"] == "50.15"

    # Configure the reference rate: the figure follows the setting.
    put = await auth_client.put("/api/v1/settings/late-interest", json={"base_rate_pp": "4.00"})
    assert put.status_code == 200, put.text
    assert put.json()["base_rate_pp"] == "4.00"
    li = (await auth_client.get(f"/api/v1/issued/{inv['id']}")).json()["late_interest"]
    assert li["rate_pp"] == "12.00"
    assert li["base_rate_configured"] is True
    # 12% × 500 × 73/365 = 12.00
    assert li["total_eur"] == "52.00"


async def test_contractual_rate_replaces_the_statutory_regime(auth_client):
    await _setup(auth_client)
    inv = await _issued(auth_client, due_days_ago=73, penalty_rate="10")
    li = (await auth_client.get(f"/api/v1/issued/{inv['id']}")).json()["late_interest"]
    assert li["basis"] == "contractual"
    assert Decimal(li["rate_pp"]) == Decimal("10")
    # 10% × 500 × 73/365 = 10.00 — and NO €40 on top of negotiated terms.
    assert li["recovery_cost_eur"] is None
    assert li["total_eur"] == "10.00"


async def test_no_figure_when_not_overdue_and_a_reason_when_not_eur(auth_client):
    await _setup(auth_client)
    fresh = await _issued(auth_client, due_days_ago=-30)  # due a month from now
    assert (await auth_client.get(f"/api/v1/issued/{fresh['id']}")).json()["late_interest"] is None

    usd = await _issued(auth_client, due_days_ago=10, currency="USD")
    li = (await auth_client.get(f"/api/v1/issued/{usd['id']}")).json()["late_interest"]
    assert li["basis"] == "unavailable"
    assert "USD" in li["reason"]


async def test_compute_is_none_for_credit_notes_and_settled_invoices():
    class _Inv:
        doc_type = "invoice"
        due_date = TODAY - timedelta(days=10)
        total = Decimal("100")
        amount_paid = Decimal("100")
        credited_total = Decimal("0")
        currency = "EUR"
        penalty_rate = None

    class _Org:
        late_interest_base_rate = None

    settled = _Inv()
    assert late_interest.compute(settled, _Org()) is None  # type: ignore[arg-type]
    cn = _Inv()
    cn.doc_type = "credit_note"
    cn.amount_paid = Decimal("0")
    assert late_interest.compute(cn, _Org()) is None  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# MT940
# --------------------------------------------------------------------------- #

MT940 = (
    ":20:STMT-2026-001\r\n"
    ":25:LV12BANK0000000000001\r\n"
    ":28C:00001/001\r\n"
    ":60F:C260801EUR12345,67\r\n"
    ":61:2608040804D450,00NTRFNONREF//B1\r\n"
    ":86:SEPA payment Overlap Supplies GmbH\r\n"
    "invoice INV-OVERLAP-1\r\n"
    ":61:2608110811C1210,50NTRFNONREF//B2\r\n"
    ":86:Incoming Riverbank Office\r\n"
    ":61:2608150815RD100,00NTRFNONREF//B3\r\n"
    ":86:Returned payment\r\n"
    ":62F:C260831EUR13006,17\r\n"
)


async def test_mt940_parses_directions_reversal_and_currency():
    result = bank_statement.parse("statement.sta", MT940.encode())
    assert result.method == "mt940"
    t1, t2, t3 = result.transactions
    assert (t1.date, t1.direction, t1.amount) == (date(2026, 8, 4), "debit", Decimal("450.00"))
    assert "Overlap Supplies" in t1.description and "INV-OVERLAP-1" in t1.description
    assert t1.currency == "EUR"  # from :60F:, stated once for the statement
    assert (t2.direction, t2.amount) == ("credit", Decimal("1210.50"))
    # RD = reversed debit: the money came BACK, so the direction flips.
    assert t3.direction == "credit"
    assert t3.description.startswith("[reversal]")


async def test_mt940_is_detected_by_content_without_the_extension():
    result = bank_statement.parse("statement.txt", MT940.encode())
    assert result.method == "mt940"


async def test_unsupported_message_names_every_supported_format():
    with pytest.raises(ValueError) as exc:
        bank_statement.parse("statement.docx", b"not a statement")
    msg = str(exc.value)
    for fmt in (".csv", ".xml", ".pdf", "MT940"):
        assert fmt in msg, f"the unsupported-format message no longer names {fmt}"


async def test_mt940_uploads_through_the_reconciliation_route(auth_client):
    await _setup(auth_client)
    up = await auth_client.post(
        "/api/v1/reconciliation/import",
        files={"file": ("august.940", io.BytesIO(MT940.encode()), "text/plain")},
    )
    assert up.status_code == 201, up.text
    out = up.json()
    assert out["method"] == "mt940" and out["imported"] == 3
