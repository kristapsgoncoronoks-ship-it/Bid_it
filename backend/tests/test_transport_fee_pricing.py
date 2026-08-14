"""Standard pricing, and a client negotiated off it.

The commercial model (owner decision, 2026-08-12): there is a STANDARD price and
an individual client is negotiated to a DISCOUNT from it. Four choices shape
what is asserted here:

* the negotiated price is stored ABSOLUTE, so raising the standard later cannot
  silently re-rate a client who has a signed rate card;
* the discount is therefore DERIVED for display, never a fact of record;
* a discount applies to BOTH the percentage and the minimum;
* a negotiated price may sit ABOVE standard, so the derived discount can be
  negative and `kind` says which way round it is.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services import modules as modules_svc
from app.services.transport import fee as fee_svc

_RATES = "/api/v1/transport/fee-rates"
_DISCOUNT = _RATES + "/discount"


async def _org_id(auth_client) -> str:
    me = (await auth_client.get("/api/v1/auth/me")).json()
    return me.get("org_id") or me["organization"]["id"]


async def _enable(db_session, org_id: str):
    await modules_svc.set_enabled(db_session, org_id, "transport", True)
    await db_session.commit()


# --- the pure arithmetic -------------------------------------------------------


def test_a_discount_applies_to_both_the_percentage_and_the_minimum():
    """20% off 15% / €50 is 12% / €40 — not 12% with the floor left alone."""
    pct, minimum = fee_svc.apply_discount(Decimal("15"), Decimal("50"), Decimal("20"))
    assert pct == Decimal("12.00")
    assert minimum == Decimal("40.00")


def test_a_negative_discount_is_a_premium():
    pct, minimum = fee_svc.apply_discount(Decimal("15"), Decimal("50"), Decimal("-10"))
    assert pct == Decimal("16.50")
    assert minimum == Decimal("55.00")


def test_a_discount_cannot_produce_a_rate_that_could_not_be_typed():
    """Arithmetic must not become a back door around the validators: a premium
    big enough to exceed 100% is refused exactly as typing 150% would be.

    The exception TYPE and CODE are asserted, not merely that something was
    raised — `pytest.raises(Exception)` would also pass on a typo in this test.
    """
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError) as e:
        fee_svc.apply_discount(Decimal("60"), Decimal("50"), Decimal("-100"))
    assert e.value.code == "invalid_fee_pct"


def test_a_full_discount_is_a_zero_price_not_an_error():
    pct, minimum = fee_svc.apply_discount(Decimal("15"), Decimal("50"), Decimal("100"))
    assert pct == Decimal("0.00") and minimum == Decimal("0.00")


# --- the derived comparison ----------------------------------------------------


class _Row:
    def __init__(self, pct, minimum):
        self.fee_pct = Decimal(pct)
        self.fee_min = Decimal(minimum)


def test_a_cheaper_client_reads_as_a_discount():
    c = fee_svc.compare_rate(Decimal("12"), Decimal("40"), _Row("15", "50"))
    assert c.kind == fee_svc.DISCOUNT
    assert c.pct_discount == Decimal("20.00")
    assert c.min_discount == Decimal("20.00")
    assert c.standard_pct == Decimal("15.00")


def test_a_dearer_client_reads_as_a_premium_with_a_negative_discount():
    c = fee_svc.compare_rate(Decimal("18"), Decimal("60"), _Row("15", "50"))
    assert c.kind == fee_svc.PREMIUM
    assert c.pct_discount == Decimal("-20.00")


def test_matching_the_standard_reads_as_standard():
    c = fee_svc.compare_rate(Decimal("15"), Decimal("50"), _Row("15", "50"))
    assert c.kind == fee_svc.STANDARD
    assert c.pct_discount == Decimal("0.00")


def test_with_no_standard_the_discount_is_unknown_not_zero():
    """"No discount" and "nothing to compare with" are different statements, and
    a zero would blur them."""
    c = fee_svc.compare_rate(Decimal("12"), Decimal("40"), None)
    assert c.kind == fee_svc.NO_STANDARD
    assert c.pct_discount is None and c.standard_pct is None


def test_a_zero_standard_gives_no_percentage_rather_than_a_made_up_one():
    """Nothing is a meaningful percentage of zero; 0 and 100 would both be
    inventions."""
    c = fee_svc.compare_rate(Decimal("5"), Decimal("10"), _Row("0", "0"))
    assert c.pct_discount is None and c.min_discount is None


# --- over HTTP -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_standard_is_reported_as_the_standard(auth_client, db_session):
    await _enable(db_session, await _org_id(auth_client))
    body = (await auth_client.put(_RATES, json={"fee_pct": "15", "fee_min": "50"})).json()
    assert body["kind"] == "standard"
    assert float(body["pct_discount"]) == 0.0


@pytest.mark.asyncio
async def test_a_discount_off_nothing_is_refused(auth_client, db_session):
    """The gate that matters commercially: a discount needs a base, and
    inventing one is the same invention `resolve_fee_rate` refuses to make."""
    await _enable(db_session, await _org_id(auth_client))
    r = await auth_client.put(_DISCOUNT, json={"entity_id": "someone", "discount_pct": "20"})
    assert r.status_code in (400, 409, 422), r.text
    assert "standard" in r.text.lower()


ISSUER = {
    "legal_name": "Baltic Haulage AS",
    "vat_number": "EE100200300",
    "registration_number": "EE-16000000",
    "address_line1": "Tartu mnt 10",
    "city": "Tallinn",
    "postal_code": "10145",
    "country": "EE",
}


async def _client_entity(auth_client) -> str:
    """A real issuer profile to hang a negotiated rate on — the customer IS the
    claimant entity (WO-73), so the rung needs a genuine id."""
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    return (await auth_client.post("/api/v1/issuer/registry", json=ISSUER)).json()["id"]


@pytest.mark.asyncio
async def test_a_client_can_be_negotiated_off_the_standard(auth_client, db_session):
    await _enable(db_session, await _org_id(auth_client))
    await auth_client.put(_RATES, json={"fee_pct": "15", "fee_min": "50"})
    entity = await _client_entity(auth_client)

    made = await auth_client.put(
        _DISCOUNT, json={"entity_id": entity, "discount_pct": "20"}
    )
    assert made.status_code == 200, made.text
    body = made.json()
    assert float(body["fee_pct"]) == 12.0, "20% off 15%"
    assert float(body["fee_min"]) == 40.0, "the discount applies to the minimum too"
    assert body["kind"] == "discount"
    assert float(body["pct_discount"]) == 20.0


@pytest.mark.asyncio
async def test_raising_the_standard_does_not_move_a_negotiated_client(
    auth_client, db_session
):
    """The whole reason the negotiated price is stored absolute. A client on a
    signed rate card must not be re-rated because we changed our list price.

    This drives a REAL client rung — an earlier draft only re-read the org rung,
    which would have passed no matter how the negotiated price was stored, and
    so proved nothing the name claimed.
    """
    await _enable(db_session, await _org_id(auth_client))
    await auth_client.put(_RATES, json={"fee_pct": "15", "fee_min": "50"})
    entity = await _client_entity(auth_client)
    await auth_client.put(_DISCOUNT, json={"entity_id": entity, "discount_pct": "20"})

    # The list price goes up.
    await auth_client.put(_RATES, json={"fee_pct": "18", "fee_min": "60"})

    rates = (await auth_client.get(_RATES)).json()
    client = [r for r in rates if r["entity_id"] == entity][0]
    assert float(client["fee_pct"]) == 12.0, "the negotiated rate followed the standard"
    assert float(client["fee_min"]) == 40.0

    # The DERIVED discount does move, and should: it is a comparison, not a term.
    assert client["kind"] == "discount"
    assert float(client["pct_discount"]) > 20.0, "12% is now further below 18%"
