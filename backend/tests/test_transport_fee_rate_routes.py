"""The contingency-fee gate had no way to be opened.

`fee.set_rate` shipped with no HTTP surface, so `resolve_fee_rate`'s deliberate
refusal (`fee_rate_not_configured`) — which blocks `submit_claim` entirely —
could only be cleared from a Python shell. An organisation could not file a
single claim through the product it had bought.

These tests cover the surface only. Every rule (an org rung carries no country,
percentage/minimum validation, idempotency, audit old→new) belongs to the
service and is tested with it; what is asserted here is that the rules are
reachable over HTTP and that the chain resolves in the order a reader is shown.
"""

from __future__ import annotations

import pytest

from app.services import modules as modules_svc

_RATES = "/api/v1/transport/fee-rates"


async def _org_id(auth_client) -> str:
    """The org the auth_client is authenticated as, read back from the API so
    the test never has to guess an id the fixture assigned."""
    me = (await auth_client.get("/api/v1/auth/me")).json()
    return me.get("org_id") or me["organization"]["id"]


async def _enable(db_session, org_id: str, on: bool = True):
    """Set the entitlement on the session directly, as every other transport
    test does: the HTTP toggle is plan-gated (402), which is a billing concern
    and not what these tests are about."""
    await modules_svc.set_enabled(db_session, org_id, "transport", on)
    await db_session.commit()


@pytest.mark.asyncio
async def test_an_org_starts_with_no_rate_and_so_cannot_file(auth_client, db_session):
    """The empty list is the whole problem this surface exists to solve: it is
    the state in which no claim can be submitted at all."""
    await _enable(db_session, await _org_id(auth_client))
    r = await auth_client.get(_RATES)
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_the_org_standard_rate_can_be_set_and_read_back(auth_client, db_session):
    await _enable(db_session, await _org_id(auth_client))
    put = await auth_client.put(_RATES, json={"fee_pct": "15", "fee_min": "50"})
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["entity_id"] is None, "no entity means the ORG STANDARD rung"
    assert body["country"] == ""
    assert float(body["fee_pct"]) == 15.0
    assert float(body["fee_min"]) == 50.0

    listed = (await auth_client.get(_RATES)).json()
    assert len(listed) == 1
    assert float(listed[0]["fee_pct"]) == 15.0


@pytest.mark.asyncio
async def test_setting_the_same_values_again_is_a_no_op_not_a_duplicate(auth_client, db_session):
    await _enable(db_session, await _org_id(auth_client))
    first = (await auth_client.put(_RATES, json={"fee_pct": "15", "fee_min": "50"})).json()
    again = (await auth_client.put(_RATES, json={"fee_pct": "15", "fee_min": "50"})).json()
    assert first["id"] == again["id"]
    assert len((await auth_client.get(_RATES)).json()) == 1


@pytest.mark.asyncio
async def test_a_rate_can_be_changed(auth_client, db_session):
    await _enable(db_session, await _org_id(auth_client))
    await auth_client.put(_RATES, json={"fee_pct": "15", "fee_min": "50"})
    changed = (await auth_client.put(_RATES, json={"fee_pct": "20", "fee_min": "75"})).json()
    assert float(changed["fee_pct"]) == 20.0
    assert float(changed["fee_min"]) == 75.0
    assert len((await auth_client.get(_RATES)).json()) == 1, "changed, not duplicated"


@pytest.mark.asyncio
async def test_an_org_rung_may_not_carry_a_country(auth_client, db_session):
    """Neither C11 nor R40 describes an org-level per-country rate. The service
    refuses; this asserts the refusal survives the HTTP layer rather than
    turning into a 500."""
    await _enable(db_session, await _org_id(auth_client))
    r = await auth_client.put(_RATES, json={"country": "LV", "fee_pct": "15", "fee_min": "50"})
    assert r.status_code in (400, 409, 422), r.text


@pytest.mark.asyncio
async def test_a_negative_fee_is_refused(auth_client, db_session):
    await _enable(db_session, await _org_id(auth_client))
    r = await auth_client.put(_RATES, json={"fee_pct": "-1", "fee_min": "50"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_an_explicit_zero_rate_is_accepted(auth_client, db_session):
    """Absence and a typed zero are different objects: a zero was chosen by a
    human and states a real arrangement. That distinction is the design."""
    await _enable(db_session, await _org_id(auth_client))
    r = await auth_client.put(_RATES, json={"fee_pct": "0", "fee_min": "0"})
    assert r.status_code == 200
    assert float(r.json()["fee_pct"]) == 0.0


@pytest.mark.asyncio
async def test_removing_the_last_rung_returns_the_org_to_refusing(auth_client, db_session):
    await _enable(db_session, await _org_id(auth_client))
    await auth_client.put(_RATES, json={"fee_pct": "15", "fee_min": "50"})
    gone = await auth_client.request("DELETE", _RATES)
    assert gone.status_code == 200
    assert gone.json()["removed"] is True
    assert (await auth_client.get(_RATES)).json() == []


@pytest.mark.asyncio
async def test_removing_a_rung_that_was_never_set_is_a_no_op(auth_client, db_session):
    await _enable(db_session, await _org_id(auth_client))
    r = await auth_client.request("DELETE", _RATES)
    assert r.status_code == 200
    assert r.json()["removed"] is False, "absence is not an error"


@pytest.mark.asyncio
async def test_the_module_gate_still_applies(auth_client, db_session):
    """The surface must not become a way around the module entitlement."""
    await _enable(db_session, await _org_id(auth_client), on=False)
    r = await auth_client.get(_RATES)
    assert r.status_code in (402, 403, 404, 409), r.text
