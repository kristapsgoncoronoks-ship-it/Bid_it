"""WO-87 — transport routes slice 7: the overpay / benchmark analyses over HTTP.

Proves `api/routes/transport/savings.py` end to end, plus the route matrix every
transport slice ships: the structural permission pair (EMPLOYEE denied /
ACCOUNTANT granted), the module entitlement (ADR-P3 rule 3), an org-scoped read
over deliberately OVERLAPPING data, the service's own refusal codes on the wire,
and Decimal-as-string (§4.9).

It also asserts the ABSENCES this surface is defined by (R53): every response
carries the *"negotiation evidence, NOT a contractual claim-back"* framing and
the NET EUR/L basis, and there is no write verb anywhere on the router.

Fixture strategy is the WO-82/WO-84 suites', unchanged: orgs/tokens ride the real
HTTP register flow, transport enablement is direct service setup (the module is
in no billing plan, so the HTTP toggle would 402), and every assertion about what
the API returned goes through the API.
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

pytestmark = pytest.mark.asyncio

V = "/api/v1"
SAME_DAY = f"{V}/transport/savings/same-day"
BENCHMARK = f"{V}/transport/savings/internal-benchmark"
REBATE = f"{V}/transport/savings/expected-rebate"
PERIOD = "2026-05"
DAY = date(2026, 5, 12)

_SEQ = {"n": 0}


async def _register_org(client: AsyncClient):
    suffix = uuid.uuid4().hex[:8]
    r = await client.post(
        f"{V}/auth/register",
        json={
            "organization_name": f"WO87 Org {suffix}",
            "name": "Owner",
            "email": f"owner-{suffix}@wo87.example.io",
            "password": "supersecret",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['token']['access_token']}"}, body["organization"]["id"]


async def _enable_transport(db_session, org_id: str) -> None:
    await modules.set_enabled(db_session, org_id, "transport", True)


async def _member_with_role(client: AsyncClient, owner_headers, db_session, stored_role: str):
    from sqlalchemy import update

    from app.models.user import User, UserRole

    email = f"{stored_role}-{uuid.uuid4().hex[:8]}@wo87.example.io"
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


async def _line(
    db_session,
    org_id: str,
    entity_id: str,
    *,
    supplier: str,
    qty: Decimal = Decimal("1000.000"),
    net_eur: Decimal = Decimal("1500.00"),
    net_eur_eff: Decimal | None = None,
    period: str = PERIOD,
    country: str = "LV",
):
    _SEQ["n"] += 1
    vat_eur = (net_eur * Decimal("0.21")).quantize(Decimal("0.01"))
    row = await fuel_ingest.ingest_transaction(
        db_session,
        org_id,
        entity_id=entity_id,
        supplier=supplier,
        period=period,
        line_seq=_SEQ["n"],
        country=country,
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=DAY,
        station="Demo Station Riga",
        product="DIESEL",
        qty=qty,
        currency="EUR",
        net_local=net_eur,
        vat_local=vat_eur,
        gross_local=net_eur + vat_eur,
        net_eur=net_eur,
        vat_eur=vat_eur,
        net_eur_eff=net_eur_eff,
    )
    await db_session.commit()
    return row


async def _seed_two_suppliers(db_session, client):
    """AAA at 1.5000 EUR/L and CCC at 1.4000 EUR/L, 1,000 L each, same day and
    country: a 100.00 EUR overpay attributable to AAA."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id, country="LV")
    await db_session.commit()
    await _line(db_session, org_id, entity.id, supplier="AAA", net_eur=Decimal("1500.00"))
    await _line(db_session, org_id, entity.id, supplier="CCC", net_eur=Decimal("1400.00"))
    return headers, org_id, entity.id


# --------------------------------------------------------------------------- #
# The happy paths
# --------------------------------------------------------------------------- #


async def test_wo87_same_day_overpay_over_http(client, db_session):
    """The hand-computed figure, served. Every money field crosses the wire as
    an exact decimal STRING (§4.9) — including `litres`, which is the €/L
    denominator and would move the prices if it round-tripped through a float."""
    headers, _org_id, _entity_id = await _seed_two_suppliers(db_session, client)

    r = await client.get(SAME_DAY, headers=headers, params={"period": PERIOD})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["avoidable_eur"] == "100.00"
    assert body["currency"] == "EUR"
    assert body["product_group"] == "Diesel"
    assert body["grain"] == "same-day, same-country cheapest rival"
    assert body["price_basis"] == "NET EUR/L, final — VAT excluded, rebates applied"
    assert body["legal_framing"] == "Negotiation evidence, NOT a contractual claim-back"

    assert len(body["findings"]) == 1
    finding = body["findings"][0]
    assert finding["supplier"] == "AAA"
    assert finding["cheapest_rival_supplier"] == "CCC"
    assert finding["eur_l_eff"] == "1.5000"
    assert finding["cheapest_rival_eur_l_eff"] == "1.4000"
    assert finding["delta_eur_l"] == "0.1000"
    assert finding["avoidable_eur"] == "100.00"
    assert finding["litres"] == "1000.000"
    assert finding["suppliers_that_day"] == 2

    assert body["by_supplier"] == [
        {
            "country": "LV",
            "supplier": "AAA",
            "litres": "1000.000",
            "avoidable_eur": "100.00",
            "days": 1,
        }
    ]
    assert body["days_compared"] == 1
    assert body["days_without_a_rival"] == 0


async def test_wo87_internal_benchmark_over_http(client, db_session):
    """R52's second grain, served with ITS grain label. The best supplier is on
    the sheet at a zero gap — it is the one the volume would be routed to."""
    headers, _org_id, _entity_id = await _seed_two_suppliers(db_session, client)

    r = await client.get(BENCHMARK, headers=headers, params={"period": PERIOD})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["benchmark_gap_eur"] == "100.00"
    assert body["grain"] == "country × month, best-of-your-own-suppliers"
    assert body["legal_framing"] == "Negotiation evidence, NOT a contractual claim-back"
    assert [
        (row["supplier"], row["gap_eur_l"], row["benchmark_gap_eur"]) for row in body["rows"]
    ] == [
        ("AAA", "0.1000", "100.00"),
        ("CCC", "0.0000", "0.00"),
    ]
    assert all(row["best_supplier"] == "CCC" for row in body["rows"])
    assert body["countries_compared"] == 1
    assert body["suppliers_compared"] == 2


async def test_wo87_expected_rebate_over_http(client, db_session):
    """Q8/LV grants 0.0600 EUR/L in April and nothing in May: one finding, an
    advisory magnitude of 0.0600 x 1,000.000 = 60.00 EUR."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id, country="LV")
    await db_session.commit()
    await _line(
        db_session,
        org_id,
        entity.id,
        supplier="Q8",
        period="2026-04",
        net_eur=Decimal("1500.00"),
        net_eur_eff=Decimal("1440.00"),
    )
    await _line(db_session, org_id, entity.id, supplier="Q8", net_eur=Decimal("1500.00"))

    r = await client.get(REBATE, headers=headers, params={"period": PERIOD})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["tolerance_eur_l"] == "0.005"
    assert body["expectations"] == [
        {"supplier": "Q8", "country": "LV", "typical_eur_l": "0.0600", "learned_from_lines": 1}
    ]
    assert len(body["findings"]) == 1
    finding = body["findings"][0]
    assert finding["applied_eur_l"] == "0.0000"
    assert finding["typical_eur_l"] == "0.0600"
    assert finding["expected_rebate_eur"] == "60.00"
    assert finding["eur_l_doc"] == "1.5000"
    assert body["expected_rebate_eur"] == "60.00"
    assert body["legal_framing"] == "Negotiation evidence, NOT a contractual claim-back"


async def test_wo87_an_empty_month_is_a_200_with_zeroes(client, db_session):
    """A month with no diesel is a 200 with no findings and a zero total, never
    a 404 and never an exception."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    for url, total in ((SAME_DAY, "avoidable_eur"), (BENCHMARK, "benchmark_gap_eur")):
        r = await client.get(url, headers=headers, params={"period": PERIOD})
        assert r.status_code == 200, r.text
        assert r.json()[total] == "0.00"

    r = await client.get(REBATE, headers=headers, params={"period": PERIOD})
    assert r.status_code == 200, r.text
    assert r.json()["findings"] == []


async def test_wo87_the_country_filter_narrows_the_scope(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id, country="LV")
    await db_session.commit()
    await _line(
        db_session, org_id, entity.id, supplier="AAA", country="LV", net_eur=Decimal("1500.00")
    )
    await _line(
        db_session, org_id, entity.id, supplier="CCC", country="LV", net_eur=Decimal("1400.00")
    )
    await _line(
        db_session, org_id, entity.id, supplier="AAA", country="EE", net_eur=Decimal("1600.00")
    )
    await _line(
        db_session, org_id, entity.id, supplier="CCC", country="EE", net_eur=Decimal("1300.00")
    )

    everywhere = await client.get(SAME_DAY, headers=headers, params={"period": PERIOD})
    lv_only = await client.get(
        SAME_DAY, headers=headers, params={"period": PERIOD, "country": "LV"}
    )

    assert everywhere.json()["avoidable_eur"] == "400.00"
    assert lv_only.json()["avoidable_eur"] == "100.00"
    assert lv_only.json()["country"] == "LV"


# --------------------------------------------------------------------------- #
# The refusals, all the service's own codes
# --------------------------------------------------------------------------- #


async def test_wo87_a_malformed_period_is_422_invalid_period(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    for url in (SAME_DAY, BENCHMARK, REBATE):
        r = await client.get(url, headers=headers, params={"period": "2026-13"})
        assert r.status_code == 422, r.text
        assert r.json()["code"] == "invalid_period"


async def test_wo87_a_malformed_country_is_422_invalid_country(client, db_session):
    """The schema catches SHAPE (two characters) and the service catches the
    business rule (alpha-2). A two-character non-alpha value gets past the
    query constraint and is refused by the service."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    r = await client.get(SAME_DAY, headers=headers, params={"period": PERIOD, "country": "1V"})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "invalid_country"


async def test_wo87_a_line_with_no_eur_basis_refuses_on_the_wire(client, db_session):
    """§4.15 over HTTP. The stored row is deliberately given the platform's
    `"unknown"` FX provenance — *"no rate available → the EUR figure is NULL,
    never a guessed number"* — and the analysis refuses rather than comparing at
    a guessed rate or over a silently smaller set."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id, country="LV")
    await db_session.commit()
    await _line(db_session, org_id, entity.id, supplier="AAA", net_eur=Decimal("1500.00"))
    bad = await _line(db_session, org_id, entity.id, supplier="CCC", net_eur=Decimal("1400.00"))
    bad.fx_source = "unknown"
    await db_session.commit()

    r = await client.get(SAME_DAY, headers=headers, params={"period": PERIOD})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "fx_rate_unavailable"
    assert "X-Request-ID" in r.headers


async def test_wo87_the_module_entitlement_gates_every_route(client, db_session):
    """ADR-P3 rule 3 — the entitlement is checked INSIDE the service and fails
    CLOSED. Not enabling transport is a 403 `module_not_enabled`, not an empty
    analysis (which would read as "you never overpaid anyone")."""
    headers, _org_id = await _register_org(client)

    for url in (SAME_DAY, BENCHMARK, REBATE):
        r = await client.get(url, headers=headers, params={"period": PERIOD})
        assert r.status_code == 403, r.text
        assert r.json()["code"] == "module_not_enabled"


# --------------------------------------------------------------------------- #
# Authorization — the structural permission pair
# --------------------------------------------------------------------------- #


async def test_wo87_an_employee_is_denied_and_an_accountant_is_granted(client, db_session):
    """`TRANSPORT_READ`, declared on the router (ADR-0024). One role that must
    be refused and one that must be allowed — the definition-of-done §5 pair."""
    owner_headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    # The stored `user` role maps to `authz.Role.EMPLOYEE` (app/models/user.py).
    employee = await _member_with_role(client, owner_headers, db_session, "user")
    accountant = await _member_with_role(client, owner_headers, db_session, "accountant")

    for url in (SAME_DAY, BENCHMARK, REBATE):
        denied = await client.get(url, headers=employee, params={"period": PERIOD})
        assert denied.status_code == 403, denied.text

        granted = await client.get(url, headers=accountant, params={"period": PERIOD})
        assert granted.status_code == 200, granted.text


async def test_wo87_an_anonymous_request_is_refused(client):
    r = await client.get(SAME_DAY, params={"period": PERIOD})
    assert r.status_code == 401


async def test_wo87_the_surface_exposes_no_write_verb_over_http(client, db_session):
    """R53 at the transport layer: there is nothing to open, freeze, package or
    send here, so a POST to the analysis path is not a route at all."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    for url in (SAME_DAY, BENCHMARK, REBATE):
        r = await client.post(url, headers=headers, json={})
        assert r.status_code == 405, f"{url} accepted a write: {r.status_code}"


# --------------------------------------------------------------------------- #
# Tenancy — identical-looking data in two orgs
# --------------------------------------------------------------------------- #


async def test_wo87_an_org_never_sees_another_orgs_prices(client, db_session):
    """§4.1/§4.4. Both orgs carry the SAME suppliers, day, litres and euros, so
    a missing tenant filter could not pass by accident — and org A additionally
    would acquire org B's much cheaper supplier as its rival, changing its
    figure sixfold."""
    headers_a, org_a = await _register_org(client)
    headers_b, org_b = await _register_org(client)
    await _enable_transport(db_session, org_a)
    await _enable_transport(db_session, org_b)
    entity_a = await make_entity(db_session, org_a, country="LV", seed=21)
    entity_b = await make_entity(db_session, org_b, country="LV", seed=22)
    await db_session.commit()

    for org_id, entity_id in ((org_a, entity_a.id), (org_b, entity_b.id)):
        await _line(db_session, org_id, entity_id, supplier="AAA", net_eur=Decimal("1500.00"))
        await _line(db_session, org_id, entity_id, supplier="CCC", net_eur=Decimal("1400.00"))
    await _line(db_session, org_b, entity_b.id, supplier="ZZZ", net_eur=Decimal("1000.00"))

    a = await client.get(SAME_DAY, headers=headers_a, params={"period": PERIOD})
    b = await client.get(SAME_DAY, headers=headers_b, params={"period": PERIOD})

    assert a.json()["lines_compared"] == 2
    assert a.json()["avoidable_eur"] == "100.00"
    assert {f["cheapest_rival_supplier"] for f in a.json()["findings"]} == {"CCC"}

    assert b.json()["lines_compared"] == 3
    assert b.json()["avoidable_eur"] == "900.00"
    assert {f["cheapest_rival_supplier"] for f in b.json()["findings"]} == {"ZZZ"}


async def test_wo87_a_cross_tenant_transaction_id_appears_nowhere(client, db_session):
    """The expected-rebate findings are the only place this surface emits an
    object id. An org bound to A must never see one of B's — the opaque-404
    principle applied to a listing rather than a fetch (§4.4: object-id guessing
    must yield zero information)."""
    headers_a, org_a = await _register_org(client)
    _headers_b, org_b = await _register_org(client)
    await _enable_transport(db_session, org_a)
    await _enable_transport(db_session, org_b)
    entity_a = await make_entity(db_session, org_a, country="LV", seed=31)
    entity_b = await make_entity(db_session, org_b, country="LV", seed=32)
    await db_session.commit()

    b_ids = set()
    for org_id, entity_id, bucket in (
        (org_a, entity_a.id, None),
        (org_b, entity_b.id, b_ids),
    ):
        await _line(
            db_session,
            org_id,
            entity_id,
            supplier="Q8",
            period="2026-04",
            net_eur=Decimal("1500.00"),
            net_eur_eff=Decimal("1440.00"),
        )
        row = await _line(db_session, org_id, entity_id, supplier="Q8", net_eur=Decimal("1500.00"))
        if bucket is not None:
            bucket.add(row.id)

    r = await client.get(REBATE, headers=headers_a, params={"period": PERIOD})
    assert r.status_code == 200, r.text
    seen = {f["fuel_transaction_id"] for f in r.json()["findings"]}
    assert seen and not (seen & b_ids)
