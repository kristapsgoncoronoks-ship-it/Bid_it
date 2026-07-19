"""Bank-statement transaction reader → expense import draft.

The critical behaviour: the transaction AMOUNT is read, not the running BALANCE.
"""
import io

import pytest


async def _activate(auth_client):
    r = await auth_client.put("/api/v1/modules/expenses", json={"enabled": True})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_module_gated(auth_client):
    files = {"file": ("s.csv", io.BytesIO(b"Date,Description,Amount\n2026-05-01,x,-1.00\n"), "text/csv")}
    r = await auth_client.post("/api/v1/expenses/import/bank-statement", files=files)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_csv_debit_credit_columns(auth_client):
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
    txns = {t["description"]: t for t in d["transactions"]}
    assert txns["AWS"]["direction"] == "debit" and txns["AWS"]["amount"] == "120.00"
    assert txns["Client refund"]["direction"] == "credit"
    # Only debits become suggested expense items (credits excluded).
    sugg = {i["description"]: i for i in d["suggested_items"]}
    assert set(sugg) == {"AWS", "Hotel Berlin"}
    assert sugg["Hotel Berlin"]["amount"] == "89.50"


@pytest.mark.asyncio
async def test_csv_signed_amount(auth_client):
    await _activate(auth_client)
    csv = "Date,Description,Amount\n2026-05-01,AWS,-120.00\n2026-05-03,Refund,300.00\n"
    files = {"file": ("stmt.csv", io.BytesIO(csv.encode()), "text/csv")}
    d = (await auth_client.post("/api/v1/expenses/import/bank-statement", files=files)).json()
    dirs = {t["description"]: t["direction"] for t in d["transactions"]}
    assert dirs == {"AWS": "debit", "Refund": "credit"}
    assert [i["description"] for i in d["suggested_items"]] == ["AWS"]


def _statement_pdf() -> bytes:
    from reportlab.pdfgen import canvas
    ROWS = [
        ("2026-05-01", "Amazon Web Services", "120.00", "4880.00"),  # debit (default first)
        ("2026-05-03", "Client refund", "300.00", "5180.00"),        # credit (balance up)
        ("2026-05-05", "Hotel Berlin", "89.50", "5090.50"),          # debit (balance down)
        ("2026-05-07", "Lufthansa flight", "410.00", "4680.50"),     # debit
        ("2026-05-09", "Office supplies", "63.20", "4617.30"),       # debit
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
    r = await auth_client.post("/api/v1/expenses/import/bank-statement", files=files)
    assert r.status_code == 200, r.text
    d = r.json()
    txns = {t["description"]: t for t in d["transactions"]}
    assert len(d["transactions"]) == 5
    # THE amount, not the balance:
    assert txns["Amazon Web Services"]["amount"] == "120.00"
    assert txns["Amazon Web Services"]["balance"] == "4880.00"
    # direction inferred from balance movement:
    assert txns["Client refund"]["direction"] == "credit"
    assert txns["Hotel Berlin"]["direction"] == "debit"
    # 4 debits suggested (refund excluded)
    assert len(d["suggested_items"]) == 4
    total = sum(float(i["amount"]) for i in d["suggested_items"])
    assert abs(total - (120.00 + 89.50 + 410.00 + 63.20)) < 0.01
