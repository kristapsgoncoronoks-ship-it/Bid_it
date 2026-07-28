"""G1.2 — `derive_product_group()` precedence (`BA_fleet_fuel.md` section 4.2
row 8, verbatim): "Precedence: PROMO -> HVO -> everything else -> Diesel LAST
so 'HVO 100' is never mis-grouped as diesel." A shared-usage/drift test per
WORK_ORDER_TEMPLATE §8 rule 5 (every predicate that must not drift gets a
shared-usage test) and rule 8 (assert the absence, not only the presence).
"""

from __future__ import annotations

import random

import pytest

from app.models.transport.fuel_transaction import PRODUCT_GROUPS
from app.services.transport.product_group import derive_product_group


@pytest.mark.parametrize(
    "product,expected",
    [
        # PROMO wins over everything, including an HVO or diesel token in
        # the SAME string.
        ("PROMO ADJUSTMENT", "Promo adj"),
        ("PROMO HVO CREDIT", "Promo adj"),
        ("DIESEL PROMO REBATE", "Promo adj"),
        # HVO wins over a diesel token in the same string — the exact case
        # the source text names: "HVO 100" is never mis-grouped as diesel.
        ("HVO 100", "HVO"),
        ("HVO DIESEL BLEND", "HVO"),
        # The "everything else" categories each resolve, and beat Diesel.
        ("ADBLUE 10L", "AdBlue"),
        ("AD BLUE CANISTER", "AdBlue"),
        ("PARKING FEE CITY CENTRE", "Parking"),
        ("ROAD TOLL BRIDGE", "Toll/Fees"),
        ("TOLL FEE MOTORWAY", "Toll/Fees"),
        # Every multilingual diesel token individually, verbatim from the
        # source list: DIESEL, ON ACT, GASOLEO, GASOIL, GAZOLE, "ON ".
        ("DIESEL", "Diesel"),
        ("GASOLIO DIESEL PREMIUM", "Diesel"),
        ("ON ACT", "Diesel"),
        ("GASOLEO A", "Diesel"),
        ("GASOIL ROUTIER", "Diesel"),
        ("GAZOLE B7", "Diesel"),
        ("ON", "Diesel"),  # the "ON " trailing-space token, as a whole word
        # A fully unrecognised product falls to the ultimate default,
        # Service/Other — NEVER Diesel, since product_group drives the Art. 9
        # goods code and an unclassifiable line must not silently imply
        # reclaimable fuel VAT (goods code "1") the way a Diesel default
        # would.
        ("MYSTERY PURCHASE XYZ", "Service/Other"),
        ("CAR WASH", "Service/Other"),
        ("", "Service/Other"),
    ],
)
def test_g1_2_product_group_precedence_matches_the_documented_order(product, expected):
    assert derive_product_group(product) == expected


def test_g1_2_product_group_is_case_insensitive():
    """Supplier product names arrive in mixed case and multiple languages —
    the derivation must not be case-sensitive."""
    assert derive_product_group("diesel") == "Diesel"
    assert derive_product_group("Hvo 100") == "HVO"
    assert derive_product_group("promo") == "Promo adj"


def test_g1_2_product_group_only_emits_the_seven_documented_categories():
    """Assert the absence, not only the presence (WORK_ORDER_TEMPLATE §8 rule
    8): over a large randomised sample of product strings — including ones
    built from every known token plus noise — every returned value is one of
    the exact 7 PRODUCT_GROUPS, never an 8th value."""
    assert len(PRODUCT_GROUPS) == 7
    tokens = [
        "PROMO",
        "HVO",
        "ADBLUE",
        "AD BLUE",
        "PARKING",
        "TOLL",
        "ROAD TOLL",
        "DIESEL",
        "ON ACT",
        "GASOLEO",
        "GASOIL",
        "GAZOLE",
        "ON",
        "RANDOM NOISE WORD",
        "",
    ]
    rng = random.Random(42)
    for _ in range(500):
        product = " ".join(rng.choices(tokens, k=rng.randrange(0, 4)))
        assert derive_product_group(product) in PRODUCT_GROUPS
