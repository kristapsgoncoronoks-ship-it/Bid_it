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
    "EL": 9,  # Greece prints "EL", not "GR", on its VAT numbers (G3.2/WO-62)
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


_EUROWAG_COLUMNS = (
    "txn_date",
    "txn_time",
    "vehicle_ref",
    "station",
    "country",
    "product",
    "qty",
    "currency",
    "net_local",
    "vat_local",
    "gross_local",
    "invoice_ref",
)
_EUROWAG_MARKER = "EUROWAG STATEMENT"
_EUROWAG_HEADER = ",".join(_EUROWAG_COLUMNS)


def synthetic_eurowag_statement(
    *,
    rows: list[dict[str, object]] | None = None,
    footer_lines: list[str] | None = None,
    seed: int | None = None,
) -> str:
    """A synthetic Eurowag statement (G3.2/WO-62) matching the format
    `app.services.transport.parsers.eurowag.EurowagParser` expects — a
    literal `"EUROWAG STATEMENT"` marker, the transaction CSV block, a
    `"---"` separator, then the free-text footer the seller entity is
    anchored in. Synthetic, generated, never derived from client data.

    `rows`/`footer_lines` are overridable so a test can build a deliberately
    malformed row or a multi-entity / anti-example footer (e.g. a genuine
    seller line plus an unrelated factoring-entity disclosure sentence that
    must NOT be picked up) without hand-writing the whole file.
    """
    r = _rng(seed)
    if rows is None:
        litres = Decimal(r.randrange(5_000, 20_000)) / Decimal(100)
        price = Decimal(r.randrange(9_000, 16_000)) / Decimal(10_000)
        net = q2(litres * price)
        vat_amt = q2(net * Decimal("0.21"))
        gross = q2(net + vat_amt)
        rows = [
            {
                "txn_date": "2026-06-03",
                "txn_time": "08:12",
                "vehicle_ref": synthetic_vehicle_ref(seed=seed),
                "station": "Demo Fuel Hub",
                "country": "BE",
                "product": "DIESEL",
                "qty": str(litres),
                "currency": "EUR",
                "net_local": str(net),
                "vat_local": str(vat_amt),
                "gross_local": str(gross),
                "invoice_ref": synthetic_invoice_number("fuelcard-a", seed=seed),
            }
        ]
    if footer_lines is None:
        vat = synthetic_vat_id("BE", seed=seed)
        footer_lines = [
            f"Pārdevējs / Verkoper: Eurowag Belgium BVBA, 1 Demo Street, Brussels, "
            f"PVN reg. Nr.: {vat}"
        ]

    lines = [_EUROWAG_MARKER, _EUROWAG_HEADER]
    for row in rows:
        lines.append(",".join(str(row.get(col, "")) for col in _EUROWAG_COLUMNS))
    lines.append("---")
    lines.extend(footer_lines)
    return "\n".join(lines) + "\n"


_E100_COLUMNS = (
    "txn_date",
    "txn_time",
    "vehicle_ref",
    "station",
    "country",
    "product",
    "qty",
    "currency",
    "gross_local",
    "vat_rate",
    "invoice_ref",
)
_E100_MARKER = "E100 STATEMENT"
_E100_HEADER = ",".join(_E100_COLUMNS)


def synthetic_e100_statement(
    *,
    rows: list[dict[str, object]] | None = None,
    footer_lines: list[str] | None = None,
    seed: int | None = None,
) -> str:
    """A synthetic E100 statement (G3.2/WO-63) matching the format
    `app.services.transport.parsers.e100.E100Parser` expects — a literal
    `"E100 STATEMENT"` marker, the transaction CSV block (VAT-INCLUSIVE
    `gross_local` + `vat_rate`, NOT independently-given net/vat the way
    Eurowag's CSV supplies them), a `"---"` separator, then the free-text
    footer the seller entity is anchored in (the literal
    `"E100 International Trade"` marker string). Synthetic, generated,
    never derived from client data.

    `rows`/`footer_lines` are overridable so a test can build a deliberately
    malformed row or a multi-entity / anti-example footer (e.g. a genuine
    E100 marker line plus an unrelated buyer-VAT annexe line that must NOT
    be picked up) without hand-writing the whole file.
    """
    r = _rng(seed)
    if rows is None:
        litres = Decimal(r.randrange(5_000, 20_000)) / Decimal(100)
        price = Decimal(r.randrange(9_000, 16_000)) / Decimal(10_000)
        net = q2(litres * price)
        vat_amt = q2(net * Decimal("0.21"))
        gross = q2(net + vat_amt)
        rows = [
            {
                "txn_date": "2026-06-03",
                "txn_time": "08:12",
                "vehicle_ref": synthetic_vehicle_ref(seed=seed),
                "station": "Demo Fuel Hub",
                "country": "PL",
                "product": "DIESEL",
                "qty": str(litres),
                "currency": "EUR",
                "gross_local": str(gross),
                "vat_rate": "21.00",
                "invoice_ref": synthetic_invoice_number("fuelcard-a", seed=seed),
            }
        ]
    if footer_lines is None:
        vat = synthetic_vat_id("PL", seed=seed)
        footer_lines = [f"E100 International Trade sp. z o.o., VAT reg. No.: {vat}"]

    lines = [_E100_MARKER, _E100_HEADER]
    for row in rows:
        lines.append(",".join(str(row.get(col, "")) for col in _E100_COLUMNS))
    lines.append("---")
    lines.extend(footer_lines)
    return "\n".join(lines) + "\n"


# Q8's CSV shape is byte-for-byte the same column layout as Eurowag's
# (independently-given net_local/vat_local/gross_local — Q8 invoices at LIST
# price, not VAT-inclusive gross the way E100 does) — see
# `app.services.transport.parsers.q8`'s module docstring.
_Q8_COLUMNS = _EUROWAG_COLUMNS
_Q8_MARKER = "Q8 STATEMENT"
_Q8_HEADER = ",".join(_Q8_COLUMNS)


def synthetic_q8_statement(
    *,
    rows: list[dict[str, object]] | None = None,
    seed: int | None = None,
) -> str:
    """A synthetic Q8/Kuwait Petroleum statement (G3.2/WO-64) matching the
    format `app.services.transport.parsers.q8.Q8Parser` expects — a literal
    `"Q8 STATEMENT"` marker, then the transaction CSV block (independently-
    given `net_local`/`vat_local`/`gross_local`, same column shape as
    Eurowag's — Q8 invoices at LIST price, never VAT-inclusive gross).
    Synthetic, generated, never derived from client data.

    Unlike `synthetic_eurowag_statement`/`synthetic_e100_statement`, there is
    no `footer_lines` parameter — `Q8Parser` never attempts seller-entity
    detection (see that module's docstring), so no footer/entity fixture is
    needed. The default two-row fixture deliberately spans TWO different
    countries and currencies (EE/EUR and PL/PLN) — the "per-line country +
    currency" property this network's onboarding slice exists to prove, per
    `BA_fleet_fuel.md` §5.1's own network-table row for Q8.

    `rows` is overridable so a test can build a deliberately malformed row
    or a single-country fixture without hand-writing the whole file.
    """
    r = _rng(seed)
    if rows is None:
        litres_ee = Decimal(r.randrange(5_000, 20_000)) / Decimal(100)
        price_ee = Decimal(r.randrange(9_000, 16_000)) / Decimal(10_000)
        net_ee = q2(litres_ee * price_ee)
        vat_ee = q2(net_ee * Decimal("0.20"))
        gross_ee = q2(net_ee + vat_ee)

        litres_pl = Decimal(r.randrange(5_000, 20_000)) / Decimal(100)
        price_pl = Decimal(r.randrange(9_000, 16_000)) / Decimal(10_000)
        net_pl = q2(litres_pl * price_pl)
        vat_pl = q2(net_pl * Decimal("0.23"))
        gross_pl = q2(net_pl + vat_pl)

        rows = [
            {
                "txn_date": "2026-06-05",
                "txn_time": "07:45",
                "vehicle_ref": synthetic_vehicle_ref(seed=seed),
                "station": "Tallinn Ring Hub",
                "country": "EE",
                "product": "DIESEL",
                "qty": str(litres_ee),
                "currency": "EUR",
                "net_local": str(net_ee),
                "vat_local": str(vat_ee),
                "gross_local": str(gross_ee),
                "invoice_ref": synthetic_invoice_number("fuelcard-a", seed=seed),
            },
            {
                "txn_date": "2026-06-12",
                "txn_time": "14:20",
                "vehicle_ref": synthetic_vehicle_ref(seed=seed),
                "station": "Warsaw Bypass Hub",
                "country": "PL",
                "product": "DIESEL",
                "qty": str(litres_pl),
                "currency": "PLN",
                "net_local": str(net_pl),
                "vat_local": str(vat_pl),
                "gross_local": str(gross_pl),
                "invoice_ref": synthetic_invoice_number("fuelcard-b", seed=seed),
            },
        ]

    lines = [_Q8_MARKER, _Q8_HEADER]
    for row in rows:
        lines.append(",".join(str(row.get(col, "")) for col in _Q8_COLUMNS))
    lines.append("---")
    return "\n".join(lines) + "\n"


# DKV's CSV shape is Eurowag's column layout PLUS the one `net_eur` column —
# the supplier's own per-line EUR figure ("trusts the supplier's per-line EUR
# and pro-rates", `BA_fleet_fuel.md` §5.1) — see
# `app.services.transport.parsers.dkv`'s module docstring.
_DKV_COLUMNS = (
    "txn_date",
    "txn_time",
    "vehicle_ref",
    "station",
    "country",
    "product",
    "qty",
    "currency",
    "net_local",
    "vat_local",
    "gross_local",
    "net_eur",
    "invoice_ref",
)
_DKV_MARKER = "DKV STATEMENT"
_DKV_HEADER = ",".join(_DKV_COLUMNS)


def synthetic_dkv_statement(
    *,
    rows: list[dict[str, object]] | None = None,
    seed: int | None = None,
) -> str:
    """A synthetic DKV Euro Service statement (G3.2/WO-65) matching the
    format `app.services.transport.parsers.dkv.DKVParser` expects — a
    literal `"DKV STATEMENT"` marker, then the transaction CSV block
    (Eurowag's independently-given `net_local`/`vat_local`/`gross_local`
    columns PLUS the per-line `net_eur` column carrying the SUPPLIER'S OWN
    stated EUR figure). Synthetic, generated, never derived from client
    data.

    Like `synthetic_q8_statement`, there is no `footer_lines` parameter —
    `DKVParser` never attempts seller-entity detection. The default two-row
    fixture deliberately pairs a SE/SEK row (whose `net_eur` is the stated
    supplier conversion) with a DE/EUR row (whose `net_eur` trivially equals
    `net_local`) — the SEK/EUR currency mix `BA_fleet_fuel.md` §5.1's own
    network-table row gives for DKV, so every test that does not override
    `rows` exercises BOTH the stated and the identity conversion paths.

    `rows` is overridable so a test can build a deliberately malformed or
    inconsistent row without hand-writing the whole file.
    """
    r = _rng(seed)
    if rows is None:
        litres_se = Decimal(r.randrange(5_000, 20_000)) / Decimal(100)
        price_se = Decimal(r.randrange(9_000, 16_000)) / Decimal(1_000)  # SEK/L ~9-16
        net_se = q2(litres_se * price_se)
        vat_se = q2(net_se * Decimal("0.25"))
        gross_se = q2(net_se + vat_se)
        # The supplier's own stated EUR conversion — a plausible ~11 SEK/EUR
        # basis, deliberately NOT any rate a test could have cached.
        stated_eur_se = q2(net_se / Decimal("11.05"))

        litres_de = Decimal(r.randrange(5_000, 20_000)) / Decimal(100)
        price_de = Decimal(r.randrange(9_000, 16_000)) / Decimal(10_000)
        net_de = q2(litres_de * price_de)
        vat_de = q2(net_de * Decimal("0.19"))
        gross_de = q2(net_de + vat_de)

        rows = [
            {
                "txn_date": "2026-06-04",
                "txn_time": "06:55",
                "vehicle_ref": synthetic_vehicle_ref(seed=seed),
                "station": "Stockholm South Hub",
                "country": "SE",
                "product": "DIESEL",
                "qty": str(litres_se),
                "currency": "SEK",
                "net_local": str(net_se),
                "vat_local": str(vat_se),
                "gross_local": str(gross_se),
                "net_eur": str(stated_eur_se),
                "invoice_ref": synthetic_invoice_number("fuelcard-a", seed=seed),
            },
            {
                "txn_date": "2026-06-18",
                "txn_time": "17:40",
                "vehicle_ref": synthetic_vehicle_ref(seed=seed),
                "station": "Hamburg Port Hub",
                "country": "DE",
                "product": "DIESEL",
                "qty": str(litres_de),
                "currency": "EUR",
                "net_local": str(net_de),
                "vat_local": str(vat_de),
                "gross_local": str(gross_de),
                "net_eur": str(net_de),
                "invoice_ref": synthetic_invoice_number("fuelcard-b", seed=seed),
            },
        ]

    lines = [_DKV_MARKER, _DKV_HEADER]
    for row in rows:
        lines.append(",".join(str(row.get(col, "")) for col in _DKV_COLUMNS))
    lines.append("---")
    return "\n".join(lines) + "\n"


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
