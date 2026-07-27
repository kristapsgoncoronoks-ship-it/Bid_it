"""Tenant tax-code catalog (Slice 4a): unique code, tenant scoping, archive +
optimistic concurrency, resolve, idempotent standard seed, and the read/manage
API (read = any role, manage = admin)."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.services import tax_codes


async def _org(db):
    return await db.scalar(select(Organization.id))


# --- service ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_unique_code_and_bad_category(auth_client, db_session):
    org_id = await _org(db_session)
    await tax_codes.create(db_session, org_id, code="LV-STD", name="LV", rate="21")
    with pytest.raises(tax_codes.TaxCodeError):
        await tax_codes.create(db_session, org_id, code="lv-std", name="dup", rate="21")
    with pytest.raises(tax_codes.TaxCodeError):
        await tax_codes.create(db_session, org_id, code="X", name="x", rate="0", category="bogus")


@pytest.mark.asyncio
async def test_tenant_scoped_listing(auth_client, db_session):
    org_a = await _org(db_session)
    org_b = Organization(name="Other Co")
    db_session.add(org_b)
    await db_session.flush()
    await tax_codes.create(db_session, org_a, code="A-STD", name="A", rate="21")
    await tax_codes.create(db_session, org_b.id, code="B-STD", name="B", rate="19")
    a = [t.code for t in await tax_codes.list_codes(db_session, org_a)]
    b = [t.code for t in await tax_codes.list_codes(db_session, org_b.id)]
    assert a == ["A-STD"] and b == ["B-STD"]


@pytest.mark.asyncio
async def test_archive_hides_from_default_list_but_resolves(auth_client, db_session):
    org_id = await _org(db_session)
    tc = await tax_codes.create(
        db_session, org_id, code="RED", name="Reduced", rate="12", category="reduced", country="lv"
    )
    assert tc.country == "LV"
    await tax_codes.set_active(db_session, org_id, tc.id, active=False, expected_version=tc.version)

    assert "RED" not in [t.code for t in await tax_codes.list_codes(db_session, org_id)]
    assert "RED" in [
        t.code for t in await tax_codes.list_codes(db_session, org_id, include_inactive=True)
    ]
    # An archived code still resolves (historical references keep working).
    assert (await tax_codes.resolve(db_session, org_id, "red")).rate == Decimal("12.000")


@pytest.mark.asyncio
async def test_optimistic_concurrency(auth_client, db_session):
    org_id = await _org(db_session)
    tc = await tax_codes.create(
        db_session, org_id, code="Z", name="Zero", rate="0", category="zero"
    )
    with pytest.raises(tax_codes.ConcurrencyError):
        await tax_codes.set_active(db_session, org_id, tc.id, active=False, expected_version=99)


@pytest.mark.asyncio
async def test_seed_standard_is_idempotent(auth_client, db_session):
    org_id = await _org(db_session)
    n1 = await tax_codes.seed_standard(db_session, org_id)
    assert n1 == 10
    n2 = await tax_codes.seed_standard(db_session, org_id)
    assert n2 == 0
    total = len(await tax_codes.list_codes(db_session, org_id))
    assert total == 10


# --- API -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_any_role_manage_admin_only(auth_client, client, db_session):
    org_id = await _org(db_session)
    await tax_codes.seed_standard(db_session, org_id)
    await db_session.commit()

    # Admin (auth_client owner) can list and filter by country.
    r = await auth_client.get("/api/v1/tax-codes?country=LV")
    assert r.status_code == 200
    assert {t["code"] for t in r.json()} == {"LV-STD", "LV-RED", "LV-RED5"}

    # Admin can create.
    made = await auth_client.post(
        "/api/v1/tax-codes",
        json={"code": "FR-STD", "name": "France", "rate": "20", "country": "FR"},
    )
    assert made.status_code == 201, made.text

    # A read-only member can list but not create.
    inv = await auth_client.post("/api/v1/team/invites", json={"email": "u@x.io", "role": "user"})
    tok = (
        await client.post(
            "/api/v1/auth/accept-invite",
            json={"token": inv.json()["token"], "name": "U", "password": "supersecret"},
        )
    ).json()["token"]["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert (await client.get("/api/v1/tax-codes", headers=h)).status_code == 200
    forbidden = await client.post(
        "/api/v1/tax-codes", json={"code": "X", "name": "x", "rate": "1"}, headers=h
    )
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_tax_code_mutations_are_audited(auth_client, db_session):
    """§4.16 (WO-14): create and set_active each append an audit event in the
    same commit as the mutation, with old→new on the flip."""
    import json

    from app.models.audit import AuditEvent

    made = await auth_client.post(
        "/api/v1/tax-codes", json={"code": "AUD-T", "name": "Audited", "rate": "9"}
    )
    assert made.status_code == 201, made.text
    tc = made.json()
    r = await auth_client.patch(
        f"/api/v1/tax-codes/{tc['id']}", json={"active": False, "version": tc["version"]}
    )
    assert r.status_code == 200

    events = list(
        await db_session.scalars(
            select(AuditEvent).where(AuditEvent.target_id == tc["id"]).order_by(AuditEvent.seq)
        )
    )
    assert [e.action for e in events] == ["tax_code.create", "tax_code.set_active"]
    assert json.loads(events[0].meta)["code"] == "AUD-T"
    flip = json.loads(events[1].meta)
    assert flip["old"] is True and flip["new"] is False
