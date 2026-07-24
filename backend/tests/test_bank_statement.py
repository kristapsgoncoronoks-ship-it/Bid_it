"""Bank-statement reader → the 'available expenses' inbox (SAP Concur style).

The critical behaviour: the transaction AMOUNT is read, not the running BALANCE,
and only debits (outflows) land in the inbox.
"""

import io

import pytest


async def _activate(auth_client):
    r = await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_module_gated(auth_client):
    files = {
        "file": ("s.csv", io.BytesIO(b"Date,Description,Amount\n2026-05-01,x,-1.00\n"), "text/csv")
    }
    r = await auth_client.post("/api/v1/expenses/import/bank-statement", files=files)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_csv_debit_credit_columns_populate_inbox(auth_client):
    await _activate(auth_client)
    csv = (
        "Date,Description,Debit,Credit,Balance\n"
        "2026-05-01,AWS,120.00,,4880.00\n"
        "2026-05-03,Client refund,,300.00,5180.00\n"
        "2026-05-05,Hotel Berlin,89.50,,5090.50\n"
    )
    files = {"file": ("stmt.csv", io.BytesIO(csv.encode()), "text/csv")}
    r = await auth_client.post("/api/v1/expenses/import/bank-statement", files=files)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["method"] == "csv"
    # only the 2 debits land in the inbox; the credit (refund) is excluded
    assert d["imported"] == 2
    inbox = {t["description"]: t for t in d["transactions"]}
    assert set(inbox) == {"AWS", "Hotel Berlin"}
    assert inbox["AWS"]["amount"] == "120.00" and inbox["AWS"]["status"] == "available"

    # persisted → visible in the inbox listing
    listed = (await auth_client.get("/api/v1/expenses/transactions")).json()
    assert {t["description"] for t in listed} == {"AWS", "Hotel Berlin"}


@pytest.mark.asyncio
async def test_csv_signed_amount(auth_client):
    await _activate(auth_client)
    csv = "Date,Description,Amount\n2026-05-01,AWS,-120.00\n2026-05-03,Refund,300.00\n"
    files = {"file": ("stmt.csv", io.BytesIO(csv.encode()), "text/csv")}
    d = (await auth_client.post("/api/v1/expenses/import/bank-statement", files=files)).json()
    assert d["imported"] == 1  # only the debit
    assert d["transactions"][0]["description"] == "AWS"


def _statement_pdf() -> bytes:
    from reportlab.pdfgen import canvas

    ROWS = [
        ("2026-05-01", "Amazon Web Services", "120.00", "4880.00"),
        ("2026-05-03", "Client refund", "300.00", "5180.00"),  # credit (balance up) → excluded
        ("2026-05-05", "Hotel Berlin", "89.50", "5090.50"),
        ("2026-05-07", "Lufthansa flight", "410.00", "4680.50"),
        ("2026-05-09", "Office supplies", "63.20", "4617.30"),
    ]
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(600, 400))
    c.setFont("Courier", 10)
    c.drawString(20, 370, "Date        Description               Amount     Balance")
    y = 350
    for d, desc, amt, bal in ROWS:
        c.drawString(20, y, f"{d}  {desc:<24} {amt:>8} {bal:>10}")
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.mark.asyncio
async def test_pdf_reads_amount_not_balance(auth_client):
    pytest.importorskip("reportlab")
    pytest.importorskip("pdfplumber")
    await _activate(auth_client)
    files = {"file": ("statement.pdf", io.BytesIO(_statement_pdf()), "application/pdf")}
    d = (await auth_client.post("/api/v1/expenses/import/bank-statement", files=files)).json()
    # 4 debits imported (credit refund excluded via balance-delta direction)
    assert d["imported"] == 4
    inbox = {t["description"]: t for t in d["transactions"]}
    assert "Client refund" not in inbox
    # THE amount, not the balance (would be 4880.00):
    assert inbox["Amazon Web Services"]["amount"] == "120.00"
    total = sum(float(t["amount"]) for t in d["transactions"])
    assert abs(total - (120.00 + 89.50 + 410.00 + 63.20)) < 0.01
