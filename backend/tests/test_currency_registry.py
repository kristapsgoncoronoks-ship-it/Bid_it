"""One currency registry (C1.5, WO-23): `services/currencies.py` (the tenant
catalogue) and `services/fx.py` (the FX module) must resolve currency identity
(name/symbol/decimal_places/indicative) through the SAME registry — proven by
a parity check, a drift self-test, and a cross-check that `/currencies` and
`/fx/currencies` agree on `indicative` for the same code."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.organization import Organization
from app.services import currencies, fx


async def _org(db):
    return await db.scalar(select(Organization.id))


# --------------------------------------------------------------------------- #
# One registry — parity + drift detection
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_seed_standard_matches_fx_registry(auth_client, db_session):
    """Every seeded code's persisted name/symbol/decimal_places equals the
    shared registry's entry exactly — proving the seed no longer keeps its
    own hand-typed copy (the C1.5 defect)."""
    org_id = await _org(db_session)
    await currencies.seed_standard(db_session, org_id)
    for code in currencies._STANDARD_CODES:
        meta = fx.CURRENCY_BY_CODE[code]
        row = await currencies.resolve(db_session, org_id, code)
        assert row is not None, code
        assert row.name == meta.name, code
        assert row.symbol == meta.symbol, code
        assert row.decimal_places == meta.decimal_places, code


def test_no_duplicate_codes_in_registry():
    """A drift self-test that can actually fail (template rule 6): the real
    registry has no duplicate codes, but a synthetic duplicate IS detected."""
    codes = [c.code for c in fx.ALL_CURRENCIES]
    assert len(codes) == len(set(codes)), "ALL_CURRENCIES has a duplicate code"

    # Seed a violation and prove the same check catches it.
    tampered = (*fx.ALL_CURRENCIES, replace(fx.ALL_CURRENCIES[0]))
    tampered_codes = [c.code for c in tampered]
    assert len(tampered_codes) != len(set(tampered_codes)), (
        "the drift check must be able to fail on a seeded duplicate"
    )


def test_no_third_registry():
    """`services/currencies.py` no longer hand-copies currency metadata — the
    old `_STANDARD` tuple and a name string it used to carry are both gone."""
    src = Path(__file__).resolve().parents[1] / "app" / "services" / "currencies.py"
    text = src.read_text()
    assert "_STANDARD:" not in text, "the old hand-maintained tuple must be removed"
    assert "Pound Sterling" not in text, "currency names must come from fx.CURRENCY_BY_CODE only"
    assert "fx.CURRENCY_BY_CODE" in text, "the seed must derive metadata from the shared registry"


# --------------------------------------------------------------------------- #
# `indicative` provenance
# --------------------------------------------------------------------------- #
def test_indicative_none_for_unknown_code():
    assert fx.indicative_for("XYZ") is None


def test_indicative_true_for_non_ecb_false_for_ecb():
    assert fx.indicative_for("PLN") is False
    assert fx.indicative_for("RSD") is True
    # Case-insensitive, mirroring every other fx.py lookup.
    assert fx.indicative_for("rsd") is True


@pytest.mark.asyncio
async def test_tenant_currency_endpoint_surfaces_indicative(auth_client, db_session):
    org_id = await _org(db_session)
    await currencies.seed_standard(db_session, org_id)
    await db_session.commit()

    made = await auth_client.post("/api/v1/currencies", json={"code": "RSD", "name": "Dinar"})
    assert made.status_code == 201, made.text
    made_unknown = await auth_client.post(
        "/api/v1/currencies", json={"code": "XYZ", "name": "Made Up"}
    )
    assert made_unknown.status_code == 201, made_unknown.text

    body = (await auth_client.get("/api/v1/currencies")).json()
    by = {c["code"]: c for c in body}
    assert by["EUR"]["indicative"] is False
    assert by["RSD"]["indicative"] is True
    assert by["XYZ"]["indicative"] is None


@pytest.mark.asyncio
async def test_indicative_agrees_across_currency_and_fx_endpoints(auth_client, db_session):
    """The C1.5 acceptance cross-check: one fact (is PLN's rate ECB-official),
    read via two different endpoints, agrees."""
    org_id = await _org(db_session)
    await currencies.create(db_session, org_id, code="PLN", name="Zloty")
    await db_session.commit()

    tenant_body = (await auth_client.get("/api/v1/currencies")).json()
    tenant_by = {c["code"]: c for c in tenant_body}

    fx_body = (await auth_client.get("/api/v1/fx/currencies")).json()
    fx_by = {c["code"]: c for c in fx_body["currencies"]}

    assert tenant_by["PLN"]["indicative"] == fx_by["PLN"]["indicative"] is False


# --------------------------------------------------------------------------- #
# Behaviour-preserving: existing role/tenant gates untouched by the response
# shape change (belt-and-braces — the originals in test_currencies.py already
# assert this unmodified; this is the same shape proven through the registry
# lens for completeness).
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_global_majors_not_leaked_into_european_endpoint():
    """GLOBAL_MAJORS (USD/JPY/CAD/AUD) must not silently widen the
    Europe-scoped `/fx/currencies` catalogue — they live only in
    `ALL_CURRENCIES`, the tenant-seed registry."""
    european_codes = {c.code for c in fx.EUROPEAN_CURRENCIES}
    for code in ("USD", "JPY", "CAD", "AUD"):
        assert code not in european_codes
        assert code in fx.CURRENCY_BY_CODE
