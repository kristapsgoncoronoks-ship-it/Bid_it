"""WO-93 — the client claim-status portal over HTTP (G4.4, R39).

Everything a ROUTE owns: Decimal-as-string on the wire, the structural
permission declaration, the module entitlement, org scoping over deliberately
OVERLAPPING data, and the two refusals. The `test_wo81_recovery.py` fixture
strategy, unchanged.

The service's `today` seam has no HTTP form (it is deliberately off the wire),
so the stage scenarios that need a controlled clock live in
`test_wo93_client_status.py`. What is asserted here is the SHAPE a client
actually receives.

THERE IS NO BY-ID FETCH ON THIS SURFACE, so master-context §4.4's opaque-404
case has no form here: the route takes no object id, and the isolation proof is
the zero-rows probe over identical-looking data (`test_wo93_one_orgs_portal_
shows_none_of_another_orgs_claims`). `vat_refund_claims`' own opaque-404 path is
already proven by `test_wo76_claim_routes.py`, and the table is already a probed
(non-EXEMPT) row in `tests/test_tenancy_parity.py`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.services import modules
from tests.transport.conftest import make_entity
from tests.transport.test_wo93_client_status import _make_claim

V = "/api/v1"
PATH = f"{V}/transport/claim-status"


async def _register_org(client: AsyncClient):
    suffix = uuid.uuid4().hex[:8]
    r = await client.post(
        f"{V}/auth/register",
        json={
            "organization_name": f"WO93 Org {suffix}",
            "name": "Owner",
            "email": f"owner-{suffix}@wo93.example.io",
            "password": "supersecret",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['token']['access_token']}"}, body["organization"]["id"]


async def _member_with_role(client: AsyncClient, owner_headers, db_session, stored_role: str):
    from sqlalchemy import update

    from app.models.user import User, UserRole

    email = f"{stored_role}-{uuid.uuid4().hex[:8]}@wo93.example.io"
    inv = await client.post(
        f"{V}/team/invites", headers=owner_headers, json={"email": email, "role": "admin"}
    )
    assert inv.status_code == 201, inv.text
    acc = await client.post(
        f"{V}/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "Member", "password": "supersecret"},
    )
    assert acc.status_code == 201, acc.text
    body = acc.json()
    await db_session.execute(
        update(User)
        .where(User.id == body["user"]["id"])
        .values(role=UserRole(stored_role), is_expense_approver=False)
    )
    await db_session.commit()
    return {"Authorization": f"Bearer {body['token']['access_token']}"}


async def _http_org_with_a_filed_claim(
    client,
    db_session,
    *,
    vat_eur: Decimal | None,
    entity_name: str = "Northbound Haulage OU",
    refund_country: str = "LV",
    ref_period: str = "2026-Q2",
):
    headers, org_id = await _register_org(client)
    await modules.set_enabled(db_session, org_id, "transport", True)
    entity = await make_entity(db_session, org_id)
    entity.legal_name = entity_name
    claim = await _make_claim(
        db_session,
        org_id,
        entity.id,
        refund_country=refund_country,
        ref_period=ref_period,
    )
    claim.status = "paid"
    claim.status_code = "3A"
    claim.vat_eur = vat_eur
    await db_session.commit()
    return headers, org_id


@pytest.mark.asyncio
async def test_wo93_route_returns_the_portal(client, db_session):
    headers, _org_id = await _http_org_with_a_filed_claim(
        client, db_session, vat_eur=Decimal("1500.00")
    )
    r = await client.get(PATH, headers=headers, params={"year": 2026})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["year"] == 2026
    assert body["currency"] == "EUR"
    assert body["total_claims"] == 1
    assert body["not_shown_claims"] == 0
    assert [s["stage"] for s in body["stages"]] == [
        "prep",
        "ready",
        "filed",
        "awaiting",
        "refunded",
        "needs_attention",
    ]
    (row,) = body["claims"]
    assert row["stage"] == "refunded"
    assert row["entity_name"] == "Northbound Haulage OU"
    assert row["refund_country"] == "LV"
    assert row["period"] == "2026-Q2"
    # Every stage carries the server's own plain-language wording.
    refunded = next(s for s in body["stages"] if s["stage"] == "refunded")
    assert refunded["label"] == "Refunded"
    assert refunded["description"].strip()


@pytest.mark.asyncio
async def test_wo93_amounts_cross_the_wire_as_exact_decimal_strings(client, db_session):
    """§4.9 — pydantic v2 serializes `Decimal` as a JSON string, so a float path
    anywhere here would arrive as a bare JSON NUMBER: the type assertion is the
    proof, and it is made on every amount in the response. The value is the
    widest the `Numeric(14, 2)` column can hold, so the full column width is
    also proven to survive the service's quantization unchanged."""
    headers, _org_id = await _http_org_with_a_filed_claim(
        client, db_session, vat_eur=Decimal("999999999999.99")
    )
    r = await client.get(PATH, headers=headers, params={"year": 2026})
    assert r.status_code == 200, r.text
    body = r.json()
    (row,) = body["claims"]
    assert isinstance(row["vat_eur"], str)
    assert row["vat_eur"] == "999999999999.99"
    for stage in body["stages"]:
        assert isinstance(stage["vat_eur"], str), stage
    refunded = next(s for s in body["stages"] if s["stage"] == "refunded")
    assert refunded["vat_eur"] == "999999999999.99"
    assert next(s for s in body["stages"] if s["stage"] == "prep")["vat_eur"] == "0.00"


@pytest.mark.asyncio
async def test_wo93_an_unstateable_figure_crosses_the_wire_as_null(client, db_session):
    """`null`, never `0.00`: a client reading EUR 0.00 for a claim we simply
    cannot total yet learns something false."""
    headers, _org_id = await _http_org_with_a_filed_claim(client, db_session, vat_eur=None)
    r = await client.get(PATH, headers=headers, params={"year": 2026})
    assert r.status_code == 200, r.text
    (row,) = r.json()["claims"]
    assert row["vat_eur"] is None


@pytest.mark.asyncio
async def test_wo93_an_empty_year_is_200_with_the_six_empty_stages(client, db_session):
    headers, _org_id = await _http_org_with_a_filed_claim(
        client, db_session, vat_eur=Decimal("10.00")
    )
    r = await client.get(PATH, headers=headers, params={"year": 2019})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_claims"] == 0
    assert body["claims"] == []
    assert len(body["stages"]) == 6
    assert all(s["claims"] == 0 and s["vat_eur"] == "0.00" for s in body["stages"])


@pytest.mark.asyncio
async def test_wo93_an_invalid_year_is_422_invalid_year(client, db_session):
    headers, _org_id = await _http_org_with_a_filed_claim(
        client, db_session, vat_eur=Decimal("10.00")
    )
    r = await client.get(PATH, headers=headers, params={"year": 99999})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "invalid_year"


@pytest.mark.asyncio
async def test_wo93_module_disabled_refuses_403_module_not_enabled(client, db_session):
    """ADR-P3 rule 3 — the entitlement is enforced INSIDE the service, so an
    un-entitled org is refused even holding VAT_READ."""
    headers, _org_id = await _register_org(client)
    r = await client.get(PATH, headers=headers, params={"year": 2026})
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "module_not_enabled"


@pytest.mark.asyncio
async def test_wo93_employee_is_denied_and_the_read_only_client_is_granted(client, db_session):
    """Deny-by-default on the wire (§4.6/§4.7), and the point of this surface:
    the READ_ONLY role — `BA_fleet_fuel.md` §1.2's *"`user` | The client.
    Read-only base"*, stored `user_free` — reaches it, and EMPLOYEE (which holds
    no VAT permission at all) is refused by the ROUTER dependency before any
    service work."""
    headers, org_id = await _http_org_with_a_filed_claim(
        client, db_session, vat_eur=Decimal("42.00")
    )

    employee = await _member_with_role(client, headers, db_session, "user")
    r = await client.get(PATH, headers=employee, params={"year": 2026})
    assert r.status_code == 403, r.text

    read_only = await _member_with_role(client, headers, db_session, "user_free")
    r = await client.get(PATH, headers=read_only, params={"year": 2026})
    assert r.status_code == 200, r.text
    assert r.json()["claims"][0]["vat_eur"] == "42.00"


@pytest.mark.asyncio
async def test_wo93_the_route_is_gated_on_vat_read(client, db_session):
    """The recorded permission decision (WO-93): this returns CLAIM ROWS, not
    the portfolio aggregates WO-79 reserved `TRANSPORT_READ` for, so it declares
    the claim permission. Pinned structurally so a future edit is deliberate."""
    from app.api.routes.transport import claim_status as claim_status_route
    from app.core import authz

    declared = set()
    for dep in claim_status_route.router.dependencies:
        declared.update(getattr(dep.dependency, authz.PERMISSIONS_ATTR, ()))
    assert declared == {authz.Permission.VAT_READ}, declared


@pytest.mark.asyncio
async def test_wo93_one_orgs_portal_shows_none_of_another_orgs_claims(client, db_session):
    """Tenant isolation over IDENTICAL-LOOKING data (§4.1/§4.4 — distinct data
    can pass a broken filter by accident): same entity name, same refunding
    country, same period, same amount, in two organizations. Each portal shows
    exactly one claim and it is its own."""
    a_headers, _a_org = await _http_org_with_a_filed_claim(
        client, db_session, vat_eur=Decimal("777.00"), entity_name="Shared Name Transport OU"
    )
    b_headers, _b_org = await _http_org_with_a_filed_claim(
        client, db_session, vat_eur=Decimal("777.00"), entity_name="Shared Name Transport OU"
    )

    for headers in (a_headers, b_headers):
        r = await client.get(PATH, headers=headers, params={"year": 2026})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_claims"] == 1, "the other tenant's identical claim must not appear"
        assert body["claims"][0]["vat_eur"] == "777.00"
        refunded = next(s for s in body["stages"] if s["stage"] == "refunded")
        assert refunded["claims"] == 1
