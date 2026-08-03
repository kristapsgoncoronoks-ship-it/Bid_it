"""The TFC by Moya fuel-card statement parser (G3.2 slice 5;
`BA_fleet_fuel.md` §5.1). The FIFTH network registered into WO-62's
`fuel_card_parser` registry — see
`docs/plan/plan-a/wo/WO-67-G3.2-slice5.md`.

WHY TFC IS THE FIFTH NETWORK
---------------------------------
WO-66's own follow-up list and `TODO.md`'s WO-66 entry both name TFC by
Moya as the recommended next slice, in the priority order WO-62 first set
out and every slice since reaffirmed ("TFC by Moya, Moeve, BP/Aral").
`BA_fleet_fuel.md` §5.1's TFC row: *"UAB «Client-LT-3»; EUR; monthly;
−0.205/L only at TFC hubs (Meer −0.19); third-party stations
undiscounted. Flat 21% VAT."* It is also the first network onboarded
against the LIVE G3.3 validation regimes (WO-66) — its ingest suite proves
the €0.02/€0.03 coversheet tie-out over this parser's DERIVED figures.

THE HUB-ONLY DISCOUNT MONEY MODEL — THE REASON THIS SLICE EXISTS
------------------------------------------------------------------------
A FOURTH distinct money model, and the one WO-62's slice list said "needs
a station-classification concept": where Eurowag/Q8 read three
independently-given figures, E100 reverse-calculates from a VAT-inclusive
gross, and DKV trusts a supplier-stated EUR, a TFC statement prices each
line as litres × a LIST price — and the invoiced net is what remains
after the per-litre, station-classified hub discount:

    discount   = 0.205 EUR/L  (station_class = hub)
               | 0.19  EUR/L  (station_class = meer — the Meer hub's own
                               harvested tier)
               | 0     EUR/L  (station_class = third_party)
    net_local  = qty * (list_price_eur_l - discount)
    vat_local  = net_local * 21 / 100        (flat 21% — harvested)
    gross_local = net_local + vat_local

All pure `Decimal`, UNROUNDED in the returned `ParsedFuelLine` —
`fuel_ingest.ingest_transaction` stays the ONE place that quantizes
(WO-50's convention, unchanged). The discount is ON-INVOICE
(`BA_fleet_fuel.md` §4.2: "on-invoice discounts (TFC hub discount
−0.205/L, ...) are already inside `net_eur`") — deriving it here IS how
it gets inside the invoiced figures; nothing off-invoice is computed and
`net_eur_eff` stays at ingestion's default (= `net_eur`).

Deliberately parser-LOCAL: every derived figure rides the EXISTING
`ParsedFuelLine` fields and every line is EUR, so ingestion's untouched
identity branch applies. WO-65 established that DKV was the LAST network
whose model touches the shared `ParsedFuelLine`/`statement_ingest`
contract — this module changes neither, by design.

WHAT IS VERBATIM FROM THE SOURCE, AND WHAT IS A DOCUMENTED INTERPRETATION
---------------------------------------------------------------------------
VERBATIM: the tier constants (−0.205/L at TFC hubs, −0.19/L at Meer, 0 at
third-party stations), the flat 21% VAT rate, the EUR currency, and the
fact the discount is ON-INVOICE (§4.2, §5.1).

A DOCUMENTED INTERPRETATION (no worked TFC column layout exists in the
harvested spec — the same flagging discipline as every prior parser's
shape choice): a `"TFC STATEMENT"` marker header line, then a transaction
CSV block (`txn_date,txn_time,vehicle_ref,station,station_class,country,
product,qty,currency,list_price_eur_l,invoice_ref`), optionally followed
by a `"---"` separator. The statement prints the LIST unit price plus a
per-line station classification, and this parser derives the invoiced
figures from them; the per-litre discount applies to the line's printed
`qty` (the spec's own "/L" basis) with no product carve-out, since the
harvested row states none.

FAIL-CLOSED REFUSALS (each aborts the WHOLE statement)
------------------------------------------------------------------------
- An UNKNOWN `station_class` value: a guessed tier is an invented
  discount — the exact failure mode `fuel_card_parser.select()`'s
  fail-closed network detection exists to prevent, one level down.
- A NON-EUR row: the tier constants are EUR-per-litre; applying them to a
  list price denominated in another currency would be arithmetically
  wrong, and §5.1 gives TFC as an EUR network. (E100's parser does not
  enforce its network currency because its money model never depends on
  it; here the constant's unit makes the check load-bearing.)
- A NON-POSITIVE discounted unit price (`list_price_eur_l - discount <=
  0`): a hub row with a list price at or below the tier constant is a
  mis-keyed price. Refusing HERE names the actual defect; letting it flow
  would surface downstream as a review-gate `net_not_positive` over a
  figure the operator never typed.

WHY TFC GETS NO SELLER-ENTITY DETECTION (LIKE Q8/DKV, UNLIKE EUROWAG/E100)
------------------------------------------------------------------------
R20's worked examples in `BA_fleet_fuel.md` §3.B1 are Eurowag and E100
only; no footer label or anchor marker is given anywhere for TFC.
Inventing a plausible-looking "TFC by Moya" marker regex would violate
R21's "marker-only, no fuzzy auto-pairing" discipline at its root —
identical reasoning to `q8.py`/`dkv.py`'s own refusals. `parse()`
therefore NEVER attempts entity detection: `entities` is unconditionally
`[]` with one explanatory warning whose wording says detection was never
attempted. The TFC entity, if ever needed on a claim, is admin-curated
via `supplier_entity.set_registration`.

WHY A MALFORMED LINE ABORTS THE WHOLE STATEMENT, NOT JUST THAT LINE
------------------------------------------------------------------------
Unchanged discipline from `eurowag.py`/`e100.py`/`q8.py`/`dkv.py` — see
any of those modules' docstrings (never silently under-report a period's
fuel spend). The unknown-tier / non-EUR / non-positive-price refusals
above are treated exactly as fatal as an unparseable decimal.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation

from app.services.transport.fuel_card_parser import (
    FuelCardParser,
    ParsedFuelLine,
    ParsedStatement,
)

MARKER = "TFC STATEMENT"
SEPARATOR = "---"

# Harvested tier constants (`BA_fleet_fuel.md` §5.1's TFC row) — EUR per
# litre, keyed by the normalized station classification. `third_party` is
# explicitly present (0) so an unknown class is distinguishable from an
# undiscounted one: absence from this table REFUSES, it never means "no
# discount".
DISCOUNT_EUR_L: dict[str, Decimal] = {
    "hub": Decimal("0.205"),
    "meer": Decimal("0.19"),
    "third_party": Decimal("0"),
}

# Harvested: "Flat 21% VAT" — the ONE rate for every TFC line.
FLAT_VAT_PCT = Decimal("21")

_REQUIRED_COLUMNS = (
    "txn_date",
    "txn_time",
    "vehicle_ref",
    "station",
    "station_class",
    "country",
    "product",
    "qty",
    "currency",
    "list_price_eur_l",
    "invoice_ref",
)

# See module docstring "WHY TFC GETS NO SELLER-ENTITY DETECTION" — this is
# the ONE warning surfaced every parse, distinguishing "never attempted"
# from `eurowag.py`/`e100.py`'s "attempted, none found in this document".
_NO_ENTITY_DETECTION_WARNING = (
    "TFC seller-entity detection is not implemented for this network (no R20 "
    "worked marker documented in BA_fleet_fuel.md, unlike Eurowag/E100) — "
    "register the entity via admin curation "
    "(supplier_entity.set_registration) if needed on a claim."
)


def _decimal(raw: str | None, field: str, row_num: int) -> Decimal:
    value = (raw or "").strip()
    if not value:
        raise ValueError(f"row {row_num}: missing {field}")
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ValueError(f"row {row_num}: invalid decimal for {field}: {value!r}") from None


def _line_from_row(row: dict[str, str], line_seq: int) -> ParsedFuelLine:
    try:
        txn_date = date.fromisoformat((row.get("txn_date") or "").strip())
    except ValueError:
        raise ValueError(f"row {line_seq}: invalid or missing txn_date") from None

    country = (row.get("country") or "").strip().upper()
    if len(country) != 2 or not country.isalpha():
        raise ValueError(f"row {line_seq}: invalid country code {country!r}")

    currency = (row.get("currency") or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError(f"row {line_seq}: invalid currency code {currency!r}")
    if currency != "EUR":
        # The tier constants are EUR/L — see module docstring "FAIL-CLOSED
        # REFUSALS". TFC is an EUR network (`BA_fleet_fuel.md` §5.1).
        raise ValueError(
            f"row {line_seq}: TFC is an EUR network — the per-litre hub-discount "
            f"constants are EUR/L and cannot be applied to a {currency} list price"
        )

    vehicle_ref = (row.get("vehicle_ref") or "").strip()
    station = (row.get("station") or "").strip()
    product = (row.get("product") or "").strip()
    if not vehicle_ref or not station or not product:
        raise ValueError(f"row {line_seq}: vehicle_ref/station/product must not be blank")

    station_class = (row.get("station_class") or "").strip().lower()
    if station_class not in DISCOUNT_EUR_L:
        # Never guess a discount tier — see module docstring.
        known = "/".join(sorted(DISCOUNT_EUR_L))
        raise ValueError(
            f"row {line_seq}: unknown station_class {station_class!r} "
            f"(expected one of: {known}) — a discount tier is never guessed"
        )
    discount = DISCOUNT_EUR_L[station_class]

    qty = _decimal(row.get("qty"), "qty", line_seq)
    list_price = _decimal(row.get("list_price_eur_l"), "list_price_eur_l", line_seq)

    unit_price = list_price - discount
    if unit_price <= 0:
        raise ValueError(
            f"row {line_seq}: discounted unit price {unit_price} is not positive "
            f"(list_price_eur_l={list_price} minus the {station_class} tier's "
            f"{discount}/L discount) — the list price is mis-keyed"
        )

    # The derived-net hub-discount money model — see module docstring.
    # Pure Decimal arithmetic throughout; never routed through float().
    net_local = qty * unit_price
    vat_local = net_local * FLAT_VAT_PCT / Decimal(100)
    gross_local = net_local + vat_local

    return ParsedFuelLine(
        line_seq=line_seq,
        country=country,
        vehicle_ref=vehicle_ref,
        txn_date=txn_date,
        txn_time=(row.get("txn_time") or "").strip(),
        station=station,
        product=product,
        qty=qty,
        currency=currency,
        net_local=net_local,
        vat_local=vat_local,
        gross_local=gross_local,
        invoice_ref=(row.get("invoice_ref") or "").strip() or None,
        # The ONLY human-visible record of the derivation basis at the
        # review surface — mirrors `e100.py`'s reverse-calc note.
        provenance_note=(
            f"derived from list_price_eur_l={list_price} minus the {station_class} "
            f"tier's {discount}/L on-invoice discount x qty={qty} at flat "
            f"{FLAT_VAT_PCT}% VAT"
        ),
    )


class TFCParser(FuelCardParser):
    """Deterministic parser for a TFC by Moya statement export — see the
    module docstring for the exact format, the hub-only discount money
    model, the fail-closed refusals, and why no seller-entity detection is
    attempted."""

    network = "TFC"

    def handles(self, filename: str, content: bytes) -> bool:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return False
        lines = text.splitlines()
        if not lines or lines[0].strip() != MARKER:
            return False
        return any(line.startswith("txn_date,") for line in lines)

    def parse(self, filename: str, content: bytes) -> ParsedStatement:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"could not decode statement as UTF-8: {exc}") from None

        raw_lines = text.splitlines()
        if not raw_lines or raw_lines[0].strip() != MARKER:
            raise ValueError(f"missing the {MARKER!r} marker header line")

        try:
            header_idx = next(i for i, line in enumerate(raw_lines) if line.startswith("txn_date,"))
        except StopIteration:
            raise ValueError("statement is missing its transaction header row") from None

        try:
            sep_idx = next(
                i
                for i in range(header_idx + 1, len(raw_lines))
                if raw_lines[i].strip() == SEPARATOR
            )
        except StopIteration:
            sep_idx = len(raw_lines)

        csv_block = "\n".join(raw_lines[header_idx:sep_idx])
        reader = csv.DictReader(io.StringIO(csv_block))
        fieldnames = reader.fieldnames or []
        missing = [c for c in _REQUIRED_COLUMNS if c not in fieldnames]
        if missing:
            raise ValueError(f"statement CSV is missing required column(s): {missing}")

        lines: list[ParsedFuelLine] = []
        for row_num, row in enumerate(reader, start=1):
            # A row with the wrong number of columns puts `None` either in a
            # declared cell (too few) or under the `None` restkey (too many)
            # — never silently coerce, refuse the whole statement (mirrors
            # every prior network parser).
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"row {row_num}: wrong number of columns")
            lines.append(_line_from_row(row, row_num))

        if not lines:
            raise ValueError("statement carries no transaction rows")

        # No seller-entity detection is ever attempted — see module
        # docstring "WHY TFC GETS NO SELLER-ENTITY DETECTION".
        return ParsedStatement(
            network=self.network,
            lines=lines,
            entities=[],
            warnings=[_NO_ENTITY_DETECTION_WARNING],
        )
