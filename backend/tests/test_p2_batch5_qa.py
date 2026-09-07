"""P2 batch 5 (audit 2026-09-05) — the QA group: the figures, not just the counts.

- QA-005: extraction was tested for provenance (which cell a value came from),
  never for arithmetic. These tests pin the line maths (quantity × unit price,
  a stated amount winning, a thousands separator), the per-line VAT rounding
  rule the confirm route applies, and what a stated e-invoice total does NOT
  do (override the lines). On the way, BE-021: a present-but-garbled amount
  cell became 0.00 silently — now it falls back to the arithmetic and warns.
- QA-006: the dunning tests asserted how many reminders went out, never what
  they demanded. These assert the outstanding balance after a part payment
  and after a partial credit note, and the accrued-interest and total-due
  lines, figure for figure.
- QA-007: cash application had one happy-path test and two refusals matched
  on a word. These assert the FIGURE each refusal quotes as the balances
  move — allocate, reverse, credit — and the receipt's remaining balance.

Industry-neutral fixtures (site crews, yard suppliers).
"""

from __future__ import annotations

import io
import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.issued_invoice import IssuedInvoice
from app.models.organization import Organization
from app.services import dunning, parser

ISSUER = {
    "legal_name": "Site Crew BV",
    "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "iban": "NL91ABNA0417164300",
    "bic": "ABNANL2A",
    "email": "billing@sitecrew.test",
}


async def _activate(auth_client):
    assert (await auth_client.put("/api/v1/issuer", json=ISSUER)).status_code == 200
    assert (
        await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    ).status_code == 200


async def _issue(auth_client, price, *, vat="21", email=None, due="2026-02-10", penalty=None):
    body = {
        "buyer_name": "Yard Owner",
        "issue_date": "2026-01-10",
        "due_date": due,
        "vat_scheme": "standard",
        "lines": [
            {"description": "Fence line", "quantity": "1", "unit_price": price, "vat_rate": vat}
        ],
    }
    if email:
        body["buyer_email"] = email
    if penalty is not None:
        body["penalty_rate"] = penalty
    r = await auth_client.post("/api/v1/issued", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def _receipt(auth_client, amount):
    r = await auth_client.post(
        "/api/v1/receipts", json={"amount": amount, "received_on": "2026-02-05"}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _allocate(auth_client, rid, inv_id, amount):
    return await auth_client.post(
        f"/api/v1/receipts/{rid}/allocate", json={"invoice_id": inv_id, "amount": amount}
    )


async def _credit(auth_client, inv_id, net, vat="21"):
    r = await auth_client.post(
        f"/api/v1/issued/{inv_id}/credit-note",
        json={
            "lines": [
                {
                    "description": "Posts returned",
                    "quantity": "1",
                    "unit_price": net,
                    "vat_rate": vat,
                }
            ]
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# QA-005 — extraction arithmetic
# --------------------------------------------------------------------------- #


def test_qa005_line_amounts_quantity_times_unit_stated_amount_wins_thousands_separator():
    csv = (
        "description,quantity,unit_price,amount,tax_rate,invoice_number,issue_date,vendor\n"
        "Gravel,3,12.50,,21,INV-Y-1,2026-02-01,Yard Supplies\n"
        'Sand,3,"1,250.00",,21,INV-Y-1,2026-02-01,Yard Supplies\n'
        "Skip hire,1,0,99.99,21,INV-Y-1,2026-02-01,Yard Supplies\n"
    )
    parsed = parser.parse_invoice_file("yard.csv", csv.encode())
    amounts = [(li.description, li.amount) for li in parsed.draft.line_items]
    assert amounts == [
        ("Gravel", Decimal("37.50")),  # 3 × 12.50
        ("Sand", Decimal("3750.00")),  # "1,250.00" is 1250.00, × 3
        ("Skip hire", Decimal("99.99")),  # a stated amount wins over 1 × 0
    ]
    assert parsed.warnings == []


def test_be021_a_garbled_amount_cell_falls_back_to_the_arithmetic_and_warns():
    """Before: `amount = "n/a"` → 0.00, no warning, provenance "extracted" —
    a line of gravel saved at no cost. Now: quantity × unit price, and the
    draft says which line and what the cell held."""
    payload = {
        "invoice_number": "INV-Y-2",
        "vendor_name": "Yard Supplies",
        "line_items": [
            {"description": "Gravel", "quantity": "10", "unit_price": "5.00", "amount": "n/a"},
            {"description": "Sand", "quantity": "2", "unit_price": "40.00", "amount": "80.00"},
        ],
    }
    parsed = parser.parse_invoice_file("yard.json", json.dumps(payload).encode())
    assert [li.amount for li in parsed.draft.line_items] == [Decimal("50.00"), Decimal("80.00")]
    assert parsed.warnings == [
        "Line 1: amount 'n/a' is not a number; used quantity × unit price (50.00)"
    ]
    # The line cell is still reported as read from the file, with the original
    # text beside the figure that replaced it — that is what provenance is for.
    cell = next(f for f in parsed.fields if f.line_index == 0 and f.field == "amount")
    assert cell.status == "extracted" and cell.original_value == "n/a"
    assert cell.normalized_value == "50.00"


@pytest.mark.asyncio
async def test_qa005_saved_totals_use_per_line_vat_rounding(auth_client, parse_upload):
    """Three lines at 21 %: 37.50 → 7.875 → 7.88 (half up), 99.99 → 20.9979 →
    21.00, 1250.00 → 262.50. VAT is rounded PER LINE and summed (291.38), not
    rounded once on the subtotal (1387.49 × 0.21 = 291.3729 → 291.37): the
    cent the two rules differ by is pinned here so nobody moves it by accident."""
    csv = (
        "description,quantity,unit_price,amount,tax_rate,invoice_number,issue_date,vendor\n"
        "Gravel,3,12.50,,21,INV-Y-3,2026-02-01,Yard Supplies\n"
        'Sand,1,"1,250.00",,21,INV-Y-3,2026-02-01,Yard Supplies\n'
        "Skip hire,1,0,99.99,21,INV-Y-3,2026-02-01,Yard Supplies\n"
    )
    up = await parse_upload(
        auth_client, {"file": ("yard.csv", io.BytesIO(csv.encode()), "text/csv")}
    )
    saved = await auth_client.post("/api/v1/invoices", json=up["draft"])
    assert saved.status_code == 201, saved.text
    body = saved.json()
    assert body["subtotal"] == "1387.49"
    assert body["tax_amount"] == "291.38"
    assert body["total"] == "1678.87"
    assert [li["amount"] for li in body["line_items"]] == ["37.50", "1250.00", "99.99"]


@pytest.mark.asyncio
async def test_qa005_a_stated_einvoice_total_is_a_warning_not_the_figure(auth_client, parse_upload):
    """The document says it is payable at 999.00; its lines say 300.00 + 21 %
    = 363.00. The saved invoice is the lines' arithmetic and the draft carries
    the stated figure as a warning to reconcile — the stated total never
    overrides what the lines add up to."""
    from tests.test_einvoice import UBL

    xml = UBL.replace(
        '<cbc:PayableAmount currencyID="EUR">363.00</cbc:PayableAmount>',
        '<cbc:PayableAmount currencyID="EUR">999.00</cbc:PayableAmount>',
    )
    assert xml != UBL
    up = await parse_upload(
        auth_client, {"file": ("supplier.xml", io.BytesIO(xml.encode()), "application/xml")}
    )
    assert any("999.00" in w and "reconcile" in w for w in up["warnings"]), up["warnings"]
    saved = await auth_client.post("/api/v1/invoices", json=up["draft"])
    assert saved.status_code == 201, saved.text
    body = saved.json()
    assert (body["subtotal"], body["tax_amount"], body["total"]) == ("300.00", "63.00", "363.00")


# --------------------------------------------------------------------------- #
# QA-006 — the amount a reminder demands
# --------------------------------------------------------------------------- #


async def _org_id(db_session):
    return await db_session.scalar(select(Organization.id))


@pytest.mark.asyncio
async def test_qa006_reminder_demands_the_outstanding_balance_after_a_part_payment(
    auth_client, db_session
):
    """1000.00 net at 0 % VAT, 250.00 received → the reminder demands 750.00;
    at 12 % p.a. and 30 days overdue the interest line is 750 × 0.12 × 30/365
    = 7.397… → 7.40 and the total now due 757.40."""
    await _activate(auth_client)
    inv = await _issue(
        auth_client, "1000.00", vat="0", email="owner@yard.io", due="2026-01-01", penalty="12"
    )
    rec = await _receipt(auth_client, "250.00")
    assert (await _allocate(auth_client, rec["id"], inv["id"], "250.00")).status_code == 200

    res = await dunning.run_overdue(db_session, await _org_id(db_session), today=date(2026, 1, 31))
    assert res.sent == 1 and len(res.messages) == 1
    body = res.messages[0].body
    assert "Outstanding balance: EUR 750.00" in body, body
    row = await db_session.scalar(select(IssuedInvoice).where(IssuedInvoice.id == inv["id"]))
    assert f"Late-payment interest ({row.penalty_rate}% p.a.): EUR 7.40" in body, body
    assert "Total now due: EUR 757.40" in body, body
    assert "(30 days overdue)" in res.messages[0].subject


@pytest.mark.asyncio
async def test_qa006_reminder_demands_the_balance_net_of_a_partial_credit_note(
    auth_client, db_session
):
    """1000.00 + 21 % = 1210.00 invoiced; 200.00 net credited (242.00 gross)
    → the reminder demands 968.00, and with no penalty rate it carries no
    interest line at all — the figure is the debt, nothing implied."""
    await _activate(auth_client)
    inv = await _issue(auth_client, "1000.00", email="owner@yard.io", due="2026-01-01")
    cn = await _credit(auth_client, inv["id"], "200.00")
    assert cn["total"] == "242.00"

    res = await dunning.run_overdue(db_session, await _org_id(db_session), today=date(2026, 1, 20))
    assert res.sent == 1, res
    body = res.messages[0].body
    assert "Outstanding balance: EUR 968.00" in body, body
    assert "interest" not in body and "Total now due" not in body


# --------------------------------------------------------------------------- #
# QA-007 — the figures a refusal quotes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_qa007_refusals_quote_the_receipts_remaining_balance(auth_client):
    await _activate(auth_client)
    inv = await _issue(auth_client, "1000.00")  # 1210.00 outstanding
    rec = await _receipt(auth_client, "300.00")

    r = await _allocate(auth_client, rec["id"], inv["id"], "400.00")
    assert r.status_code == 400
    assert r.json()["detail"] == "amount exceeds the receipt's unallocated balance (300.00)"

    ok = await _allocate(auth_client, rec["id"], inv["id"], "100.00")
    assert ok.status_code == 200 and ok.json()["unallocated"] == "200.00"

    r = await _allocate(auth_client, rec["id"], inv["id"], "250.00")
    assert r.json()["detail"] == "amount exceeds the receipt's unallocated balance (200.00)"

    # Exactly the remaining balance is accepted; the receipt is then spent.
    ok = await _allocate(auth_client, rec["id"], inv["id"], "200.00")
    assert ok.status_code == 200 and ok.json()["unallocated"] == "0.00"
    r = await _allocate(auth_client, rec["id"], inv["id"], "0.01")
    assert r.json()["detail"] == "amount exceeds the receipt's unallocated balance (0.00)"


@pytest.mark.asyncio
async def test_qa007_refusals_quote_the_invoices_outstanding_balance_as_it_moves(
    auth_client, db_session
):
    await _activate(auth_client)
    inv = await _issue(auth_client, "1000.00")  # 1210.00 outstanding
    rec = await _receipt(auth_client, "5000.00")

    r = await _allocate(auth_client, rec["id"], inv["id"], "1300.00")
    assert r.status_code == 400
    assert r.json()["detail"] == "amount exceeds the invoice's outstanding balance (1210.00)"

    ok = await _allocate(auth_client, rec["id"], inv["id"], "1000.00")
    assert ok.status_code == 200 and ok.json()["unallocated"] == "4000.00"
    r = await _allocate(auth_client, rec["id"], inv["id"], "300.00")
    assert r.json()["detail"] == "amount exceeds the invoice's outstanding balance (210.00)"

    # Reversing the allocation restores the full figure to the refusal.
    payment_id = ok.json()["allocations"][0]["id"]
    rev = await auth_client.post(
        f"/api/v1/receipts/{rec['id']}/deallocate", json={"payment_id": payment_id}
    )
    assert rev.status_code == 200, rev.text
    assert rev.json()["unallocated"] == "5000.00"
    r = await _allocate(auth_client, rec["id"], inv["id"], "1300.00")
    assert r.json()["detail"] == "amount exceeds the invoice's outstanding balance (1210.00)"

    # A partial credit note lowers what can be applied: 1210.00 − 605.00.
    cn = await _credit(auth_client, inv["id"], "500.00")
    assert cn["total"] == "605.00"
    r = await _allocate(auth_client, rec["id"], inv["id"], "700.00")
    assert r.json()["detail"] == "amount exceeds the invoice's outstanding balance (605.00)"
    ok = await _allocate(auth_client, rec["id"], inv["id"], "605.00")
    assert ok.status_code == 200 and ok.json()["unallocated"] == "4395.00"
    row = await db_session.scalar(select(IssuedInvoice).where(IssuedInvoice.id == inv["id"]))
    assert Decimal(row.amount_paid) == Decimal("605.00")

    # And the credit note itself is never a receivable.
    r = await _allocate(auth_client, rec["id"], cn["id"], "1.00")
    assert r.status_code == 400
    assert r.json()["detail"] == "a credit note is not a receivable — no payment applies"
