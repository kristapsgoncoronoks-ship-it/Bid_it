"""Email invoice intake: inbound webhook → review inbox → confirm into an invoice."""
import base64

import pytest

CSV = (
    "vendor,invoice_number,issue_date,description,quantity,unit_price,amount,tax_rate\n"
    "Globex Ltd,INV-EMAIL-1,2026-06-01,Widgets,10,5.00,50.00,21\n"
)
JSON = (
    '{"vendor_name": "Initech", "invoice_number": "INV-EMAIL-2", "issue_date": "2026-06-02",'
    ' "currency": "EUR", "line_items": [{"description": "Consulting", "quantity": 1,'
    ' "unit_price": 200, "amount": 200, "tax_rate": 0}]}'
)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _att(filename, text, ct=None):
    return {"filename": filename, "content_type": ct, "content_base64": _b64(text)}


async def _activate(auth_client):
    r = await auth_client.put("/api/v1/modules/email_intake", json={"enabled": True})
    assert r.status_code == 200, r.text


async def _address(auth_client) -> str:
    r = await auth_client.get("/api/v1/email/settings")
    assert r.status_code == 200, r.text
    return r.json()["address"]


def _token(address: str) -> str:
    return address.split("@", 1)[0]


@pytest.mark.asyncio
async def test_settings_module_gated(auth_client):
    r = await auth_client.get("/api/v1/email/settings")
    assert r.status_code == 403  # off by default


@pytest.mark.asyncio
async def test_inbound_creates_pending_and_confirm_creates_invoice(auth_client, client):
    await _activate(auth_client)
    address = await _address(auth_client)

    # Provider posts the parsed email (no auth) — resolve tenant by the `to` address.
    r = await client.post("/api/v1/email/inbound", json={
        "to": f"Accounts <{address}>",
        "from": "supplier@globex.io",
        "subject": "Your invoice",
        "attachments": [_att("invoice.csv", CSV)],
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"received": 1, "pending": 1, "failed": 0}

    inbox = (await auth_client.get("/api/v1/email/inbox")).json()
    assert inbox["total"] == 1
    row = inbox["items"][0]
    assert row["status"] == "pending"
    assert row["from_addr"] == "supplier@globex.io"
    assert row["invoice_id"] is None

    detail = (await auth_client.get(f"/api/v1/email/inbox/{row['id']}")).json()
    assert detail["draft"]["draft"]["invoice_number"] == "INV-EMAIL-1"
    assert detail["has_file"] is True

    conf = await auth_client.post(f"/api/v1/email/inbox/{row['id']}/confirm", json={})
    assert conf.status_code == 201, conf.text
    inv = conf.json()
    assert inv["invoice_number"] == "INV-EMAIL-1"
    assert inv["vendor_name"] == "Globex Ltd"
    assert inv["total"] == "60.50"  # 50 net + 21% tax

    # The inbound row is now linked + confirmed.
    detail2 = (await auth_client.get(f"/api/v1/email/inbox/{row['id']}")).json()
    assert detail2["status"] == "confirmed"
    assert detail2["invoice_id"] == inv["id"]

    # And it shows up as a normal invoice.
    lst = (await auth_client.get("/api/v1/invoices")).json()
    assert any(i["invoice_number"] == "INV-EMAIL-1" for i in lst["items"])

    # Re-confirming is a conflict.
    again = await auth_client.post(f"/api/v1/email/inbox/{row['id']}/confirm", json={})
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_inbound_by_token_and_multiple_attachments(auth_client, client):
    await _activate(auth_client)
    token = _token(await _address(auth_client))

    r = await client.post("/api/v1/email/inbound", json={
        "token": token,
        "attachments": [_att("a.csv", CSV), _att("b.json", JSON), _att("bad.txt", "not an invoice")],
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"received": 3, "pending": 2, "failed": 1}

    failed = (await auth_client.get("/api/v1/email/inbox?status=failed")).json()
    assert failed["total"] == 1
    assert failed["items"][0]["filename"] == "bad.txt"
    assert failed["items"][0]["error"]


@pytest.mark.asyncio
async def test_unknown_token_404(auth_client, client):
    await _activate(auth_client)
    r = await client.post("/api/v1/email/inbound", json={
        "token": "deadbeefdeadbeef",
        "attachments": [_att("a.csv", CSV)],
    })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_inbound_rejected_when_module_off(auth_client, client):
    # Activate to mint an address, then deactivate — inbound must be refused.
    await _activate(auth_client)
    token = _token(await _address(auth_client))
    off = await auth_client.put("/api/v1/modules/email_intake", json={"enabled": False})
    assert off.status_code == 200
    r = await client.post("/api/v1/email/inbound", json={
        "token": token, "attachments": [_att("a.csv", CSV)],
    })
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_rotate_changes_address(auth_client, client):
    await _activate(auth_client)
    old = _token(await _address(auth_client))
    rot = await auth_client.post("/api/v1/email/settings/rotate")
    assert rot.status_code == 200
    new = _token(rot.json()["address"])
    assert new != old
    # Old token no longer routes.
    r = await client.post("/api/v1/email/inbound", json={
        "token": old, "attachments": [_att("a.csv", CSV)],
    })
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_tenant_isolation(auth_client, client):
    """Org A's inbound rows are invisible to Org B, and each token routes to its
    own tenant only."""
    await _activate(auth_client)
    addr_a = await _address(auth_client)
    await client.post("/api/v1/email/inbound", json={
        "to": addr_a, "attachments": [_att("a.csv", CSV)],
    })

    # A second, independent workspace.
    reg = await client.post("/api/v1/auth/register", json={
        "organization_name": "Beta", "name": "Bob", "email": "bob@beta.io", "password": "supersecret",
    })
    tok_b = reg.json()["token"]["access_token"]
    hb = {"Authorization": f"Bearer {tok_b}"}
    await client.put("/api/v1/modules/email_intake", json={"enabled": True}, headers=hb)

    inbox_b = (await client.get("/api/v1/email/inbox", headers=hb)).json()
    assert inbox_b["total"] == 0  # cannot see Org A's inbound invoice

    # Org B's address routes to Org B, not Org A.
    addr_b = (await client.get("/api/v1/email/settings", headers=hb)).json()["address"]
    assert addr_b != addr_a
    await client.post("/api/v1/email/inbound", json={
        "to": addr_b, "attachments": [_att("b.json", JSON)],
    })
    inbox_b2 = (await client.get("/api/v1/email/inbox", headers=hb)).json()
    assert inbox_b2["total"] == 1
    assert inbox_b2["items"][0]["filename"] == "b.json"
