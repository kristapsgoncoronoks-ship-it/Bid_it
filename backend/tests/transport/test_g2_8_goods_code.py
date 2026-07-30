"""G2.8 — Art. 9 expenditure (goods) codes (`app.services.transport.
goods_code`), `BA_fleet_fuel.md` A6/R11. See that module's docstring for the
verbatim harvested mapping and why every `PRODUCT_GROUPS` value is listed
explicitly rather than left to the default.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.transport.fuel_transaction import PRODUCT_GROUPS
from app.services.transport import claim as claim_svc
from app.services.transport import claim_lines, fuel_ingest, goods_code
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org


@pytest.mark.parametrize(
    "product_group,expected_code",
    [
        ("Diesel", "1"),
        ("HVO", "1"),
        ("Promo adj", "1"),
        ("AdBlue", "10"),
        ("Toll/Fees", "4"),
        ("Parking", "10"),
        ("Service/Other", "10"),
    ],
)
def test_g2_8_derive_goods_code_matches_the_harvested_mapping(product_group, expected_code):
    assert goods_code.derive_goods_code(product_group) == expected_code


def test_g2_8_every_product_group_is_explicitly_mapped_not_left_to_the_default():
    """Assert the absence, not only the presence: every one of today's 7
    `PRODUCT_GROUPS` values has its OWN row in `GOODS_CODE` — none of them
    is silently falling through to the `.get(..., default)` branch."""
    assert set(PRODUCT_GROUPS) <= set(goods_code.GOODS_CODE)


def test_g2_8_no_mapped_code_is_ever_9():
    """R11's central rule — assert the absence of '9' across every entry,
    not just spot-check the ones we expect to be safe."""
    codes = {code for code, _desc in goods_code.GOODS_CODE.values()}
    assert "9" not in codes


def test_g2_8_an_unrecognised_product_group_defaults_to_10_never_9():
    for bogus in ("Petrol", "", "Unknown Group", "9", "Diesel "):
        assert goods_code.derive_goods_code(bogus) == "10"


def test_g2_8_goods_code_and_description_returns_the_full_pair():
    code, desc = goods_code.goods_code_and_description("Toll/Fees")
    assert code == "4"
    assert desc == "Road tolls and road user charges"

    code, desc = goods_code.goods_code_and_description("a-made-up-group")
    assert code == "10"
    assert desc == "Other"


async def _make_claim(db_session, org, entity, **overrides):
    kwargs = dict(refund_country="LV", ref_period="2026-Q2")
    kwargs.update(overrides)
    return await claim_svc.get_or_create_claim(db_session, org.id, entity_id=entity.id, **kwargs)


async def _make_txn(db_session, org, entity, *, product, line_seq):
    return await fuel_ingest.ingest_transaction(
        db_session,
        org.id,
        entity_id=entity.id,
        supplier="Q8",
        period="2026-05",
        line_seq=line_seq,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=date(2026, 5, 15),
        station="Demo Station Riga",
        product=product,
        qty=Decimal("10.000"),
        currency="EUR",
        net_local=Decimal("10.00"),
        vat_local=Decimal("2.10"),
        gross_local=Decimal("12.10"),
        net_eur=Decimal("10.00"),
        vat_eur=Decimal("2.10"),
        invoice_ref=None,  # UNMATCHED — goods_code derivation doesn't care
    )


@pytest.mark.asyncio
async def test_g2_8_build_claim_lines_populates_goods_code_per_product_group(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    await _make_txn(db_session, org, entity, product="DIESEL", line_seq=1)
    await _make_txn(db_session, org, entity, product="TOLL ROAD", line_seq=2)
    await _make_txn(db_session, org, entity, product="AD BLUE", line_seq=3)
    await db_session.commit()

    lines = await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    by_group = {line.product_group: line.goods_code for line in lines}

    assert by_group["Diesel"] == "1"
    assert by_group["Toll/Fees"] == "4"
    assert by_group["AdBlue"] == "10"


@pytest.mark.asyncio
async def test_g2_8_no_claim_line_ever_persists_goods_code_9(db_session):
    """App-level proof alongside the WO-49 DB-level CHECK constraint
    (`ck_vat_claim_lines_goods_code_never_9`) — assert the absence at BOTH
    layers, not just one."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    claim = await _make_claim(db_session, org, entity)
    await db_session.commit()
    for i, product in enumerate(
        ["DIESEL", "HVO", "PROMO", "AD BLUE", "TOLL", "PARKING", "SOMETHING ELSE"], start=1
    ):
        await _make_txn(db_session, org, entity, product=product, line_seq=i)
    await db_session.commit()

    lines = await claim_lines.build_claim_lines(db_session, org.id, claim.id)
    assert lines  # sanity: something was built
    assert all(line.goods_code != "9" for line in lines)
    assert all(line.goods_code is not None for line in lines)
