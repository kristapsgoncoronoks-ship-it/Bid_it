"""The Eurowag / W.A.G. fuel-card statement parser (G3.2 slice 1;
`BA_fleet_fuel.md` §3.B1, §5.1).

WHY EUROWAG IS THE FIRST NETWORK
---------------------------------
`BA_fleet_fuel.md` §5.1's own network table lists Eurowag as "(parser
only)" — unlike the other six networks (Q8, BP, TFC, E100, Moeve, DKV),
which each carry a documented on-invoice discount/service-fee quirk baked
into `net_eur`, Eurowag's ONLY harvested quirk is R20's own worked example:
reading the per-country SELLER legal entity off the invoice **footer**,
matched against a legal-form token set, deliberately NOT the Czech
"W.A.G. Issuing Services, a.s." factoring entity the receivables are ceded
to (§3.B1). That makes it the best-specified network to prove the
registry pattern against, and the direct, literal closer of R20 — which
WO-61 (G3.1 slice 1) explicitly left open pending this order.

WHAT IS VERBATIM FROM THE SOURCE, AND WHAT IS A DOCUMENTED INTERPRETATION
---------------------------------------------------------------------------
VERBATIM (do not edit without re-reading `BA_fleet_fuel.md` §3.B1): the
footer label `"Pārdevējs / Verkoper:"`, the legal-form token set
`(BVBA|GmbH|UAB|SIA|s.r.o.|d.o.o.|a.s.|S.A.|AB|SE|BV)`, and the anchoring
discipline itself (match ONLY the labeled line, never any other
legal-form-shaped text in the document — the same reason the Czech
factoring entity can never be picked up by this parser, since the source
text explicitly frames it as a NEGATIVE example, not something a generic
scan would exclude on its own).

A DOCUMENTED INTERPRETATION (the source spec has no worked example for
this, and says so by tagging Eurowag "(parser only)" with no cadence/
currency/quirk cell the way E100/DKV/Q8/etc. get): the exact per-line
TRANSACTION CSV column layout below. `BA_fleet_fuel.md` §4.2's canonical
schema names the FIELDS every fuel transaction carries; this parser reads
those same fields from a CSV block (`txn_date,txn_time,vehicle_ref,
station,country,product,qty,currency,net_local,vat_local,gross_local,
invoice_ref`) preceded by a literal `"EUROWAG STATEMENT"` marker line and
followed by a `"---"` separator ahead of the free-text footer the seller
block lives in. This mirrors the real onboarding contract §5.1 describes
("get the first invoice PDF + portal export -> build the standard
workbook... a ~5-10 line row map") without inventing a business RULE — only
a column SHAPE, exactly the kind of interpretation WO-50 already made once
for `line_seq` (see that order's Implementation guidance §2) and flagged
the same way there.

ALSO A DOCUMENTED INTERPRETATION: the "Pārdevējs / Verkoper" bilingual
label is treated as the ONE constant Eurowag prints on every country's
invoice (only the entity DATA after the colon changes per country), not a
per-issuing-country label that would need its own translation. The source
text gives exactly one worked example and does not say the label itself is
localized — assuming a SINGLE universal label is the more conservative
reading (it never invents a language-specific label this order has no
evidence for) and is what lets one regex serve every country Eurowag
issues through, matching R20's own BE/CZ worked example directly.

WHY A MALFORMED LINE ABORTS THE WHOLE STATEMENT, NOT JUST THAT LINE
------------------------------------------------------------------------
`parse()` raises `ValueError` (never silently drops a row) the first time
a required field is missing or unparseable. Skipping a bad row and
continuing would silently under-report a period's fuel spend with no
structural signal that anything was dropped — the same reasoning
`app.services.parser._parse_csv`/`_parse_json` already apply (raise on a
malformed structured input, never partially ingest). A tie-out regime that
checks line-COUNT against the invoice PDF (G3.3, `ARCH_plan.md` line
939-945) is explicitly future work, layered on top of this parser, not
duplicated here.
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

MARKER = "EUROWAG STATEMENT"
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
    "net_local",
    "vat_local",
    "gross_local",
    "invoice_ref",
)

# Harvested verbatim, `BA_fleet_fuel.md` §3.B1 — see module docstring.
_EW_SELLER_LABEL = "Pārdevējs / Verkoper:"
_EW_LEGAL_FORMS = (
    "BVBA",
    "GmbH",
    "UAB",
    "SIA",
    "s.r.o.",
    "d.o.o.",
    "a.s.",
    "S.A.",
    "AB",
    "SE",
    "BV",
)
# Deliberately permissive on the VAT BODY (alphanumeric, 4-17 chars) — the
# country-code check happens separately in `_country_from_vat`, so a VAT-like
# token with an implausible (non-alphabetic) prefix is still CAPTURED here
# and correctly refused one step later, rather than silently failing to
# match at all (which would misreport as "no VAT registration number found"
# instead of the more precise "found a VAT but couldn't place its country").
_EW_VAT_RE = re.compile(r"PVN reg\.\s*Nr\.[^:]*:\s*([A-Za-z0-9]{4,17})")

# EU VAT numbers are prefixed with the ISO-3166 country code, with one
# documented exception (Greece prints "EL", not "GR"). A fuller exceptions
# table is future work if a network onboards a country with another
# differing convention — never a silent guess when the prefix is unknown.
_VAT_PREFIX_TO_COUNTRY = {"EL": "GR"}


def _country_from_vat(vat_number: str) -> str | None:
    """The 2-letter ISO country from a VAT id's prefix, or `None` when the
    prefix isn't a plausible country code — never a guess."""
    prefix = vat_number[:2].upper()
    if len(prefix) != 2 or not prefix.isalpha():
        return None
    return _VAT_PREFIX_TO_COUNTRY.get(prefix, prefix)


def _detect_entities(text: str) -> tuple[list[DetectedEntity], list[str]]:
    """Anchor ONLY to lines carrying `_EW_SELLER_LABEL` (R20/B1's "anchor" —
    the same discipline `_e100_seller_name` uses for the E100 marker). Any
    OTHER legal-form-shaped text elsewhere in the document — e.g. a
    factoring-entity disclosure sentence naming "W.A.G. Issuing
    Services, a.s." — is never even inspected, so the Czech factoring
    entity can never be picked up by accident (§3.B1's own worked
    example)."""
    entities: list[DetectedEntity] = []
    warnings: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if _EW_SELLER_LABEL not in line:
            continue
        body = line.split(_EW_SELLER_LABEL, 1)[1].strip()
        if not any(form in body for form in _EW_LEGAL_FORMS):
            warnings.append(f"seller line found but no recognised legal form in: {body!r}")
            continue
        name = body.split(",", 1)[0].strip()
        vat_match = _EW_VAT_RE.search(body)
        if not vat_match:
            warnings.append(f"seller line found but no VAT registration number in: {body!r}")
            continue
        vat_number = vat_match.group(1).upper()
        country = _country_from_vat(vat_number)
        if country is None:
            warnings.append(
                f"could not determine a country from VAT number {vat_number!r}; entity not learned"
            )
            continue
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
        net_local=_decimal(row.get("net_local"), "net_local", line_seq),
        vat_local=_decimal(row.get("vat_local"), "vat_local", line_seq),
        gross_local=_decimal(row.get("gross_local"), "gross_local", line_seq),
        invoice_ref=(row.get("invoice_ref") or "").strip() or None,
    )


class EurowagParser(FuelCardParser):
    """Deterministic parser for a Eurowag/W.A.G. statement export — see the
    module docstring for the exact format and which parts are verbatim vs a
    documented interpretation."""

    network = "Eurowag"

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
            # — either way, never silently coerce, refuse the whole statement.
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
