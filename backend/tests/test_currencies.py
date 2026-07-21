"""Tenant currency catalog (Slice 5a): unique code (normalised upper), tenant
scoping, archive + optimistic concurrency, resolve, idempotent standard seed, and
the read/manage API (read = any role, manage = admin)."""

import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.services import currencies


async def _org(db):
    return await db.scalar(select(Organization.id))


@pytest.mark.asyncio
async def test_unique_code_normalised(auth_client, db_session):
    org_id = await _org(db_session)
    await currencies.create(db_session, org_id, code="usd", name="US Dollar")
    with pytest.raises(currencies.CurrencyError):
        await currencies.create(db_session, org_id, code="USD", name="dup")
    cur = await currencies.resolve(db_session, org_id, "usd")
    assert cur.code == "USD"  # normalised on write + resolvable case-insensitively


@pytest.mark.asyncio
async def test_tenant_scoped(auth_client, db_session):
    org_a = await _org(db_session)
    org_b = Organization(name="Other Co")
    db_session.add(org_b)
    await db_session.flush()
    await currencies.create(db_session, org_a, code="EUR", name="Euro")
    await currencies.create(db_session, org_b.id, code="GBP", name="Pound")
    a = [c.code for c in await currencies.list_currencies(db_session, org_a)]
    b = [c.code for c in await currencies.list_currencies(db_session, org_b.id)]
    assert a == ["EUR"] and b == ["GBP"]


@pytest.mark.asyncio
async def test_archive_and_optimistic_concurrency(auth_client, db_session):
    org_id = await _org(db_session)
    c = await currencies.create(db_session, org_id, code="PLN", name="Zloty")
    await currencies.set_active(db_session, org_id, c.id, active=False, expected_version=c.version)
    assert "PLN" not in [x.code for x in await currencies.list_currencies(db_session, org_id)]
    assert "PLN" in [
        x.code for x in await currencies.list_currencies(db_session, org_id, include_inactive=True)
    ]
    with pytest.raises(currencies.ConcurrencyError):
        await currencies.set_active(db_session, org_id, c.id, active=True, expected_version=99)


@pytest.mark.asyncio
async def test_seed_idempotent_with_decimal_places(auth_client, db_session):
    org_id = await _org(db_session)
    assert await currencies.seed_standard(db_session, org_id) == 9
    assert await currencies.seed_standard(db_session, org_id) == 0
    jpy = await currencies.resolve(db_session, org_id, "JPY")
    assert jpy.decimal_places == 0 and jpy.symbol == "¥"


@pytest.mark.asyncio
async def test_read_any_role_manage_admin_only(auth_client, client, db_session):
    org_id = await _org(db_session)
    await currencies.seed_standard(db_session, org_id)
    await db_session.commit()

    r = await auth_client.get("/api/v1/currencies")
    assert r.status_code == 200 and "EUR" in {c["code"] for c in r.json()}
    made = await auth_client.post(
        "/api/v1/currencies", json={"code": "CAD", "name": "Canadian Dollar"}
    )
    assert made.status_code == 201, made.text

    inv = await auth_client.post("/api/v1/team/invites", json={"email": "u@x.io", "role": "user"})
    tok = (
        await client.post(
            "/api/v1/auth/accept-invite",
            json={"token": inv.json()["token"], "name": "U", "password": "supersecret"},
        )
    ).json()["token"]["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert (await client.get("/api/v1/currencies", headers=h)).status_code == 200
    forbidden = await client.post(
        "/api/v1/currencies", json={"code": "AUD", "name": "x"}, headers=h
    )
    assert forbidden.status_code == 403
