"""Cash-flow trend (Phase 19): monthly inflow (AR payment ledger) vs outflow
(supplier payments + paid reimbursement payouts), bucketed by month with a
controllable `today`; plus the REPORT_READ route gate."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.expense import ReimbursementBatch
from app.models.organization import Organization
from app.services import cash_flow

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


@pytest.mark.asyncio
async def test_monthly_inflow_and_outflow(auth_client, db_session):
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    # AR inflow: an issued invoice paid directly in March 2026 (500).
    inv = (
        await auth_client.post(
            "/api/v1/issued",
            json={
                "buyer_name": "Globex",
                "issue_date": "2026-02-01",
                "due_date": "2026-03-01",
                "lines": [
                    {"description": "S", "quantity": "1", "unit_price": "500", "vat_rate": "0"}
                ],
            },
        )
    ).json()
    await auth_client.patch(
        f"/api/v1/issued/{inv['id']}/payment",
        json={"amount_paid": "500.00", "paid_date": "2026-03-15"},
    )

    org = await db_session.scalar(select(Organization.id))
    # AP outflow: a paid reimbursement payout in March 2026 (300).
    db_session.add(
        ReimbursementBatch(
            org_id=org,
            status="paid",
            total_eur=Decimal("300.00"),
            paid_at=datetime(2026, 3, 10, tzinfo=UTC),
            version=1,
        )
    )
    await db_session.commit()
    db_session.expire_all()

    series = await cash_flow.monthly(db_session, org, months=12, today=date(2026, 3, 20))
    assert len(series) == 12 and series[-1].period == "2026-03"
    march = next(p for p in series if p.period == "2026-03")
    assert march.inflow == Decimal("500.00")
    assert march.outflow == Decimal("300.00")
    assert march.net == Decimal("200.00")


@pytest.mark.asyncio
async def test_cash_flow_route_and_authz(auth_client, client):
    r = await auth_client.get("/api/v1/analytics/cash-flow?months=6")
    assert r.status_code == 200 and len(r.json()) == 6
    emp = await _member(auth_client, client, "emp@corp.io", role="user")
    assert (await client.get("/api/v1/analytics/cash-flow", headers=_h(emp))).status_code == 403
