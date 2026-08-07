"""WO-84 — transport routes slice 6: the off-invoice rebate registry.

Proves `api/routes/transport/rebates.py` end to end over HTTP, plus the route
matrix every transport slice ships: the structural permission pair (EMPLOYEE
denied / ACCOUNTANT granted), the read/write SPLIT that gives the two
permissions their meaning (an AUDITOR holds `TRANSPORT_READ` but not
`VAT_WRITE`, so it reads and cannot record), the module entitlement (ADR-P3
rule 3), an org-scoped list over deliberately OVERLAPPING data, R50's source
guard refusing on the wire, §4.15's FX refusal on the wire, and
Decimal-as-string (§4.9).

It also asserts the ABSENCE that matters: recording a rebate over HTTP changes
NO `fuel_transactions` row. The merge is a close stage, because an
already-validated transaction's derived figure is product data the ENGINE owns
(`BA_fleet_fuel.md` §3.H).

Fixture strategy is the WO-82 suite's, unchanged: orgs/tokens ride the real HTTP
register flow, transport enablement is direct service setup (the module is in no
billing plan, so the HTTP toggle would 402), and every assertion about what the
API returned goes through the API.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.transport.fuel_transaction import FuelTransaction
from app.services import modules
from app.services.transport import fuel_ingest
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import make_entity

pytestmark = pytest.mark.asyncio

V = "/api/v1"
REBATES = f"{V}/transport/rebates"
AUDIT = f"{V}/transport/overcharges/audit"
PERIOD = "2026-05"
SUPPLIER = "Q8"
PARTY = "Demo Rebate Partner"


def _body(**over):
    payload = {
        "supplier": SUPPLIER,
        "country": "LV",
        "period": PERIOD,
        "source_ref": "RBT-0001",
        "source_party": PARTY,
        "rebate_date": "2026-06-05",
        "currency": "EUR",
        "amount_local": "50.00",
    }
    payload.update(over)
    return payload


async def _register_org(client: AsyncClient):
    suffix = uuid.uuid4().hex[:8]
    r = await client.post(
        f"{V}/auth/register",
        json={
            "organization_name": f"WO84 Org {suffix}",
            "name": "Owner",
            "email": f"owner-{suffix}@wo84.example.io",
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

    email = f"{stored_role}-{uuid.uuid4().hex[:8]}@wo84.example.io"
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


async def _seed_line(
    db_session,
    org_id: str,
    entity_id: str,
    *,
    line_seq: int = 1,
    period: str = PERIOD,
    net_eur: Decimal = Decimal("1350.00"),
) -> FuelTransaction:
    vat_eur = (net_eur * Decimal("0.21")).quantize(Decimal("0.01"))
    txn = await fuel_ingest.ingest_transaction(
        db_session,
        org_id,
        entity_id=entity_id,
        supplier=SUPPLIER,
        period=period,
        line_seq=line_seq,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=date(2026, 5, 12),
        station="Demo Station Riga",
        product="DIESEL",
        qty=Decimal("1000.000"),
        currency="EUR",
        net_local=net_eur,
        vat_local=vat_eur,
        gross_local=net_eur + vat_eur,
        net_eur=net_eur,
        vat_eur=vat_eur,
    )
    await db_session.commit()
    return txn


# --------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------- #


async def test_wo84_record_and_list_a_rebate_over_http(client, db_session):
    """POST records the document and the SERVER states the euro — the request
    carries no `amount_eur` at all (§4.10). Every money field crosses the wire
    as an exact decimal STRING (§4.9)."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    r = await client.post(REBATES, headers=headers, json=_body())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["amount_local"] == "50.00"
    assert body["amount_eur"] == "50.00"
    assert body["fx_source"] == "eur"
    assert body["source_ref"] == "RBT-0001"
    assert body["source_party"] == PARTY

    listed = await client.get(REBATES, headers=headers, params={"period": PERIOD})
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [body["id"]]


async def test_wo84_reposting_the_same_source_ref_corrects_it_in_place(client, db_session):
    """The same document number is the same document — a CORRECTION, not a
    duplicate row (the natural-key idempotency every transport write ships)."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    first = await client.post(REBATES, headers=headers, json=_body())
    assert first.status_code == 200, first.text
    second = await client.post(REBATES, headers=headers, json=_body(amount_local="80.00"))
    assert second.status_code == 200, second.text

    assert second.json()["id"] == first.json()["id"]
    assert second.json()["amount_eur"] == "80.00"
    listed = await client.get(REBATES, headers=headers)
    assert len(listed.json()) == 1


async def test_wo84_a_second_document_for_the_same_country_is_a_second_row(client, db_session):
    """A rebate plus a later credit note are two documents; the merge sums
    them."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    assert (await client.post(REBATES, headers=headers, json=_body())).status_code == 200
    assert (
        await client.post(REBATES, headers=headers, json=_body(source_ref="RBT-0002"))
    ).status_code == 200

    listed = await client.get(REBATES, headers=headers)
    assert len(listed.json()) == 2


async def test_wo84_recording_a_rebate_changes_no_fuel_transaction(client, db_session):
    """§3.H, asserted rather than promised: a web request writes the rebate
    REGISTRY and nothing else. The effective net still equals the invoiced net
    until the engine closes the period."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id, country="LV", seed=8431)
    txn = await _seed_line(db_session, org_id, entity.id)

    assert (await client.post(REBATES, headers=headers, json=_body())).status_code == 200

    row = await db_session.scalar(select(FuelTransaction).where(FuelTransaction.id == txn.id))
    assert row is not None
    assert Decimal(row.net_eur_eff) == Decimal("1350.00") == Decimal(row.net_eur)


# --------------------------------------------------------------------------- #
# R50's source guard, and §4.15, on the wire
# --------------------------------------------------------------------------- #


async def test_wo84_a_blank_source_ref_is_refused_on_the_wire(client, db_session):
    """`min_length=1` on the schema catches the empty string as 422; a
    WHITESPACE-only ref passes the shape check and is caught by the SERVICE's
    own `rebate_source_required` — the guard that actually matters. Either way
    no row is created."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    empty = await client.post(REBATES, headers=headers, json=_body(source_ref=""))
    assert empty.status_code == 422, empty.text

    blank = await client.post(REBATES, headers=headers, json=_body(source_ref="   "))
    assert blank.status_code == 422, blank.text
    assert blank.json()["code"] == "rebate_source_required"
    assert "detail" in blank.json()

    assert (await client.get(REBATES, headers=headers)).json() == []


async def test_wo84_a_blank_source_party_is_refused_on_the_wire(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    r = await client.post(REBATES, headers=headers, json=_body(source_party="  "))
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "rebate_source_required"
    assert (await client.get(REBATES, headers=headers)).json() == []


async def test_wo84_an_unconvertible_currency_is_refused_on_the_wire(client, db_session):
    """§4.15 over HTTP. ZAR is outside the test fixture's bundled ECB / fallback
    currency set, so no rate can be resolved — the platform refuses rather than
    guessing one, and creates no row."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    r = await client.post(REBATES, headers=headers, json=_body(currency="ZAR"))
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "fx_rate_unavailable"
    assert (await client.get(REBATES, headers=headers)).json() == []


@pytest.mark.parametrize(
    "over,code",
    [
        ({"period": "2026-13"}, "invalid_period"),
        ({"amount_local": "0.00"}, "rebate_amount_invalid"),
    ],
)
async def test_wo84_service_refusal_codes_reach_the_wire_unmapped(client, db_session, over, code):
    """The route maps nothing — every refusal a client sees is the service's own
    slug in the frozen `{"detail","code"}` shape (§4.20)."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    r = await client.post(REBATES, headers=headers, json=_body(**over))
    assert r.status_code == 422, r.text
    assert r.json()["code"] == code


# --------------------------------------------------------------------------- #
# The guard surfaces on the analytics response
# --------------------------------------------------------------------------- #


async def test_wo84_the_audit_response_carries_the_source_guard_warning(client, db_session):
    """R50 where list-price analytics would otherwise be produced silently. The
    field is additive and defaults to empty, so an existing consumer that never
    had a rebate layer sees exactly what it saw before."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id, country="LV", seed=8432)
    await _seed_line(db_session, org_id, entity.id, period="2026-04")
    await _seed_line(db_session, org_id, entity.id, period=PERIOD)

    # No rebate has ever been recorded for Q8/LV, so there is no expectation and
    # nothing to warn about — the falsifying half of the guard, on the wire.
    clean = await client.get(AUDIT, headers=headers, params={"period": PERIOD})
    assert clean.status_code == 200, clean.text
    assert clean.json()["source_warnings"] == []

    # April carried a rebate document; May has litres and none.
    assert (
        await client.post(REBATES, headers=headers, json=_body(period="2026-04"))
    ).status_code == 200

    flagged = await client.get(AUDIT, headers=headers, params={"period": PERIOD})
    assert flagged.status_code == 200, flagged.text
    warnings = flagged.json()["source_warnings"]
    assert len(warnings) == 1
    assert "Q8/LV" in warnings[0]


# --------------------------------------------------------------------------- #
# The route matrix
# --------------------------------------------------------------------------- #


async def test_wo84_employee_is_denied_and_accountant_is_granted(client, db_session):
    """The structural permission pair: the lowest role that must be refused
    really gets 403, and the lowest that must be allowed really gets 2xx."""
    owner, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    employee = await _member_with_role(client, owner, db_session, "user")
    denied = await client.post(REBATES, headers=employee, json=_body())
    assert denied.status_code == 403, denied.text
    assert (await client.get(REBATES, headers=employee)).status_code == 403

    accountant = await _member_with_role(client, owner, db_session, "accountant")
    granted = await client.post(REBATES, headers=accountant, json=_body())
    assert granted.status_code == 200, granted.text


async def test_wo84_auditor_reads_but_cannot_record(client, db_session):
    """The read/write SPLIT that gives `TRANSPORT_READ` and `VAT_WRITE` their
    separate meaning — an auditor sees the registry and can add nothing to it."""
    owner, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    assert (await client.post(REBATES, headers=owner, json=_body())).status_code == 200

    auditor = await _member_with_role(client, owner, db_session, "auditor")
    read = await client.get(REBATES, headers=auditor)
    assert read.status_code == 200, read.text
    assert len(read.json()) == 1

    write = await client.post(REBATES, headers=auditor, json=_body(source_ref="RBT-0009"))
    assert write.status_code == 403, write.text


async def test_wo84_the_module_entitlement_gates_both_verbs(client, db_session):
    """ADR-P3 rule 3 — the transport entitlement is checked INSIDE the service
    and fails CLOSED, so an org that has not activated the module gets the
    module refusal rather than an empty list."""
    headers, _org_id = await _register_org(client)

    read = await client.get(REBATES, headers=headers)
    assert read.status_code == 403, read.text
    assert read.json()["code"] == "module_not_enabled"

    write = await client.post(REBATES, headers=headers, json=_body())
    assert write.status_code == 403, write.text
    assert write.json()["code"] == "module_not_enabled"


async def test_wo84_the_list_is_org_scoped_over_overlapping_data(client, db_session):
    """Two orgs record an IDENTICAL-looking rebate — same supplier, country,
    period, document number and amount. Distinct data can pass a broken filter
    by accident; overlapping data cannot (master-context §8)."""
    a_headers, a_org = await _register_org(client)
    b_headers, b_org = await _register_org(client)
    await _enable_transport(db_session, a_org)
    await _enable_transport(db_session, b_org)

    a = await client.post(REBATES, headers=a_headers, json=_body())
    b = await client.post(REBATES, headers=b_headers, json=_body())
    assert a.status_code == 200 and b.status_code == 200
    a_id, b_id = a.json()["id"], b.json()["id"]
    assert a_id != b_id

    a_rows = {r["id"] for r in (await client.get(REBATES, headers=a_headers)).json()}
    b_rows = {r["id"] for r in (await client.get(REBATES, headers=b_headers)).json()}
    assert a_rows == {a_id}
    assert b_rows == {b_id}
    assert b_id not in a_rows and a_id not in b_rows
