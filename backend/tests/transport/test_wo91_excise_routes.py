"""WO-91 — transport routes slice 8: the diesel-excise RATE registry over HTTP.

Proves the rate half of `api/routes/transport/excise.py` end to end, plus the
route matrix every transport slice ships: the structural permission pair
(EMPLOYEE denied / ACCOUNTANT granted), the read/write split (an AUDITOR reads
but cannot type a rate), the module entitlement (ADR-P3 rule 3), an org-scoped
read over deliberately OVERLAPPING data, the service's own refusal codes on the
wire, and Decimal-as-string (§4.9).

It also asserts what R42's acceptance line requires — *"The UI shows the
indicative-rate and eligibility caveats on **every surface that shows the
number**"* — as a property of the RESPONSE rather than of a screen: the rate
table is one of those surfaces, so it carries `eligibility`,
`eligibility_asserted: false`, `rate_caveat` and `legal_framing`.

Fixture strategy is the WO-82/WO-84/WO-87 suites', unchanged: orgs/tokens ride
the real HTTP register flow, transport enablement is direct service setup (the
module is in no billing plan, so the HTTP toggle would 402), and every assertion
about what the API returned goes through the API.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.services import modules
from app.services.transport import excise
from tests.transport.conftest import make_entity  # noqa: F401  (shared fixture module import)

pytestmark = pytest.mark.asyncio

V = "/api/v1"
RATES = f"{V}/transport/excise/rates"


async def _register_org(client: AsyncClient):
    suffix = uuid.uuid4().hex[:8]
    r = await client.post(
        f"{V}/auth/register",
        json={
            "organization_name": f"WO91 Org {suffix}",
            "name": "Owner",
            "email": f"owner-{suffix}@wo91.example.io",
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

    email = f"{stored_role}-{uuid.uuid4().hex[:8]}@wo91.example.io"
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


def _by_country(payload: dict) -> dict[str, dict]:
    return {row["country"]: row for row in payload["rates"]}


# --------------------------------------------------------------------------- #
# The read surface
# --------------------------------------------------------------------------- #


async def test_wo91_rates_list_every_refund_state_at_the_harvested_default(client, db_session):
    """An organization that has typed nothing still sees the rate its figure
    WILL be computed at — the EUR 30.00 placeholder — rather than an empty
    list, and every row says it is a placeholder."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    r = await client.get(RATES, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["countries"] == ["BE", "ES", "FR", "HR", "HU", "IT", "SI"]
    assert body["default_rate_eur_per_1000l"] == "30.0000"
    rows = _by_country(body)
    assert set(rows) == set(body["countries"])
    assert all(row["is_override"] is False for row in rows.values())
    assert all(row["rate_eur_per_1000l"] == "30.0000" for row in rows.values())


async def test_wo91_every_rate_surface_carries_the_caveats(client, db_session):
    """R42's acceptance, asserted on the wire: the caveats are REQUIRED fields
    sourced from the service's single constants, so a client cannot receive the
    rate without them and cannot render a softened wording."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    body = (await client.get(RATES, headers=headers)).json()
    assert body["eligibility"] == excise.ELIGIBILITY_STATEMENT
    assert body["eligibility_asserted"] is False
    assert body["rate_caveat"] == excise.RATE_CAVEAT
    assert body["legal_framing"] == excise.LEGAL_FRAMING
    assert body["currency"] == "EUR"
    assert all(row["rate_caveat"] == excise.RATE_CAVEAT for row in body["rates"])


async def test_wo91_rates_cross_the_wire_as_decimal_strings(client, db_session):
    """§4.9 — a rate is a `Decimal` end to end. `27.1234` must survive verbatim;
    a float round-trip would be visible as a JSON number."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    put = await client.put(
        RATES, headers=headers, json={"country": "HU", "rate_eur_per_1000l": "27.1234"}
    )
    assert put.status_code == 200, put.text
    row = _by_country(put.json())["HU"]
    assert row["rate_eur_per_1000l"] == "27.1234"
    assert isinstance(row["rate_eur_per_1000l"], str)


# --------------------------------------------------------------------------- #
# The write surface
# --------------------------------------------------------------------------- #


async def test_wo91_setting_and_removing_an_override_round_trips(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    put = await client.put(
        RATES, headers=headers, json={"country": "FR", "rate_eur_per_1000l": "22.5000"}
    )
    assert put.status_code == 200, put.text
    rows = _by_country(put.json())
    assert rows["FR"] == {
        "country": "FR",
        "rate_eur_per_1000l": "22.5000",
        "is_override": True,
        "rate_caveat": excise.RATE_CAVEAT,
    }
    assert rows["IT"]["is_override"] is False

    delete = await client.delete(RATES, headers=headers, params={"country": "FR"})
    assert delete.status_code == 200, delete.text
    after = _by_country(delete.json())["FR"]
    assert after["is_override"] is False
    assert after["rate_eur_per_1000l"] == "30.0000"


async def test_wo91_the_service_refusals_reach_the_wire_verbatim(client, db_session):
    """This module maps nothing: the codes are the service's own, so the wire
    vocabulary cannot drift from the service layer (§4.20)."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    unsupported = await client.put(
        RATES, headers=headers, json={"country": "LV", "rate_eur_per_1000l": "30.0000"}
    )
    assert unsupported.status_code == 422, unsupported.text
    assert unsupported.json()["code"] == "excise_country_not_supported"

    # `gt=0` is caught by the schema (422 without a service code); the SERVICE
    # refusal is proven on the boundary the schema cannot express — a value that
    # parses and is positive is accepted, and the service's own code appears for
    # a state it rejects. Both halves of "schemas catch shape, services catch
    # business rules" (master-context DoD 3).
    tiny = await client.put(
        RATES, headers=headers, json={"country": "FR", "rate_eur_per_1000l": "0.0001"}
    )
    assert tiny.status_code == 200, tiny.text

    zero = await client.put(
        RATES, headers=headers, json={"country": "FR", "rate_eur_per_1000l": "0"}
    )
    assert zero.status_code == 422, zero.text

    bad_delete = await client.delete(RATES, headers=headers, params={"country": "LV"})
    assert bad_delete.status_code == 422
    assert bad_delete.json()["code"] == "excise_country_not_supported"


# --------------------------------------------------------------------------- #
# Authorization — the structural permission pair, and the read/write split
# --------------------------------------------------------------------------- #


async def test_wo91_an_employee_is_denied_and_an_accountant_is_granted(client, db_session):
    """`TRANSPORT_READ`, declared on the router (ADR-0024). One role that must
    be refused and one that must be allowed — the definition-of-done §5 pair."""
    owner_headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    employee = await _member_with_role(client, owner_headers, db_session, "user")
    accountant = await _member_with_role(client, owner_headers, db_session, "accountant")

    assert (await client.get(RATES, headers=employee)).status_code == 403
    granted = await client.get(RATES, headers=accountant)
    assert granted.status_code == 200, granted.text


async def test_wo91_an_auditor_can_read_a_rate_but_not_type_one(client, db_session):
    """The per-route `VAT_WRITE` override. An AUDITOR holds `TRANSPORT_READ` and
    not `VAT_WRITE`, which is exactly the split configuring master data needs —
    and the SAME existing permission `overcharges.py` gates a contract term
    with. No permission member was invented for excise (§10)."""
    owner_headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    auditor = await _member_with_role(client, owner_headers, db_session, "auditor")

    assert (await client.get(RATES, headers=auditor)).status_code == 200
    assert (
        await client.put(
            RATES, headers=auditor, json={"country": "FR", "rate_eur_per_1000l": "22.5000"}
        )
    ).status_code == 403
    assert (
        await client.delete(RATES, headers=auditor, params={"country": "FR"})
    ).status_code == 403


async def test_wo91_an_anonymous_request_is_refused(client):
    assert (await client.get(RATES)).status_code == 401


async def test_wo91_module_disabled_refuses_403_module_not_enabled(client, db_session):
    """ADR-P3 rule 3 — the service gate, fail-CLOSED, surfaced verbatim."""
    headers, _org_id = await _register_org(client)
    r = await client.get(RATES, headers=headers)
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "module_not_enabled"

    w = await client.put(RATES, headers=headers, json={"country": "FR", "rate_eur_per_1000l": "1"})
    assert w.status_code == 403
    assert w.json()["code"] == "module_not_enabled"


# --------------------------------------------------------------------------- #
# Tenancy — identical-looking data in two orgs
# --------------------------------------------------------------------------- #


async def test_wo91_an_org_never_sees_another_orgs_rate(client, db_session):
    """§4.1. Both orgs override the SAME state, so a missing tenant filter
    cannot pass by accident: the values are the only discriminator, and each
    org must read back exactly the rate it typed."""
    a_headers, a_org = await _register_org(client)
    b_headers, b_org = await _register_org(client)
    await _enable_transport(db_session, a_org)
    await _enable_transport(db_session, b_org)

    for headers, rate in ((a_headers, "22.5000"), (b_headers, "27.7500")):
        put = await client.put(
            RATES, headers=headers, json={"country": "FR", "rate_eur_per_1000l": rate}
        )
        assert put.status_code == 200, put.text

    for headers, mine, theirs in (
        (a_headers, "22.5000", "27.7500"),
        (b_headers, "27.7500", "22.5000"),
    ):
        rows = _by_country((await client.get(RATES, headers=headers)).json())
        assert rows["FR"]["rate_eur_per_1000l"] == mine
        assert rows["FR"]["rate_eur_per_1000l"] != theirs
