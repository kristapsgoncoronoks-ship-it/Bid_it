"""Synthetic transport-vertical fixture generators (WO-6).

Everything in this module is **synthetic, generated, never derived from
client data**. The retired Fleet Fuel system contained real client
identifiers; the PII quarantine (scripts/pii_scan.py + the harvest protocol
in docs/transport/harvest-protocol.md) forbids any of them from entering
this repository. All Epic-G transport tests MUST take their fixtures from
here — values are realistic in *shape* (correct VAT prefix and digit count,
MOD-97-valid IBANs over a documented test-only bank code, plausible invoice
number formats and fuel quantities) and fictional in *content*.

Generators are deterministic given a ``seed`` so tests stay reproducible.
Generated VAT/IBAN values are intentionally NOT committed anywhere — a
structurally valid literal written into the tree would (correctly) trip the
PII scanner unless allow-listed.
"""

from __future__ import annotations

import random
from decimal import Decimal

from app.core.bank_id import is_valid_iban
from app.core.money import q2

# Digit count of the numeric VAT-id body per country (structural shape only;
# check-digit schemes are deliberately not implemented — these ids must never
# validate against a real registry).
VAT_DIGITS: dict[str, int] = {
    "EE": 9,
    "LT": 12,
    "LV": 11,
    "PL": 10,
    "CZ": 8,
    "DE": 9,
    "FI": 8,
    "SE": 12,
    "SI": 8,
    "HU": 8,
    "HR": 11,
    "BE": 10,
    "IT": 11,
    "FR": 11,
    "DK": 8,
}

# BBAN recipe per country: (prefix, filler_digit_count). The prefix is a
# documented TEST-ONLY bank code (all-nines numeric, or the literal "TEST"
# where the country's format demands letters) so a generated IBAN can never
# collide with a real account, while the computed check digits still pass
# ISO 7064 MOD-97 and app.core.bank_id.is_valid_iban.
_IBAN_RECIPES: dict[str, tuple[str, int]] = {
    "EE": ("99", 14),  # 2-digit bank code + 14 digits = 16-char BBAN
    "DE": ("99999999", 10),  # 8-digit bank code + 10-digit account
    "LT": ("99999", 11),  # 5-digit bank code + 11-digit account
    "FI": ("999999", 8),  # 6-digit bank code + 8 digits
    "LV": ("TEST", 13),  # 4-letter bank code + 13 alphanumerics (digits here)
    "BE": ("999", 9),  # 3-digit bank code + 7-digit account + 2-digit check
}

# Obviously fictional carrier/company names — never a real transport company.
_COMPANY_NAMES = [
    "Northwind Haulage OÜ",
    "Baltic Freight Demo UAB",
    "Contoso Cargo SIA",
    "Acme Axle Logistics OY",
    "Placeholder Transit Sp. z o.o.",
    "Fictional Fleet Lines s.r.o.",
    "Specimen Shipping GmbH",
    "Demo Diesel Distribution AS",
]

# Plausible fuel-card invoice-number *shapes* (prefix + zero-padded counter).
# The prefixes are generic demo mnemonics; no real supplier's live numbering
# is reproduced here (the source system is deleted — shape only, by design).
_INVOICE_FORMATS: dict[str, str] = {
    "fuelcard-a": "FCA-{n:07d}",
    "fuelcard-b": "FB/{n:08d}",
    "toll-operator": "TOLL-{n:06d}",
    "generic": "INV-{n:06d}",
}


def _rng(seed: int | None) -> random.Random:
    return random.Random(seed)


def synthetic_vat_id(country: str = "EE", *, seed: int | None = None) -> str:
    """A VAT id with a correct country prefix and digit count and a fictional
    numeric body. Synthetic, generated, never derived from client data.

    The body always starts with ``9`` — outside the allocation ranges the
    Baltic registries actually use — as an extra layer of "cannot be real".
    """
    country = country.upper()
    digits = VAT_DIGITS[country]
    r = _rng(seed)
    body = "9" + "".join(str(r.randrange(10)) for _ in range(digits - 1))
    return f"{country}{body}"


def synthetic_iban(country: str = "EE", *, seed: int | None = None) -> str:
    """An IBAN with valid MOD-97 check digits over a documented test-only
    bank code, so ``app.core.bank_id.is_valid_iban`` accepts it. Synthetic,
    generated, never derived from client data.
    """
    country = country.upper()
    prefix, fill = _IBAN_RECIPES[country]
    r = _rng(seed)
    bban = prefix + "".join(str(r.randrange(10)) for _ in range(fill))
    # ISO 7064: check digits make MOD-97 of the rearranged IBAN equal 1.
    rearranged = bban + country + "00"
    as_digits = "".join(str(int(ch, 36)) for ch in rearranged)
    check = 98 - int(as_digits) % 97
    iban = f"{country}{check:02d}{bban}"
    if not is_valid_iban(iban):  # pragma: no cover - guards recipe drift
        raise AssertionError(f"factory produced an invalid IBAN shape for {country}")
    return iban


def synthetic_company_name(*, seed: int | None = None) -> str:
    """A carrier name from an obviously fictional set. Synthetic, generated,
    never derived from client data."""
    return _rng(seed).choice(_COMPANY_NAMES)


_VEHICLE_REFS = [
    "Fleet-A-001",
    "Fleet-B-014",
    "Card-9921",
    "Card-4410",
    "Plate-DEMO-01",
    "Plate-DEMO-02",
]


def synthetic_vehicle_ref(*, seed: int | None = None) -> str:
    """A vehicle/card identity from an obviously fictional set (G1.2/WO-50).
    Synthetic, generated, never derived from client data."""
    return _rng(seed).choice(_VEHICLE_REFS)


def synthetic_invoice_number(kind: str = "generic", *, seed: int | None = None) -> str:
    """An invoice number matching a plausible fuel-card numbering *shape*
    without reusing any real number. Synthetic, generated, never derived
    from client data."""
    fmt = _INVOICE_FORMATS[kind]
    return fmt.format(n=_rng(seed).randrange(1, 10**6))


def synthetic_fuel_line(*, seed: int | None = None) -> dict[str, object]:
    """A fuel line item with plausible litres and NET EUR/L prices.

    Synthetic, generated, never derived from client data. ``litres`` is
    deliberately not money-quantized (it is the €/L denominator — R49);
    ``net_eur`` is Decimal ROUND_HALF_UP via ``app.core.money.q2``.
    """
    r = _rng(seed)
    litres = Decimal(r.randrange(20_000, 90_000)) / Decimal(100)  # 200.00–900.00 L
    price = Decimal(r.randrange(9_000, 16_000)) / Decimal(10_000)  # 0.9000–1.6000 €/L
    return {
        "product_code": "1",  # fuel — R11: never "9"
        "litres": litres,
        "unit_price_net_eur_l": price,
        "net_eur": q2(litres * price),
        "supply_country": r.choice(list(VAT_DIGITS)),
        "station": synthetic_company_name(seed=r.randrange(2**31)),
    }
