"""Cross-tenant isolation WARRANTY (identity/authz).

Proves the core security property end-to-end over HTTP: a user authenticated to
Organization B can never READ, UPDATE, EXPORT, or DOWNLOAD Organization A's data —
across invoices, issued invoices, and expense reports (incl. receipts).

Isolation is enforced in the backend at three layers (per-route org filter, the
`do_orm_execute` ORM guard, and Postgres RLS), so a by-id request for another
org's object returns an OPAQUE 404 — never a 403 that would confirm the id exists
(object-id-guessing defence). B is fully activated (same modules as A) so every
assertion isolates the TENANT check, not a module gate.

Companion narrative: docs/security/cross-tenant-isolation-report.md
"""

import pytest

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x00IEND\xaeB`\x82"
)

_ISSUER = {
    "legal_name": "Demo BV",
    "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "iban": "NL91ABNA0417164300",
    "bic": "ABNANL2A",
    "email": "billing@demo.test",
}


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register(client, org: str, email: str) -> str:
    r = await client.post(
        "/api/v1/auth/register",
        json={"organization_name": org, "name": "Owner", "email": email, "password": "supersecret"},
    )
    assert r.status_code == 201, r.text
    return r.json()["token"]["access_token"]


async def _activate(client, tok: str) -> None:
    """Turn on issuing (needs a complete issuer profile) + expenses."""
    await client.put("/api/v1/issuer", json=_ISSUER, headers=_h(tok))
    await client.put("/api/v1/modules/issuing", json={"enabled": True}, headers=_h(tok))
    await client.put("/api/v1/modules/expenses", json={"enabled": True}, headers=_h(tok))


async def _seed_org_a(client, tok: str) -> dict:
    """Create one resource of each protected kind for Org A; return their ids."""
    invoice = (
        await client.post(
            "/api/v1/invoices",
            json={
                "vendor_name": "SecretVendor",
                "invoice_number": "A-SECRET-1",
                "issue_date": "2026-06-01",
                "currency": "EUR",
                "status": "pending",
                "line_items": [
                    {
                        "description": "x",
                        "category": "c",
                        "quantity": "1",
                        "unit_price": "100",
                        "tax_rate": "0",
                    }
                ],
            },
            headers=_h(tok),
        )
    ).json()

    issued = (
        await client.post(
            "/api/v1/issued",
            json={
                "buyer_name": "A Buyer",
                "issue_date": "2026-06-01",
                "due_date": "2026-07-01",
                "lines": [
                    {
                        "description": "Service",
                        "quantity": "1",
                        "unit_price": "500",
                        "vat_rate": "0",
                    }
                ],
            },
            headers=_h(tok),
        )
    ).json()

    report = (
        await client.post(
            "/api/v1/expenses",
            json={
                "title": "A confidential trip",
                "currency": "EUR",
                "items": [
                    {
                        "spend_date": "2026-05-01",
                        "category": "meals",
                        "description": "Dinner",
                        "amount": "50.00",
                        "vat_amount": "0",
                    }
                ],
            },
            headers=_h(tok),
        )
    ).json()
    item_id = report["items"][0]["id"]
    # Attach a receipt so the download path has bytes to (not) leak.
    up = await client.post(
        f"/api/v1/expenses/{report['id']}/items/{item_id}/receipt",
        files={"file": ("r.png", _PNG, "image/png")},
        headers=_h(tok),
    )
    assert up.status_code == 200, up.text

    return {
        "invoice_id": invoice["id"],
        "issued_id": issued["id"],
        "report_id": report["id"],
        "item_id": item_id,
    }


@pytest.fixture
async def two_orgs(client):
    """Org A seeded with data across every protected kind; Org B fully activated
    (same modules) with a bearer token, ready to attempt cross-tenant access."""
    a_tok = await _register(client, "Alpha Ltd", "a-owner@alpha.io")
    await _activate(client, a_tok)
    ids = await _seed_org_a(client, a_tok)

    b_tok = await _register(client, "Beta Ltd", "b-owner@beta.io")
    await _activate(client, b_tok)
    return {"client": client, "a": a_tok, "b": b_tok, "ids": ids}


# --------------------------------------------------------------------------- #
# READ
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_org_b_cannot_read_org_a(two_orgs):
    c, b, ids = two_orgs["client"], two_orgs["b"], two_orgs["ids"]
    for path in (
        f"/api/v1/invoices/{ids['invoice_id']}",
        f"/api/v1/issued/{ids['issued_id']}",
        f"/api/v1/expenses/{ids['report_id']}",
    ):
        r = await c.get(path, headers=_h(b))
        assert r.status_code == 404, f"READ leak at {path}: {r.status_code}"


# --------------------------------------------------------------------------- #
# UPDATE
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_org_b_cannot_update_org_a(two_orgs):
    c, b, ids = two_orgs["client"], two_orgs["b"], two_orgs["ids"]
    attempts = [
        ("patch", f"/api/v1/invoices/{ids['invoice_id']}", {"notes": "pwned"}),
        ("post", f"/api/v1/invoices/{ids['invoice_id']}/validate", {"action": "approve"}),
        ("patch", f"/api/v1/expenses/{ids['report_id']}", {"title": "pwned"}),
        (
            "patch",
            f"/api/v1/expenses/{ids['report_id']}/items/{ids['item_id']}",
            {"comment": "pwned"},
        ),
    ]
    for verb, path, body in attempts:
        r = await getattr(c, verb)(path, json=body, headers=_h(b))
        assert r.status_code == 404, f"UPDATE leak at {verb} {path}: {r.status_code}"

    # A's invoice is untouched.
    got = await c.get(f"/api/v1/invoices/{ids['invoice_id']}", headers=_h(two_orgs["a"]))
    assert got.status_code == 200 and got.json().get("notes") in (None, "")


# --------------------------------------------------------------------------- #
# EXPORT
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_org_b_cannot_export_org_a(two_orgs):
    c, a, b, ids = two_orgs["client"], two_orgs["a"], two_orgs["b"], two_orgs["ids"]

    # By-id e-invoice XML export of A's issued invoice → opaque 404 for B.
    r = await c.get(f"/api/v1/issued/{ids['issued_id']}/xml", headers=_h(b))
    assert r.status_code == 404

    # The accounting-ledger export is ORG-SCOPED: A's export carries A's vendor;
    # B's export sees none of it (404 = no rows in B's own ledger).
    a_exp = await c.get("/api/v1/export/accounting?from=2026-01-01&to=2026-12-31", headers=_h(a))
    assert a_exp.status_code == 200
    assert "SecretVendor" in a_exp.text
    b_exp = await c.get("/api/v1/export/accounting?from=2026-01-01&to=2026-12-31", headers=_h(b))
    assert b_exp.status_code == 404  # B has no invoices — and certainly not A's
    assert "SecretVendor" not in b_exp.text


# --------------------------------------------------------------------------- #
# DOWNLOAD (files)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_org_b_cannot_download_org_a_files(two_orgs):
    c, b, ids = two_orgs["client"], two_orgs["b"], two_orgs["ids"]
    for path in (
        f"/api/v1/issued/{ids['issued_id']}/pdf",
        f"/api/v1/expenses/{ids['report_id']}/pdf",
        f"/api/v1/expenses/{ids['report_id']}/items/{ids['item_id']}/receipt",
    ):
        r = await c.get(path, headers=_h(b))
        assert r.status_code == 404, f"DOWNLOAD leak at {path}: {r.status_code}"


# --------------------------------------------------------------------------- #
# ID-guessing opacity: a cross-tenant id is indistinguishable from a nonexistent
# one (both 404) — the response never confirms the object exists.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_cross_tenant_and_nonexistent_are_both_opaque_404(two_orgs):
    c, b, ids = two_orgs["client"], two_orgs["b"], two_orgs["ids"]
    real = await c.get(f"/api/v1/invoices/{ids['invoice_id']}", headers=_h(b))
    fake = await c.get("/api/v1/invoices/00000000-0000-0000-0000-000000000000", headers=_h(b))
    assert real.status_code == fake.status_code == 404
