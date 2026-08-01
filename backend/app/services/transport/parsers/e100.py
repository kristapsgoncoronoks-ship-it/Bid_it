"""The E100 fuel-card statement parser (G3.2 slice 2; `BA_fleet_fuel.md`
§3.B1, §5.1). The second network registered into WO-62's
`fuel_card_parser` registry — see `docs/plan/plan-a/wo/WO-63-G3.2-slice2.md`.

WHY E100 IS THE SECOND NETWORK
---------------------------------
WO-62's own report and `TODO.md`'s WO-62 entry both name E100 as the
recommended next slice, and it is R20's OWN SECOND worked example
(`BA_fleet_fuel.md` §3.B1): *"`extract._e100_seller_name()` /
`_e100_seller_vat()` anchor seller name and VAT to the `"E100 International
Trade"` marker itself, because a generic seller/buyer heuristic was grabbing
the buyer's (client's) LV VAT id from the annexe pages where it repeats."*
It is also the first parser in this codebase to encounter a genuinely
different MONEY MODEL: unlike Eurowag (which supplies `net_local`/
`vat_local`/`gross_local` as three independently-given figures), E100's
invoices are VAT-INCLUSIVE GROSS — only the gross figure and a VAT rate are
given, and `net_local`/`vat_local` must be DERIVED by the reverse
calculation (see "THE REVERSE VAT-INCLUSIVE-GROSS CALCULATION" below). Every
subsequent VAT-inclusive network this codebase onboards (Moeve is next)
needs this exact code path to exist first.

WHAT IS VERBATIM FROM THE SOURCE, AND WHAT IS A DOCUMENTED INTERPRETATION
---------------------------------------------------------------------------
VERBATIM (do not edit without re-reading `BA_fleet_fuel.md` §3.B1): the
anchor discipline itself ("anchor to the marker", never a seller/buyer
position heuristic) and the marker string `"E100 International Trade"`.

A DOCUMENTED INTERPRETATION (the source gives the marker string but no
worked invoice-footer sentence for E100 the way it quotes Eurowag's
`"Pārdevējs / Verkoper:"` line verbatim, and no worked example of the exact
per-line transaction CSV column layout either): this parser reads a `"E100
STATEMENT"` marker header line, then a transaction CSV block (`txn_date,
txn_time,vehicle_ref,station,country,product,qty,currency,gross_local,
vat_rate,invoice_ref` — note NO `net_local`/`vat_local` columns, since E100
gives only the VAT-inclusive gross + rate) followed by a `"---"` separator
ahead of the free-text footer the seller entity is anchored in. This mirrors
the SHAPE choice `eurowag.py` already made and flags the same way (WO-62's
own docstring, WO-50's `line_seq` before it) — a small, obviously-
representative shape, never presented as harvested fact.

THE REVERSE VAT-INCLUSIVE-GROSS CALCULATION
------------------------------------------------
E100 supplies `gross_local` (VAT-inclusive) and a per-line `vat_rate`
(percentage, e.g. `"21.00"`) instead of independently-given `net_local`/
`vat_local`. Derive, per line, entirely in `Decimal`:

    net_local = gross_local / (1 + vat_rate / 100)
    vat_local = gross_local - net_local

Both results stay UNROUNDED Decimals in the returned `ParsedFuelLine` —
`fuel_ingest.ingest_transaction` remains the ONE place `q2` rounds (WO-50's
convention, unchanged). `vat_rate` is bounded to `[0, 100]` — a defensive,
conservative gate (no real EU VAT rate falls outside it) — and raises
`ValueError` outside that range, matching this order's "refuse rather than
guess on an inconsistent row" instruction. `gross_local` itself is not sign-
or range-restricted (mirrors Eurowag's own lack of a sign check — a credit/
correction line is not ruled out by either parser). `ParsedFuelLine.
provenance_note` records the reverse-calc basis (the `gross_local`/
`vat_rate` as read) — the ONLY place this derivation is visible for a human
reviewing a captured line, since G3.3's numeric tie-out does not exist yet.

ANCHOR TO THE MARKER STRING ITSELF — R20'S SECOND WORKED EXAMPLE
------------------------------------------------------------------------
`BA_fleet_fuel.md` §3.B1's own hazard: a generic seller/buyer heuristic was
grabbing the BUYER's VAT id from annexe pages where it repeats. This
parser's `_detect_entities` therefore inspects ONLY lines containing the
literal substring `"E100 International Trade"` — exactly the discipline
`EurowagParser._detect_entities` uses for its own label (never inspecting
unlabelled legal-form-shaped text elsewhere) — and extracts the VAT number
and entity name from THAT SAME line only. A buyer VAT number printed on an
unrelated annexe line (no marker present) must never enter
`parsed.entities`.

WHY A MALFORMED ROW ABORTS THE WHOLE STATEMENT
------------------------------------------------------------------------
Unchanged discipline from `eurowag.py` (see that module's docstring) — now
also covering an out-of-range `vat_rate`, treated exactly as fatal as an
unparseable decimal, never silently clamped.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from app.services.transport.fuel_card_parser import (
    DetectedEntity,
    FuelCardParser,
    ParsedFuelLine,
    ParsedStatement,
)

MARKER = "E100 STATEMENT"
SEPARATOR = "---"

_REQUIRED_COLUMNS = (
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

# Harvested verbatim, `BA_fleet_fuel.md` §3.B1 — see module docstring.
_E100_MARKER_LABEL = "E100 International Trade"
# Deliberately permissive on the VAT BODY (alphanumeric, 4-17 chars) — the
# country-code check happens separately in `_country_from_vat`, mirroring
# `eurowag.py`'s own `_EW_VAT_RE` reasoning.
_E100_VAT_RE = re.compile(r"VAT reg\.\s*No\.[^:]*:\s*([A-Za-z0-9]{4,17})")

# EU VAT numbers are prefixed with the ISO-3166 country code, with one
# documented exception (Greece prints "EL", not "GR") — same table
# `eurowag.py` uses; kept local rather than imported so each parser module
# stays a self-contained, independently-reviewable unit (mirrors the
# per-parser duplication `eurowag.py` already accepts for its own
# `_VAT_PREFIX_TO_COUNTRY`).
_VAT_PREFIX_TO_COUNTRY = {"EL": "GR"}


def _country_from_vat(vat_number: str) -> str | None:
    """The 2-letter ISO country from a VAT id's prefix, or `None` when the
    prefix isn't a plausible country code — never a guess."""
    prefix = vat_number[:2].upper()
    if len(prefix) != 2 or not prefix.isalpha():
        return None
    return _VAT_PREFIX_TO_COUNTRY.get(prefix, prefix)


def _detect_entities(text: str) -> tuple[list[DetectedEntity], list[str]]:
    """Anchor ONLY to lines carrying the literal `_E100_MARKER_LABEL` marker
    string (R20's second worked example — see module docstring). Any OTHER
    VAT-shaped text elsewhere in the document — e.g. a buyer VAT reference
    that repeats on every annexe page — is never even inspected, so it can
    never be picked up by accident."""
    entities: list[DetectedEntity] = []
    warnings: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if _E100_MARKER_LABEL not in line:
            continue
        vat_match = _E100_VAT_RE.search(line)
        if not vat_match:
            warnings.append(f"marker line found but no VAT registration number in: {line!r}")
            continue
        vat_number = vat_match.group(1).upper()
        country = _country_from_vat(vat_number)
        if country is None:
            warnings.append(
                f"could not determine a country from VAT number {vat_number!r}; entity not learned"
            )
            continue
        name = line.split(",", 1)[0].strip()
        entities.append(DetectedEntity(country=country, vat_number=vat_number, entity_name=name))
    return entities, warnings


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

    vehicle_ref = (row.get("vehicle_ref") or "").strip()
    station = (row.get("station") or "").strip()
    product = (row.get("product") or "").strip()
    if not vehicle_ref or not station or not product:
        raise ValueError(f"row {line_seq}: vehicle_ref/station/product must not be blank")

    gross_local = _decimal(row.get("gross_local"), "gross_local", line_seq)
    vat_rate = _decimal(row.get("vat_rate"), "vat_rate", line_seq)
    if not (Decimal(0) <= vat_rate <= Decimal(100)):
        raise ValueError(f"row {line_seq}: vat_rate {vat_rate} out of range [0, 100]")

    # The reverse VAT-inclusive-gross calculation — see module docstring.
    # Pure Decimal arithmetic throughout; never routed through float().
    net_local = gross_local / (Decimal(1) + vat_rate / Decimal(100))
    vat_local = gross_local - net_local

    return ParsedFuelLine(
        line_seq=line_seq,
        country=country,
        vehicle_ref=vehicle_ref,
        txn_date=txn_date,
        txn_time=(row.get("txn_time") or "").strip(),
        station=station,
        product=product,
        qty=_decimal(row.get("qty"), "qty", line_seq),
        currency=currency,
        net_local=net_local,
        vat_local=vat_local,
        gross_local=gross_local,
        invoice_ref=(row.get("invoice_ref") or "").strip() or None,
        provenance_note=(
            f"reverse-calculated from gross_local={gross_local} at vat_rate={vat_rate}%"
        ),
    )


class E100Parser(FuelCardParser):
    """Deterministic parser for an E100 statement export — see the module
    docstring for the exact format, the reverse VAT-inclusive-gross money
    model, and which parts are verbatim vs a documented interpretation."""

    network = "E100"

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
            # `eurowag.py`).
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"row {row_num}: wrong number of columns")
            lines.append(_line_from_row(row, row_num))

        if not lines:
            raise ValueError("statement carries no transaction rows")

        footer_text = "\n".join(raw_lines[sep_idx + 1 :]) if sep_idx < len(raw_lines) else ""
        entities, warnings = _detect_entities(footer_text)
        if not entities:
            warnings.append("no per-country seller entity detected in the statement footer")

        return ParsedStatement(
            network=self.network, lines=lines, entities=entities, warnings=warnings
        )
