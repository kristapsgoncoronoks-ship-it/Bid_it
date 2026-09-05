"""WO-AC — the refund-estimate funnel over HTTP (G4.8, R43).

WHAT THESE TESTS ARE FOR
-------------------------
The service tests prove the arithmetic and the write-free property. These prove
the three things that only exist at the route:

1. **It requires authentication.** That is a DECISION (`DECISIONS-NEEDED.md`
   §17, owner-confirmed 2026-09-05), not an accident: an anonymous `/estimate`
   would be the first route in this system where an unauthenticated stranger
   makes the server parse bytes they supply.

   Worth being precise about WHICH mechanism does what, because seeding the
   violations showed they are three different things and it is easy to credit
   the wrong one. The 401 below comes from the `CurrentUser` dependency —
   authentication. `require_perm(VAT_READ)` on the router is authorization, and
   removing it does NOT change this test's answer. And `authz`'s public
   allowlist is neither: it is the DECLARATION that a route may be public, and
   `test_authz_coverage.py` is what fails if this route ever appears there or
   loses its declared permission. That test, not this one, is the guard on
   publicness; this one pins the observable behaviour an anonymous caller gets.
2. **The security gate runs before any parser sees the bytes.** A preview file
   is not more trusted for being a preview.
3. **The response carries the R53 caveat**, so a client cannot render the
   number without the framing that rule forbids flattening.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.services import modules
from tests.factories.transport import synthetic_eurowag_statement

V = "/api/v1"


async def _register_org(client: AsyncClient):
    suffix = uuid.uuid4().hex[:8]
    r = await client.post(
        f"{V}/auth/register",
        json={
            "organization_name": f"WO-AC Org {suffix}",
            "name": "Owner",
            "email": f"owner-{suffix}@woac.example.io",
            "password": "supersecret",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['token']['access_token']}"}, body["organization"]["id"]


async def _enable_transport(db_session, org_id: str) -> None:
    # Direct service enablement: `transport` is deliberately in no PLANS set,
    # so the HTTP module toggle would 402 (the WO-76/WO-77 recorded rationale).
    # Setup only — every path under test stays HTTP.
    await modules.set_enabled(db_session, org_id, "transport", True)
    await db_session.commit()


def _statement() -> bytes:
    return synthetic_eurowag_statement(
        rows=[
            {
                "txn_date": "2026-06-03",
                "txn_time": "08:12",
                "vehicle_ref": "LV-1234",
                "station": "Demo Fuel Hub",
                "country": "BE",
                "product": "DIESEL",
                "qty": "100.00",
                "currency": "EUR",
                "net_local": "5000.00",
                "vat_local": "1050.00",
                "gross_local": "6050.00",
                "invoice_ref": "INV-0001",
            }
        ]
    ).encode()


@pytest.mark.asyncio
async def test_wo_ac_estimate_requires_authentication(client: AsyncClient):
    """The owner decision, pinned as observable behaviour: a caller with no
    credential gets 401, not a refund estimate.

    This asserts what an anonymous request RECEIVES. The structural guard on
    whether the route is allowed to be public at all lives in
    `test_authz_coverage.py` — see this module's docstring for why the two are
    not the same test and why conflating them would leave a real gap."""
    r = await client.post(
        f"{V}/transport/estimate",
        data={"period": "2026-Q2"},
        files={"file": ("statement.csv", _statement(), "text/csv")},
    )
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_wo_ac_estimate_returns_the_opportunity_with_its_caveat(
    client: AsyncClient, db_session
):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    r = await client.post(
        f"{V}/transport/estimate",
        headers=headers,
        data={"period": "2026-Q2"},
        files={"file": ("statement.csv", _statement(), "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["network"] == "Eurowag"
    assert body["lines"] == 1
    assert body["recoverable_eur"] == "1050.00"
    assert [c["country"] for c in body["countries"]] == ["BE"]
    # €1050 clears the €400 quarterly threshold.
    assert body["countries"][0]["below_minimum"] is False
    assert body["countries"][0]["threshold_currency"] == "EUR"
    # R53: the framing rides the number.
    assert "never a filed figure" in body["caveat"]


@pytest.mark.asyncio
async def test_wo_ac_a_non_csv_is_refused_before_any_parser_runs(client: AsyncClient, db_session):
    """The same CSV-only allow-list the real statement route states its reason
    for. A preview file is not more trusted for being a preview."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    r = await client.post(
        f"{V}/transport/estimate",
        headers=headers,
        data={"period": "2026-Q2"},
        files={"file": ("statement.exe", b"MZ\x90\x00binary", "application/octet-stream")},
    )
    assert r.status_code == 415, r.text


@pytest.mark.asyncio
async def test_wo_ac_a_malformed_period_is_a_422_with_the_service_code(
    client: AsyncClient, db_session
):
    """The refusal travels as the SERVICE's own `{"detail", "code"}` — this
    route maps nothing, so the wire vocabulary cannot drift from the service
    layer (§4.20)."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)

    r = await client.post(
        f"{V}/transport/estimate",
        headers=headers,
        data={"period": "2026-06"},
        files={"file": ("statement.csv", _statement(), "text/csv")},
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "invalid_period"


@pytest.mark.asyncio
async def test_wo_ac_the_module_gate_answers_before_the_file_is_parsed(
    client: AsyncClient, db_session
):
    """Transport off: the funnel refuses like every other transport entry
    point, rather than quietly working for a workspace that has not bought the
    vertical."""
    headers, _org_id = await _register_org(client)

    r = await client.post(
        f"{V}/transport/estimate",
        headers=headers,
        data={"period": "2026-Q2"},
        files={"file": ("statement.csv", _statement(), "text/csv")},
    )
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "module_not_enabled"
