"""WO-79 — transport routes slice 3: the fuel-transaction READ surface.

Proves `api/routes/transport/fuel.py` end to end: the (entity, period) listing
with its supplier/country filters, the claim-reference-period expansion through
the shared `claim_lines.period_months` mapping, pagination that loses no row,
Decimal-as-string on the wire (§4.9, INCLUDING `qty`'s three decimals and the FX
provenance quadruple), the structural permission pair (EMPLOYEE denied /
ACCOUNTANT granted), the module entitlement (ADR-P3 rule 3) and the §4.4 opaque
cross-tenant 404 — plus an org-scoped list over deliberately OVERLAPPING data.

Fixture strategy is `test_wo76_claim_routes.py`'s, unchanged: orgs/tokens ride
the real HTTP register flow, transport enablement is direct service setup (the
module is in no billing plan, so the HTTP toggle would 402), transactions seed
through `fuel_ingest.ingest_transaction` — the only writer there is — and every
assertion about what the API returned goes through the API.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.services import modules
from app.services.transport import fuel_ingest
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import make_entity

V = "/api/v1"
PATH = f"{V}/transport/fuel-transactions"


# --------------------------------------------------------------------------- #
# HTTP org / member helpers (the WO-76 suite's shapes)
# --------------------------------------------------------------------------- #


async def _register_org(client: AsyncClient):
    suffix = uuid.uuid4().hex[:8]
    r = await client.post(
        f"{V}/auth/register",
        json={
            "organization_name": f"WO79 Org {suffix}",
            "name": "Owner",
            "email": f"owner-{suffix}@wo79.example.io",
            "password": "supersecret",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['token']['access_token']}"}, body["organization"]["id"]


async def _enable_transport(db_session, org_id: str) -> None:
    await modules.set_enabled(db_session, org_id, "transport", True)


async def _member_with_role(client: AsyncClient, owner_headers, db_session, stored_role: str):
    """Invite a member into the OWNER's org, then downgrade the stored role
    (the `role_client` conftest pattern — registration/invites only mint
    admin-tier accounts). Same org, so only the permission set differs."""
    from app.models.user import User, UserRole

    email = f"{stored_role}-{uuid.uuid4().hex[:8]}@wo79.example.io"
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
    from sqlalchemy import update

    await db_session.execute(
        update(User)
        .where(User.id == body["user"]["id"])
        .values(role=UserRole(stored_role), is_expense_approver=False)
    )
    await db_session.commit()
    return {"Authorization": f"Bearer {body['token']['access_token']}"}


# --------------------------------------------------------------------------- #
# Domain seeding
# --------------------------------------------------------------------------- #


async def _make_txn(
    db_session,
    org_id: str,
    entity_id: str,
    *,
    supplier: str = "Q8",
    period: str = "2026-05",
    line_seq: int = 1,
    country: str = "LV",
    txn_date: date = date(2026, 5, 15),
    product: str = "DIESEL",
    qty: Decimal = Decimal("100.125"),
    currency: str = "EUR",
    net_eur: Decimal = Decimal("2000.00"),
    vat_eur: Decimal = Decimal("420.00"),
    invoice_ref: str | None = "INV-0001",
    **extra,
):
    return await fuel_ingest.ingest_transaction(
        db_session,
        org_id,
        entity_id=entity_id,
        supplier=supplier,
        period=period,
        line_seq=line_seq,
        country=country,
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=txn_date,
        station="Demo Station Riga",
        product=product,
        qty=qty,
        currency=currency,
        net_local=net_eur,
        vat_local=vat_eur,
        gross_local=net_eur + vat_eur,
        net_eur=net_eur,
        vat_eur=vat_eur,
        invoice_ref=invoice_ref,
        **extra,
    )


async def _org_with_entity(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id)
    entity_id = entity.id
    await db_session.commit()
    return headers, org_id, entity_id


# --------------------------------------------------------------------------- #
# The listing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo79_lists_the_periods_transactions_for_an_entity(client, db_session):
    headers, org_id, entity_id = await _org_with_entity(client, db_session)
    await _make_txn(db_session, org_id, entity_id, line_seq=1)
    await _make_txn(db_session, org_id, entity_id, line_seq=2, supplier="BP")
    # A DIFFERENT period — must not appear under `period=2026-05`.
    await _make_txn(
        db_session, org_id, entity_id, period="2026-06", line_seq=1, txn_date=date(2026, 6, 2)
    )
    await db_session.commit()

    r = await client.get(
        PATH, headers=headers, params={"entity_id": entity_id, "period": "2026-05"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert body["page"] == 1 and body["page_size"] == 100
    # Deterministic order: (period, supplier, txn_date, line_seq) — BP before Q8.
    assert [i["supplier"] for i in body["items"]] == ["BP", "Q8"]
    assert {i["period"] for i in body["items"]} == {"2026-05"}
    first = body["items"][0]
    assert first["product_group"] == "Diesel"  # derived at ingestion, never supplied
    assert first["country"] == "LV"
    assert first["invoice_ref"] == "INV-0001"
    # `invoice_id` is NULL by design — no code populates it yet (WO-50's model
    # docstring). Asserted so a future service that starts resolving it has to
    # come past this test deliberately.
    assert first["invoice_id"] is None


@pytest.mark.asyncio
async def test_wo79_money_and_qty_cross_the_wire_as_exact_decimal_strings(client, db_session):
    """§4.9 — pydantic v2 serializes `Decimal` as a JSON string. A float path
    anywhere here would show up as a bare number (or as 100.125 losing a digit
    on a value an IEEE-754 double cannot hold exactly)."""
    headers, org_id, entity_id = await _org_with_entity(client, db_session)
    await _make_txn(
        db_session,
        org_id,
        entity_id,
        qty=Decimal("100.125"),
        net_eur=Decimal("2000.00"),
        vat_eur=Decimal("420.00"),
        currency="PLN",
        fx_rate=Decimal("4.325100"),
        fx_ecb_rate=Decimal("4.325100"),
        fx_ecb_date=date(2026, 5, 15),
        fx_source="ecb",
    )
    await db_session.commit()

    r = await client.get(
        PATH, headers=headers, params={"entity_id": entity_id, "period": "2026-05"}
    )
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    for key in (
        "qty",
        "net_local",
        "vat_local",
        "gross_local",
        "net_eur",
        "vat_eur",
        "net_eur_eff",
        "fx_rate",
        "fx_ecb_rate",
    ):
        assert isinstance(item[key], str), f"{key} is not a string on the wire: {item[key]!r}"
    assert item["qty"] == "100.125"
    assert item["net_eur"] == "2000.00"
    assert item["vat_eur"] == "420.00"
    assert item["gross_local"] == "2420.00"
    # `net_eur_eff` defaults to `net_eur` when no off-invoice rebate applies —
    # set explicitly by the ingestion service, never a silent DB identity.
    assert item["net_eur_eff"] == "2000.00"
    # §4.15 — the FX provenance quadruple survives the read surface intact.
    assert item["fx_rate"] == "4.325100"
    assert item["fx_ecb_date"] == "2026-05-15"
    assert item["fx_source"] == "ecb"
    assert item["currency"] == "PLN"


@pytest.mark.asyncio
async def test_wo79_supplier_and_country_filters_narrow_the_set(client, db_session):
    headers, org_id, entity_id = await _org_with_entity(client, db_session)
    await _make_txn(db_session, org_id, entity_id, supplier="Q8", country="LV", line_seq=1)
    await _make_txn(db_session, org_id, entity_id, supplier="Q8", country="PL", line_seq=2)
    await _make_txn(db_session, org_id, entity_id, supplier="BP", country="LV", line_seq=3)
    await db_session.commit()

    base = {"entity_id": entity_id, "period": "2026-05"}
    r = await client.get(PATH, headers=headers, params={**base, "supplier": "Q8"})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2
    assert {i["supplier"] for i in r.json()["items"]} == {"Q8"}

    r = await client.get(PATH, headers=headers, params={**base, "country": "LV"})
    assert r.json()["total"] == 2
    assert {i["country"] for i in r.json()["items"]} == {"LV"}

    # Lower-case in, upper-case column: the service upper-cases to match what
    # `fuel_ingest` stored, so the filter is not silently empty.
    r = await client.get(PATH, headers=headers, params={**base, "country": "lv"})
    assert r.json()["total"] == 2

    r = await client.get(PATH, headers=headers, params={**base, "supplier": "Q8", "country": "PL"})
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["line_seq"] == 2


@pytest.mark.asyncio
async def test_wo79_a_claim_reference_period_expands_to_its_months(client, db_session):
    """The pick-list's real query: a claim holds `"2026-Q2"`, the model column
    holds `"YYYY-MM"`. The expansion is the SHARED `claim_lines.period_months`
    mapping — not a second copy of it."""
    headers, org_id, entity_id = await _org_with_entity(client, db_session)
    for month, day in (("2026-04", 3), ("2026-05", 15), ("2026-06", 20)):
        await _make_txn(
            db_session,
            org_id,
            entity_id,
            period=month,
            line_seq=1,
            txn_date=date(int(month[:4]), int(month[5:]), day),
        )
    # Just outside the quarter — Q2 must not pick it up.
    await _make_txn(
        db_session, org_id, entity_id, period="2026-07", line_seq=1, txn_date=date(2026, 7, 1)
    )
    await db_session.commit()

    r = await client.get(
        PATH, headers=headers, params={"entity_id": entity_id, "period": "2026-Q2"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 3
    assert [i["period"] for i in r.json()["items"]] == ["2026-04", "2026-05", "2026-06"]

    r = await client.get(
        PATH, headers=headers, params={"entity_id": entity_id, "period": "2026-YEAR"}
    )
    assert r.json()["total"] == 4


@pytest.mark.asyncio
async def test_wo79_an_unparseable_period_is_422_invalid_period(client, db_session):
    """Fails CLOSED. `"2026-13"` is the trap: `period_months` on its own would
    read it as Q3 (`int(tail[1])`), so the accessor validates the reference-
    period shape through `claim.validate_ref_period` FIRST."""
    headers, _org_id, entity_id = await _org_with_entity(client, db_session)
    for bad in ("2026-13", "2026-Q5", "not-a-period", "2026"):
        r = await client.get(PATH, headers=headers, params={"entity_id": entity_id, "period": bad})
        assert r.status_code == 422, f"{bad}: {r.status_code} {r.text}"
        assert r.json()["code"] == "invalid_period", f"{bad}: {r.text}"


@pytest.mark.asyncio
async def test_wo79_pagination_pages_the_set_without_losing_a_row(client, db_session):
    headers, org_id, entity_id = await _org_with_entity(client, db_session)
    for seq in range(1, 6):
        await _make_txn(db_session, org_id, entity_id, line_seq=seq)
    await db_session.commit()

    seen: list[int] = []
    for page in (1, 2, 3):
        r = await client.get(
            PATH,
            headers=headers,
            params={"entity_id": entity_id, "period": "2026-05", "page": page, "page_size": 2},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # `total` describes the whole filtered set, never the page.
        assert body["total"] == 5
        assert body["page"] == page and body["page_size"] == 2
        seen.extend(i["line_seq"] for i in body["items"])
    assert seen == [1, 2, 3, 4, 5]  # every row exactly once, in a stable order

    # The bound is enforced at the boundary, not silently clamped.
    r = await client.get(
        PATH,
        headers=headers,
        params={"entity_id": entity_id, "period": "2026-05", "page_size": 501},
    )
    assert r.status_code == 422, r.text


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo79_module_disabled_refuses_403_module_not_enabled(client, db_session):
    """ADR-P3 rule 3 — the entitlement is enforced INSIDE the service, so an
    un-entitled org is refused even holding VAT_READ."""
    headers, _org_id = await _register_org(client)
    r = await client.get(
        PATH, headers=headers, params={"entity_id": str(uuid.uuid4()), "period": "2026-05"}
    )
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "module_not_enabled"


@pytest.mark.asyncio
async def test_wo79_employee_is_denied_and_accountant_is_granted(client, db_session, role_client):
    """Deny-by-default live on the wire (§4.6/§4.7). EMPLOYEE holds neither
    VAT_READ nor TRANSPORT_READ and is refused by the ROUTER dependency, before
    any service work; ACCOUNTANT holds VAT_READ and reads the surface."""
    headers, org_id, entity_id = await _org_with_entity(client, db_session)
    await _make_txn(db_session, org_id, entity_id)
    await db_session.commit()

    employee = await role_client("user")
    r = await employee.get(PATH, params={"entity_id": entity_id, "period": "2026-05"})
    assert r.status_code == 403, r.text

    acct = await _member_with_role(client, headers, db_session, "accountant")
    r = await client.get(PATH, headers=acct, params={"entity_id": entity_id, "period": "2026-05"})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1


@pytest.mark.asyncio
async def test_wo79_vat_read_and_transport_read_have_identical_role_coverage():
    """The recorded judgment call (WO-79 Implementation guidance §2): the route
    is gated VAT_READ rather than TRANSPORT_READ, and that choice changes NO
    effective access. Pinned so a future matrix edit that splits them has to
    revisit this route's gate deliberately."""
    from app.core.authz import ROLE_PERMISSIONS, Permission

    for role, perms in ROLE_PERMISSIONS.items():
        assert (Permission.VAT_READ in perms) == (Permission.TRANSPORT_READ in perms), role


@pytest.mark.asyncio
async def test_wo79_a_cross_tenant_entity_id_is_an_opaque_404(client, db_session):
    """§4.4 — org B (transport-enabled, full permissions) probing org A's entity
    id gets 404 `entity_not_found`, never 403: object-id guessing yields zero
    information, and the refusal lands BEFORE any transaction row is read."""
    headers_a, org_a, entity_a = await _org_with_entity(client, db_session)
    await _make_txn(db_session, org_a, entity_a)
    await db_session.commit()

    headers_b, _org_b = await _register_org(client)
    await _enable_transport(db_session, _org_b)
    await db_session.commit()

    r = await client.get(
        PATH, headers=headers_b, params={"entity_id": entity_a, "period": "2026-05"}
    )
    assert r.status_code != 403, "403 confirms the entity exists (§4.4)"
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "entity_not_found"

    # A's own view is untouched by B's probing.
    r = await client.get(
        PATH, headers=headers_a, params={"entity_id": entity_a, "period": "2026-05"}
    )
    assert r.status_code == 200 and r.json()["total"] == 1


@pytest.mark.asyncio
async def test_wo79_list_is_org_scoped_with_overlapping_data(client, db_session):
    """The parity overlap discipline: both orgs hold the SAME supplier, period,
    country, product and amounts — only tenancy discriminates, so a broken
    filter cannot pass by luck."""
    orgs = {}
    for key in ("a", "b"):
        headers, org_id, entity_id = await _org_with_entity(client, db_session)
        txn = await _make_txn(db_session, org_id, entity_id)
        txn_id = txn.id
        await db_session.commit()
        orgs[key] = (headers, entity_id, txn_id)

    for mine, other in (("a", "b"), ("b", "a")):
        headers, entity_id, my_txn = orgs[mine]
        _, other_entity, other_txn = orgs[other]
        r = await client.get(
            PATH, headers=headers, params={"entity_id": entity_id, "period": "2026-05"}
        )
        assert r.status_code == 200, r.text
        seen = {i["id"] for i in r.json()["items"]}
        assert my_txn in seen
        assert other_txn not in seen, "TENANT LEAK — the other org's transaction is visible"
        # And the other org's ENTITY is not even addressable from here.
        cross = await client.get(
            PATH, headers=headers, params={"entity_id": other_entity, "period": "2026-05"}
        )
        assert cross.status_code == 404 and cross.json()["code"] == "entity_not_found"
