"""WO-82 — transport routes slice 5: the supplier overcharge surface.

Proves `api/routes/transport/overcharges.py` end to end over HTTP: the contract
TERM admin, the read-only breach DETECTION, the claim-back LIFECYCLE, and the
booked-cash north star — plus the route matrix every transport slice ships:
the structural permission pair (EMPLOYEE denied / ACCOUNTANT granted), the
read/write SPLIT that gives the two permissions their meaning (an AUDITOR holds
`TRANSPORT_READ` but not `VAT_WRITE`, so it reads and cannot advance), the
module entitlement (ADR-P3 rule 3), the §4.4 opaque cross-tenant 404, an
org-scoped list over deliberately OVERLAPPING data, and Decimal-as-string on the
wire (§4.9).

Fixture strategy is `test_wo79_fuel_routes.py`'s, unchanged: orgs/tokens ride
the real HTTP register flow, transport enablement is direct service setup (the
module is in no billing plan, so the HTTP toggle would 402), fuel lines seed
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
TERMS = f"{V}/transport/contract-terms"
OVERCHARGES = f"{V}/transport/overcharges"
AUDIT = f"{OVERCHARGES}/audit"
PERIOD = "2026-05"
SUPPLIER = "Q8"


# --------------------------------------------------------------------------- #
# HTTP org / member helpers (the WO-79 suite's shapes)
# --------------------------------------------------------------------------- #


async def _register_org(client: AsyncClient):
    suffix = uuid.uuid4().hex[:8]
    r = await client.post(
        f"{V}/auth/register",
        json={
            "organization_name": f"WO82 Org {suffix}",
            "name": "Owner",
            "email": f"owner-{suffix}@wo82.example.io",
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
    (the `role_client` conftest pattern). Same org, so only the permission set
    differs — which is exactly what the read/write split is asserted on."""
    from sqlalchemy import update

    from app.models.user import User, UserRole

    email = f"{stored_role}-{uuid.uuid4().hex[:8]}@wo82.example.io"
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


# --------------------------------------------------------------------------- #
# Domain seeding
# --------------------------------------------------------------------------- #


async def _seed_breach(
    db_session,
    org_id: str,
    entity_id: str,
    *,
    line_seq: int = 1,
    supplier: str = SUPPLIER,
    qty: Decimal = Decimal("1000.000"),
    net_eur: Decimal = Decimal("1350.00"),
    net_eur_eff: Decimal = Decimal("1300.00"),
) -> None:
    """A 1,000 L line whose applied discount is 0.05 €/L against an agreed
    0.20 €/L: gap 0.15, recover 150.00 — hand-computed."""
    await fuel_ingest.ingest_transaction(
        db_session,
        org_id,
        entity_id=entity_id,
        supplier=supplier,
        period=PERIOD,
        line_seq=line_seq,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=date(2026, 5, 12),
        station="Demo Station Riga",
        product="DIESEL",
        qty=qty,
        currency="EUR",
        net_local=net_eur,
        vat_local=Decimal("283.50"),
        gross_local=net_eur + Decimal("283.50"),
        net_eur=net_eur,
        vat_eur=Decimal("283.50"),
        net_eur_eff=net_eur_eff,
    )


async def _org_with_breach(client: AsyncClient, db_session, *, seed: int = 826):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id, country="LV", seed=seed)
    await db_session.commit()
    r = await client.put(
        TERMS,
        headers=headers,
        json={
            "supplier": SUPPLIER,
            "country": "LV",
            "product_group": "Diesel",
            "expected_discount_eur_l": "0.20",
        },
    )
    assert r.status_code == 200, r.text
    await _seed_breach(db_session, org_id, entity.id)
    await db_session.commit()
    return headers, org_id, entity


async def _open_claim(client: AsyncClient, headers) -> dict:
    r = await client.post(
        OVERCHARGES, headers=headers, json={"supplier": SUPPLIER, "period": PERIOD}
    )
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# Terms + detection over HTTP
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo82_a_term_round_trips_as_decimal_strings(client, db_session):
    headers, org_id, _ = await _org_with_breach(client, db_session)

    r = await client.get(TERMS, headers=headers)
    assert r.status_code == 200, r.text
    (term,) = r.json()
    assert term["expected_discount_eur_l"] == "0.2000"
    assert term["max_net_eur_l"] is None
    assert term["station_like"] == ""
    assert term["active"] is True


@pytest.mark.asyncio
async def test_wo82_a_term_asserting_no_figure_is_refused_422(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    await db_session.commit()

    r = await client.put(
        TERMS,
        headers=headers,
        json={"supplier": SUPPLIER, "country": "LV", "product_group": "Diesel"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "term_has_no_figure"


@pytest.mark.asyncio
async def test_wo82_an_unknown_product_group_is_refused_422(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    await db_session.commit()

    r = await client.put(
        TERMS,
        headers=headers,
        json={
            "supplier": SUPPLIER,
            "country": "LV",
            "product_group": "Kerosene",
            "expected_discount_eur_l": "0.20",
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "invalid_product_group"


@pytest.mark.asyncio
async def test_wo82_a_term_can_be_removed_and_the_removal_is_reported(client, db_session):
    headers, org_id, _ = await _org_with_breach(client, db_session)

    r = await client.delete(
        TERMS,
        headers=headers,
        params={"supplier": SUPPLIER, "country": "LV", "product_group": "Diesel"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"removed": True}
    # A second delete is a truthful no-op, not a 404.
    r = await client.delete(
        TERMS,
        headers=headers,
        params={"supplier": SUPPLIER, "country": "LV", "product_group": "Diesel"},
    )
    assert r.json() == {"removed": False}
    assert await client.get(TERMS, headers=headers) is not None


@pytest.mark.asyncio
async def test_wo82_the_audit_returns_the_breach_with_its_basis_and_framing(client, db_session):
    headers, org_id, _ = await _org_with_breach(client, db_session)

    r = await client.get(AUDIT, headers=headers, params={"period": PERIOD})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["currency"] == "EUR"
    assert body["price_basis"] == "NET EUR/L, final — VAT excluded, rebates applied"
    assert body["legal_framing"].startswith("Money the supplier owes")
    assert body["recover_eur"] == "150.00"
    (breach,) = body["breaches"]
    assert breach["flag"] == "short discount"
    assert breach["gap_eur_l"] == "0.1500"
    assert breach["recover_eur"] == "150.00"
    assert breach["document_currency"] == "EUR"
    # §4.9 — every money/rate figure is a JSON STRING, never a JSON number.
    for key in ("recover_eur", "gap_eur_l", "eur_l_doc", "eur_l_eff", "qty"):
        assert isinstance(breach[key], str), key


@pytest.mark.asyncio
async def test_wo82_an_unconfigured_supplier_returns_no_breach_not_an_error(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id, country="LV", seed=8261)
    await _seed_breach(db_session, org_id, entity.id)
    await db_session.commit()

    r = await client.get(AUDIT, headers=headers, params={"period": PERIOD})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["breaches"] == []
    assert body["recover_eur"] == "0.00"
    assert body["lines_without_terms"] == 1


@pytest.mark.asyncio
async def test_wo82_an_invalid_period_is_refused_422(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    await db_session.commit()

    r = await client.get(AUDIT, headers=headers, params={"period": "2026-13"})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "invalid_period"


# --------------------------------------------------------------------------- #
# The claim-back lifecycle over HTTP
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo82_opening_a_claimback_freezes_the_detected_euro(client, db_session):
    headers, org_id, _ = await _org_with_breach(client, db_session)

    claim = await _open_claim(client, headers)
    assert claim["status"] == "detected"
    assert claim["detected_eur"] == "150.00"
    assert claim["lines_count"] == 1
    assert claim["recovered_eur"] is None
    assert claim["currency"] == "EUR"

    # Idempotent on the natural key — 200 on both calls (the WO-76 posture).
    again = await _open_claim(client, headers)
    assert again["id"] == claim["id"]


@pytest.mark.asyncio
async def test_wo82_opening_with_nothing_detected_is_refused_422(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id, country="LV", seed=8262)
    await db_session.commit()
    r = await client.put(
        TERMS,
        headers=headers,
        json={
            "supplier": SUPPLIER,
            "country": "LV",
            "product_group": "Diesel",
            "expected_discount_eur_l": "0.20",
        },
    )
    assert r.status_code == 200
    # applied is exactly 0.20 — the boundary case, honoured.
    await _seed_breach(db_session, org_id, entity.id, net_eur_eff=Decimal("1150.00"))
    await db_session.commit()

    r = await client.post(
        OVERCHARGES, headers=headers, json={"supplier": SUPPLIER, "period": PERIOD}
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "no_overcharge_detected"
    assert (await client.get(OVERCHARGES, headers=headers)).json() == []


@pytest.mark.asyncio
async def test_wo82_the_whole_chain_walks_over_http_and_books_cash(client, db_session):
    headers, org_id, _ = await _org_with_breach(client, db_session)
    claim = await _open_claim(client, headers)
    path = f"{OVERCHARGES}/{claim['id']}/advance"

    for to_status in ("packaged", "claimed"):
        r = await client.post(path, headers=headers, json={"to_status": to_status})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == to_status

    r = await client.post(
        path,
        headers=headers,
        json={"to_status": "recovered", "recovered_eur": "120.00", "note": "Credit note"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "recovered"
    assert body["recovered_eur"] == "120.00"

    total = await client.get(f"{OVERCHARGES}/total", headers=headers, params={"year": 2026})
    assert total.status_code == 200, total.text
    assert total.json() == {"year": 2026, "currency": "EUR", "recovered_eur": "120.00"}


@pytest.mark.asyncio
async def test_wo82_an_illegal_transition_is_409_with_nothing_mutated(client, db_session):
    headers, org_id, _ = await _org_with_breach(client, db_session)
    claim = await _open_claim(client, headers)

    r = await client.post(
        f"{OVERCHARGES}/{claim['id']}/advance", headers=headers, json={"to_status": "claimed"}
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "overcharge_transition_invalid"

    fresh = await client.get(f"{OVERCHARGES}/{claim['id']}", headers=headers)
    assert fresh.json() == claim


@pytest.mark.asyncio
async def test_wo82_an_unknown_status_is_refused_422(client, db_session):
    headers, org_id, _ = await _org_with_breach(client, db_session)
    claim = await _open_claim(client, headers)

    r = await client.post(
        f"{OVERCHARGES}/{claim['id']}/advance", headers=headers, json={"to_status": "settled"}
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "invalid_overcharge_status"

    r = await client.get(OVERCHARGES, headers=headers, params={"status": "settled"})
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_overcharge_status"


@pytest.mark.asyncio
async def test_wo82_recovering_more_than_detected_is_refused_422(client, db_session):
    headers, org_id, _ = await _org_with_breach(client, db_session)
    claim = await _open_claim(client, headers)
    path = f"{OVERCHARGES}/{claim['id']}/advance"
    for to_status in ("packaged", "claimed"):
        assert (
            await client.post(path, headers=headers, json={"to_status": to_status})
        ).status_code == 200

    r = await client.post(
        path, headers=headers, json={"to_status": "recovered", "recovered_eur": "150.01"}
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "recovered_amount_invalid"
    assert (await client.get(f"{OVERCHARGES}/{claim['id']}", headers=headers)).json()[
        "status"
    ] == "claimed"

    r = await client.post(
        path, headers=headers, json={"to_status": "recovered", "recovered_eur": "150.00"}
    )
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# The north star, wired into the WO-81 dashboard
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo82_the_dashboard_overcharges_euro_is_the_recovered_total(client, db_session):
    """WO-81's deviation 1 closed: the dashboard now carries the field, and it
    equals the booked-cash total — not the detected exposure (150.00)."""
    headers, org_id, _ = await _org_with_breach(client, db_session)
    claim = await _open_claim(client, headers)
    path = f"{OVERCHARGES}/{claim['id']}/advance"
    for payload in (
        {"to_status": "packaged"},
        {"to_status": "claimed"},
        {"to_status": "recovered", "recovered_eur": "90.00"},
    ):
        assert (await client.post(path, headers=headers, json=payload)).status_code == 200

    r = await client.get(
        f"{V}/transport/recovery-dashboard", headers=headers, params={"year": 2026}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overcharges_eur"] == "90.00"
    # The VAT reconciliation R38 demands is UNCHANGED by the second cash stream.
    assert body["recovered_eur"] == "0.00"
    assert body["awaiting_eur"] == "0.00"
    assert body["claimable_eur"] == "0.00"


@pytest.mark.asyncio
async def test_wo82_the_dashboard_reports_zero_overcharges_when_none_came_back(client, db_session):
    headers, org_id, _ = await _org_with_breach(client, db_session)
    await _open_claim(client, headers)  # detected, never recovered

    r = await client.get(
        f"{V}/transport/recovery-dashboard", headers=headers, params={"year": 2026}
    )
    assert r.json()["overcharges_eur"] == "0.00"


# --------------------------------------------------------------------------- #
# The route matrix
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo82_employee_is_denied_and_accountant_is_granted(client, db_session):
    owner_headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    await db_session.commit()
    employee = await _member_with_role(client, owner_headers, db_session, "user")
    accountant = await _member_with_role(client, owner_headers, db_session, "accountant")

    assert (await client.get(OVERCHARGES, headers=employee)).status_code == 403  # no TRANSPORT_READ
    assert (await client.get(OVERCHARGES, headers=accountant)).status_code == 200


@pytest.mark.asyncio
async def test_wo82_an_auditor_can_read_but_not_write(client, db_session):
    """The read/write split that makes the two permissions mean something:
    AUDITOR holds `TRANSPORT_READ` (read every money surface for assurance) and
    NOT `VAT_WRITE` (it books nothing), so it reads the worklist and cannot
    drive it."""
    owner_headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    await db_session.commit()
    auditor = await _member_with_role(client, owner_headers, db_session, "auditor")

    assert (await client.get(OVERCHARGES, headers=auditor)).status_code == 200
    assert (await client.get(AUDIT, headers=auditor, params={"period": PERIOD})).status_code == 200
    assert (
        await client.post(
            OVERCHARGES, headers=auditor, json={"supplier": SUPPLIER, "period": PERIOD}
        )
    ).status_code == 403
    assert (
        await client.put(
            TERMS,
            headers=auditor,
            json={
                "supplier": SUPPLIER,
                "country": "LV",
                "product_group": "Diesel",
                "expected_discount_eur_l": "0.20",
            },
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_wo82_module_disabled_refuses_403_module_not_enabled(client, db_session):
    headers, _org_id = await _register_org(client)  # transport NOT enabled

    for resp in (
        await client.get(OVERCHARGES, headers=headers),
        await client.get(AUDIT, headers=headers, params={"period": PERIOD}),
        await client.get(TERMS, headers=headers),
    ):
        assert resp.status_code == 403, resp.text
        assert resp.json()["code"] == "module_not_enabled"


@pytest.mark.asyncio
async def test_wo82_a_cross_tenant_claim_id_is_an_opaque_404(client, db_session):
    headers_a, _, _ = await _org_with_breach(client, db_session, seed=8271)
    headers_b, _, _ = await _org_with_breach(client, db_session, seed=8272)
    claim_b = await _open_claim(client, headers_b)

    r = await client.get(f"{OVERCHARGES}/{claim_b['id']}", headers=headers_a)
    assert r.status_code != 403, "a 403 would confirm the object exists (§4.4)"
    assert r.status_code == 404
    assert r.json()["code"] == "overcharge_claim_not_found"

    r = await client.post(
        f"{OVERCHARGES}/{claim_b['id']}/advance", headers=headers_a, json={"to_status": "packaged"}
    )
    assert r.status_code == 404
    # B's claim-back is untouched.
    assert (await client.get(f"{OVERCHARGES}/{claim_b['id']}", headers=headers_b)).json()[
        "status"
    ] == "detected"


@pytest.mark.asyncio
async def test_wo82_the_worklist_is_org_scoped_with_overlapping_data(client, db_session):
    """Both orgs carry the SAME supplier code, the SAME period and the SAME
    amounts — distinct data could pass a broken filter by accident."""
    headers_a, _, _ = await _org_with_breach(client, db_session, seed=8281)
    headers_b, _, _ = await _org_with_breach(client, db_session, seed=8282)
    claim_a = await _open_claim(client, headers_a)
    claim_b = await _open_claim(client, headers_b)

    a_ids = {c["id"] for c in (await client.get(OVERCHARGES, headers=headers_a)).json()}
    b_ids = {c["id"] for c in (await client.get(OVERCHARGES, headers=headers_b)).json()}
    assert a_ids == {claim_a["id"]}
    assert b_ids == {claim_b["id"]}
    assert not a_ids & b_ids

    a_terms = {t["id"] for t in (await client.get(TERMS, headers=headers_a)).json()}
    b_terms = {t["id"] for t in (await client.get(TERMS, headers=headers_b)).json()}
    assert a_terms and b_terms and not a_terms & b_terms
