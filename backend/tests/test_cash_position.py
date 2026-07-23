"""Cash-position dashboard (Phase 15): the AR + AP + reconciliation roll-up. An
empty org reads all zeros; issuing a sales invoice grows receivables; scheduling a
supplier invoice grows payables; importing a statement grows the unmatched count;
net = receivables − payables. Plus the REPORT_READ authz gate."""

import io

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


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def _member(auth_client, client, email, role="admin"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "M", "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


async def _cash(auth_client):
    return (await auth_client.get("/api/v1/analytics/cash-position")).json()


@pytest.mark.asyncio
async def test_empty_org_is_zero(auth_client):
    c = await _cash(auth_client)
    assert c["receivables"]["outstanding"] == "0.00"
    assert c["payables"]["outstanding"] == "0.00"
    assert c["reconciliation"]["unmatched"] == 0
    assert c["net_position"] == "0.00"


@pytest.mark.asyncio
async def test_rollup_across_ar_ap_and_recon(auth_client, client):
    approver = await _member(auth_client, client, "appr@acme.io", role="admin")

    # AR: issue a sales invoice (1000 @ 21% = 1210) → outstanding 1210.
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    await auth_client.post(
        "/api/v1/issued",
        json={
            "buyer_name": "Globex",
            "issue_date": "2026-05-10",
            "due_date": "2026-12-01",
            "vat_scheme": "standard",
            "lines": [
                {"description": "S", "quantity": "1", "unit_price": "1000", "vat_rate": "21"}
            ],
        },
    )

    # AP: a supplier invoice driven to scheduled_for_payment (total 100).
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Acme Supplies",
            "invoice_number": "INV-1",
            "issue_date": "2026-05-01",
            "due_date": "2026-12-01",
            "currency": "EUR",
            "line_items": [
                {"description": "W", "quantity": "1", "unit_price": "100", "tax_rate": "0"}
            ],
        },
    )
    iid = r.json()["id"]
    sub = await auth_client.post(f"/api/v1/invoices/{iid}/submit", json={"version": 1})
    appr = await auth_client.post(
        f"/api/v1/invoices/{iid}/approve",
        headers=_h(approver),
        json={"version": sub.json()["version"]},
    )
    await auth_client.post(
        f"/api/v1/invoices/{iid}/transition",
        json={"version": appr.json()["version"], "target": "scheduled_for_payment"},
    )

    # Reconciliation: import a 2-line statement (both unmatched).
    csv = "date,description,credit,debit\n2026-05-05,In,1210.00,\n2026-05-06,Out,,100.00\n"
    files = {"file": ("s.csv", io.BytesIO(csv.encode()), "text/csv")}
    await auth_client.post("/api/v1/reconciliation/import", files=files)

    c = await _cash(auth_client)
    assert c["receivables"]["outstanding"] == "1210.00"
    assert c["payables"]["outstanding"] == "100.00" and c["payables"]["scheduled"] == 1
    assert c["reconciliation"]["unmatched"] == 2
    assert c["net_position"] == "1110.00"  # 1210 − 100
    # Aging buckets are present on the AR side.
    assert any(b["label"] == "Current" for b in c["receivables"]["aging"])


@pytest.mark.asyncio
async def test_report_read_authz(auth_client, client):
    # EMPLOYEE (role 'user') has no REPORT_READ → 403.
    emp = await _member(auth_client, client, "emp@corp.io", role="user")
    assert (await client.get("/api/v1/analytics/cash-position", headers=_h(emp))).status_code == 403
    # READ_ONLY (role 'user_free') has REPORT_READ → 200.
    ro = await _member(auth_client, client, "ro@corp.io", role="user_free")
    assert (await client.get("/api/v1/analytics/cash-position", headers=_h(ro))).status_code == 200
