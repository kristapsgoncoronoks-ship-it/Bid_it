"""Dunning ladder (Phase 16): each escalation level fires at most once as the
invoice ages past its thresholds (no daily re-send storm), the tone escalates, a
no-email invoice is skipped, and the ladder config is admin-editable (SETTINGS_MANAGE)
with a built-in default when unconfigured. Thresholds are crossed by passing an
explicit `today` into run_overdue."""

from datetime import date

import pytest
from sqlalchemy import select

from app.models.issued_invoice import IssuedInvoice
from app.models.organization import Organization
from app.services import dunning

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


async def _activate(auth_client):
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})


async def _issue(auth_client, *, email=None, due="2026-01-01"):
    body = {
        "buyer_name": "Acme",
        "issue_date": "2025-12-01",
        "due_date": due,
        "lines": [{"description": "S", "quantity": "1", "unit_price": "1000", "vat_rate": "0"}],
    }
    if email is not None:
        body["buyer_email"] = email
    return (await auth_client.post("/api/v1/issued", json=body)).json()


async def _org_id(db_session):
    return await db_session.scalar(select(Organization.id))


async def _level(db_session, inv_id):
    db_session.expire_all()
    row = await db_session.scalar(select(IssuedInvoice).where(IssuedInvoice.id == inv_id))
    return row.dunning_level


async def _member(auth_client, client, email, role="user"):
    inv = await auth_client.post("/api/v1/team/invites", json={"email": email, "role": role})
    acc = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "M", "password": "supersecret"},
    )
    return acc.json()["token"]["access_token"]


@pytest.mark.asyncio
async def test_ladder_escalates_each_level_once(auth_client, db_session):
    await _activate(auth_client)
    inv = await _issue(auth_client, email="ar@acme.io")  # due 2026-01-01
    org = await _org_id(db_session)

    # 1 day overdue → below the first default threshold (3 days) → nothing sent.
    r = await dunning.run_overdue(db_session, org, today=date(2026, 1, 2))
    await db_session.commit()
    assert r.sent == 0 and r.skipped == 1
    assert await _level(db_session, inv["id"]) == 0

    # 4 days overdue → level 1 fires.
    r = await dunning.run_overdue(db_session, org, today=date(2026, 1, 5))
    await db_session.commit()
    assert r.sent == 1 and await _level(db_session, inv["id"]) == 1

    # Same day, run again → already at level 1, nothing re-sends (no storm).
    r = await dunning.run_overdue(db_session, org, today=date(2026, 1, 5))
    await db_session.commit()
    assert r.sent == 0 and r.skipped == 1

    # 15 days overdue → level 2 (firm) fires.
    r = await dunning.run_overdue(db_session, org, today=date(2026, 1, 16))
    await db_session.commit()
    assert r.sent == 1 and await _level(db_session, inv["id"]) == 2

    # 35 days overdue → level 3 (final) fires; then it's exhausted.
    r = await dunning.run_overdue(db_session, org, today=date(2026, 2, 5))
    await db_session.commit()
    assert r.sent == 1 and await _level(db_session, inv["id"]) == 3
    r = await dunning.run_overdue(db_session, org, today=date(2026, 3, 5))
    await db_session.commit()
    assert r.sent == 0


@pytest.mark.asyncio
async def test_no_email_is_skipped(auth_client, db_session):
    await _activate(auth_client)
    await _issue(auth_client, email=None)  # no customer email
    org = await _org_id(db_session)
    r = await dunning.run_overdue(db_session, org, today=date(2026, 2, 1))
    assert r.sent == 0 and r.skipped_no_email == 1


@pytest.mark.asyncio
async def test_policy_default_then_custom(auth_client, db_session):
    # Fresh org → the built-in default ladder.
    p = (await auth_client.get("/api/v1/dunning/policy")).json()
    assert p["is_default"] is True and [lv["level"] for lv in p["levels"]] == [1, 2, 3]

    # Configure a custom single-level ladder at 10 days.
    put = await auth_client.put(
        "/api/v1/dunning/policy",
        json={"levels": [{"level": 1, "days_overdue": 10, "tone": "firm", "active": True}]},
    )
    assert put.status_code == 200 and put.json()["is_default"] is False
    got = (await auth_client.get("/api/v1/dunning/policy")).json()
    assert got["levels"] == [{"level": 1, "days_overdue": 10, "tone": "firm", "active": True}]

    # The custom ladder is what run_overdue uses: 5 days overdue < 10 → nothing.
    await _activate(auth_client)
    await _issue(auth_client, email="ar@acme.io")
    org = await _org_id(db_session)
    r = await dunning.run_overdue(db_session, org, today=date(2026, 1, 6))
    await db_session.commit()
    assert r.sent == 0
    r = await dunning.run_overdue(db_session, org, today=date(2026, 1, 15))  # 14 days ≥ 10
    await db_session.commit()
    assert r.sent == 1


@pytest.mark.asyncio
async def test_policy_config_requires_admin(auth_client, client):
    # EMPLOYEE (role 'user') lacks SETTINGS_MANAGE.
    emp = await _member(auth_client, client, "emp@corp.io", role="user")
    assert (await client.get("/api/v1/dunning/policy", headers=_h(emp))).status_code == 403
    assert (
        await client.put(
            "/api/v1/dunning/policy",
            headers=_h(emp),
            json={"levels": [{"level": 1, "days_overdue": 5}]},
        )
    ).status_code == 403
