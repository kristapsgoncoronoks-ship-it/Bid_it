"""Slice 4b — a tax-code on an issued line drives its VAT rate from the tenant
catalogue and is snapshotted onto the line; an unknown/inactive code is rejected;
lines with no code behave exactly as before."""

import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.services import tax_codes

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


async def _activate(auth_client, db_session):
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    org_id = await db_session.scalar(select(Organization.id))
    await tax_codes.seed_standard(db_session, org_id)
    await db_session.commit()


def _body(line):
    return {
        "buyer_name": "Acme",
        "issue_date": "2026-01-10",
        "due_date": "2026-02-10",
        "vat_scheme": "standard",
        "lines": [line],
    }


@pytest.mark.asyncio
async def test_tax_code_drives_rate_and_is_snapshotted(auth_client, db_session):
    await _activate(auth_client, db_session)
    # LV-STD is 21% in the seeded catalogue; the line omits vat_rate entirely.
    body = _body(
        {"description": "Service", "quantity": "1", "unit_price": "1000.00", "tax_code": "lv-std"}
    )
    r = await auth_client.post("/api/v1/issued", json=body)
    assert r.status_code == 201, r.text
    inv = r.json()
    line = inv["lines"][0]
    assert line["vat_rate"] == "21.00"  # resolved from the catalogue
    assert line["tax_code"] == "LV-STD"  # snapshotted, canonical casing
    assert inv["tax_total"] == "210.00" and inv["total"] == "1210.00"


@pytest.mark.asyncio
async def test_unknown_or_inactive_code_is_rejected(auth_client, db_session):
    await _activate(auth_client, db_session)
    r = await auth_client.post(
        "/api/v1/issued",
        json=_body({"description": "x", "quantity": "1", "unit_price": "10", "tax_code": "NOPE"}),
    )
    assert r.status_code == 400

    # Archive LV-STD, then it can no longer be used on a new invoice.
    org_id = await db_session.scalar(select(Organization.id))
    tc = await tax_codes.resolve(db_session, org_id, "LV-STD")
    await tax_codes.set_active(db_session, org_id, tc.id, active=False, expected_version=tc.version)
    await db_session.commit()
    r2 = await auth_client.post(
        "/api/v1/issued",
        json=_body({"description": "x", "quantity": "1", "unit_price": "10", "tax_code": "LV-STD"}),
    )
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_line_without_code_unchanged(auth_client, db_session):
    await _activate(auth_client, db_session)
    body = _body(
        {"description": "Service", "quantity": "1", "unit_price": "100.00", "vat_rate": "9"}
    )
    r = await auth_client.post("/api/v1/issued", json=body)
    assert r.status_code == 201, r.text
    line = r.json()["lines"][0]
    assert line["vat_rate"] == "9.00" and line["tax_code"] is None
