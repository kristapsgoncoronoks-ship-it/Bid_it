import io

import pytest


def _invoice_payload(number="INV-1"):
    return {
        "vendor_name": "AWS",
        "invoice_number": number,
        "issue_date": "2026-01-15",
        "currency": "EUR",
        "status": "pending",
        "line_items": [
            {
                "description": "Compute",
                "category": "cloud",
                "quantity": "2",
                "unit_price": "100.00",
                "tax_rate": "21",
            },
            {
                "description": "Storage",
                "category": "cloud",
                "quantity": "1",
                "unit_price": "50.00",
                "tax_rate": "21",
            },
        ],
    }


@pytest.mark.asyncio
async def test_create_and_get_invoice_computes_totals(auth_client):
    r = await auth_client.post("/api/v1/invoices", json=_invoice_payload())
    assert r.status_code == 201, r.text
    inv = r.json()
    # subtotal = 2*100 + 1*50 = 250 ; tax = 21% = 52.50 ; total = 302.50
    assert inv["subtotal"] == "250.00"
    assert inv["tax_amount"] == "52.50"
    assert inv["total"] == "302.50"
    assert inv["vendor_name"] == "AWS"
    assert len(inv["line_items"]) == 2

    got = await auth_client.get(f"/api/v1/invoices/{inv['id']}")
    assert got.status_code == 200
    assert got.json()["invoice_number"] == "INV-1"


@pytest.mark.asyncio
async def test_list_filter_and_patch(auth_client):
    await auth_client.post("/api/v1/invoices", json=_invoice_payload("INV-1"))
    await auth_client.post("/api/v1/invoices", json=_invoice_payload("INV-2"))

    lst = await auth_client.get("/api/v1/invoices?page=1&page_size=10")
    assert lst.status_code == 200
    assert lst.json()["total"] == 2

    search = await auth_client.get("/api/v1/invoices?q=INV-2")
    assert search.json()["total"] == 1

    inv_id = search.json()["items"][0]["id"]
    patched = await auth_client.patch(f"/api/v1/invoices/{inv_id}", json={"status": "paid"})
    assert patched.status_code == 200
    assert patched.json()["status"] == "paid"

    by_status = await auth_client.get("/api/v1/invoices?status=paid")
    assert by_status.json()["total"] == 1


@pytest.mark.asyncio
async def test_duplicate_candidates_scored_needs_all_four_params(auth_client):
    # E1.4: `scored` only computes when vendor_id/total/currency/issue_date are
    # ALL present — a partial set (e.g. only vendor_id + total) must not error
    # or half-compute; it stays an empty, advisory no-op.
    r = await auth_client.post("/api/v1/invoices", json=_invoice_payload())
    inv = r.json()
    dupes = await auth_client.get(
        "/api/v1/invoices/duplicate-candidates",
        params={
            "invoice_number": inv["invoice_number"],
            "vendor_id": inv["vendor_id"],
            "total": inv["total"],
            # currency and issue_date deliberately omitted
        },
    )
    assert dupes.status_code == 200, dupes.text
    assert dupes.json()["scored"] == []


@pytest.mark.asyncio
async def test_delete_invoice(auth_client):
    r = await auth_client.post("/api/v1/invoices", json=_invoice_payload())
    inv_id = r.json()["id"]
    d = await auth_client.delete(f"/api/v1/invoices/{inv_id}")
    assert d.status_code == 204
    assert (await auth_client.get(f"/api/v1/invoices/{inv_id}")).status_code == 404


@pytest.mark.asyncio
async def test_tenant_isolation(client):
    # Org A
    ra = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "A",
            "name": "a",
            "email": "a@acme-a.io",
            "password": "supersecret",
        },
    )
    ta = ra.json()["token"]["access_token"]
    made = await client.post(
        "/api/v1/invoices", json=_invoice_payload(), headers={"Authorization": f"Bearer {ta}"}
    )
    inv_id = made.json()["id"]

    # Org B must not see or fetch A's invoice
    rb = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "B",
            "name": "b",
            "email": "b@acme-b.io",
            "password": "supersecret",
        },
    )
    tb = rb.json()["token"]["access_token"]
    hb = {"Authorization": f"Bearer {tb}"}

    assert (await client.get(f"/api/v1/invoices/{inv_id}", headers=hb)).status_code == 404
    assert (await client.get("/api/v1/invoices", headers=hb)).json()["total"] == 0


@pytest.mark.asyncio
async def test_upload_csv_returns_draft(auth_client, parse_upload):
    csv = "description,category,quantity,unit_price,tax_rate,vendor,invoice_number,issue_date\n"
    csv += "Fuel,fuel,10,1.50,21,Shell,INV-CSV-1,2026-02-01\n"
    csv += "Toll,fuel,2,5.00,21,Shell,INV-CSV-1,2026-02-01\n"
    files = {"file": ("lines.csv", io.BytesIO(csv.encode()), "text/csv")}
    up = await parse_upload(auth_client, files)
    draft = up["draft"]
    assert draft["vendor_name"] == "Shell"
    assert draft["invoice_number"] == "INV-CSV-1"
    assert len(draft["line_items"]) == 2

    # the draft can be saved as-is
    saved = await auth_client.post("/api/v1/invoices", json=draft)
    assert saved.status_code == 201
    assert saved.json()["subtotal"] == "25.00"  # 10*1.5 + 2*5


@pytest.mark.asyncio
async def test_upload_rejects_unknown_type(auth_client):
    files = {"file": ("x.txt", io.BytesIO(b"nope"), "text/plain")}
    r = await auth_client.post("/api/v1/invoices/upload", files=files)
    # The security gate blocks unsupported/unrecognised types (415).
    assert r.status_code == 415
