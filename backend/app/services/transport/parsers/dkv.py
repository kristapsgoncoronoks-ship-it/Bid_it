"""The DKV Euro Service fuel-card statement parser (G3.2 slice 4;
`BA_fleet_fuel.md` §5.1). The FOURTH network registered into WO-62's
`fuel_card_parser` registry — see
`docs/plan/plan-a/wo/WO-65-G3.2-slice4.md`.

WHY DKV IS THE FOURTH NETWORK
---------------------------------
WO-64's own report and `TODO.md`'s WO-64 entry both name DKV as the
recommended next slice, in the exact priority order WO-62 first set out and
WO-63/WO-64 each reaffirmed verbatim ("DKV (5.63% service fee), TFC by
Moya, Moeve, BP/Aral — priority ordering unchanged, now with Q8 struck
off"). `BA_fleet_fuel.md` §5.1's DKV row: *"«Client-EE» AS; SEK/EUR;
semi-monthly; Swedish fiscal rep; flat 1.30 SEK/L diesel discount; 5.63%
service fee on parking/services; trusts the supplier's per-line EUR and
pro-rates."*

THE SUPPLIER-STATED-EUR MONEY MODEL — THE REASON THIS SLICE EXISTS
------------------------------------------------------------------------
"Trusts the supplier's per-line EUR and pro-rates" is a THIRD money model,
distinct from Eurowag/Q8's independently-given figures and E100's
VAT-inclusive reverse-calculation: DKV's statement carries a per-line EUR
figure computed by the SUPPLIER, and Fleet Fuel trusted that figure instead
of running its own ECB conversion — because the supplier's EUR is what the
legal invoice document actually says, and the invoice is what a refund
state audits against. Recomputing it via ECB would silently produce figures
that disagree with the document (master-context §4.10/§4.19's exact
failure mode). This parser therefore carries the statement's own `net_eur`
column into `ParsedFuelLine.stated_net_eur` for every NON-EUR line;
`statement_ingest._resolve_line` (extended by the same work order) uses it
verbatim as `net_eur`, PRO-RATES the VAT at the same implied basis (the
harvested "and pro-rates" clause), records the implied applied rate, and
freezes the official ECB reference best-effort — `fx_source="stated"`, the
platform's pre-existing ADR-0010 convention (`app/models/fx.py::FxSource.
stated`, precedent `app.services.expenses.apply_item_fx`).

An EUR-currency DKV line has nothing to state: its `net_eur` column MUST
equal `net_local` (a mismatch is a malformed row — the whole statement
aborts, never a silent pick-one), and `stated_net_eur` stays `None` so the
line rides the same identity branch every other network's EUR lines use.

WHAT IS VERBATIM FROM THE SOURCE, AND WHAT IS A DOCUMENTED INTERPRETATION
---------------------------------------------------------------------------
VERBATIM: the trust-the-supplier's-per-line-EUR-and-pro-rate model itself,
the SEK/EUR currency mix, and the fact that the flat 1.30 SEK/L diesel
discount and the 5.63% parking/services service fee are ON-INVOICE — i.e.
ALREADY INSIDE the given figures (`BA_fleet_fuel.md` §4.2: "on-invoice
discounts (... DKV 1.30 SEK/L + 5.63% service fee) are already inside
`net_eur`"). This parser READS those figures; it never recomputes the
discount or the fee (verifying the supplier actually applied the
contracted terms is G3.4's advisory post-capture-check territory, not
capture).

A DOCUMENTED INTERPRETATION (no worked DKV column layout exists in the
harvested spec, the same flagging discipline as every prior parser's shape
choice): a `"DKV STATEMENT"` marker header line, then a transaction CSV
block — Eurowag's column shape plus the one `net_eur` column
(`txn_date,txn_time,vehicle_ref,station,country,product,qty,currency,
net_local,vat_local,gross_local,net_eur,invoice_ref`) — optionally
followed by a `"---"` separator. "Pro-rates" is read as: the VAT converts
at the same implied rate the supplier's net conversion used
(`vat_eur = vat_local * net_eur / net_local`), which keeps the line
internally consistent with the stated basis; that arithmetic lives in
`statement_ingest`, not here — the parser only carries the figure.

WHY DKV GETS NO SELLER-ENTITY DETECTION (LIKE Q8, UNLIKE EUROWAG/E100)
------------------------------------------------------------------------
R20's worked examples in `BA_fleet_fuel.md` §3.B1 are Eurowag and E100
only; no footer label or anchor marker is given anywhere for DKV.
Inventing a plausible-looking "DKV Euro Service" marker regex would
violate R21's "marker-only, no fuzzy auto-pairing" discipline at its root
— identical reasoning to `q8.py`'s own refusal, see that module's
docstring. `parse()` therefore NEVER attempts entity detection:
`entities` is unconditionally `[]` with one explanatory warning whose
wording says detection was never attempted (distinct from Eurowag/E100's
"attempted, none found"). The per-country DKV entity (e.g. the Swedish
fiscal representative), if ever needed on a claim, is admin-curated via
`supplier_entity.set_registration`.

WHY A MALFORMED LINE ABORTS THE WHOLE STATEMENT, NOT JUST THAT LINE
------------------------------------------------------------------------
Unchanged discipline from `eurowag.py`/`e100.py`/`q8.py` — see any of
those modules' docstrings (never silently under-report a period's fuel
spend). The EUR-row `net_eur != net_local` mismatch is treated exactly as
fatal as an unparseable decimal.
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

MARKER = "DKV STATEMENT"
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
    "net_eur",
    "invoice_ref",
)

# See module docstring "WHY DKV GETS NO SELLER-ENTITY DETECTION" — this is
# the ONE warning surfaced every parse, distinguishing "never attempted"
# from `eurowag.py`/`e100.py`'s "attempted, none found in this document".
_NO_ENTITY_DETECTION_WARNING = (
    "DKV seller-entity detection is not implemented for this network (no R20 "
    "worked marker documented in BA_fleet_fuel.md, unlike Eurowag/E100) — "
    "register the per-country entity via admin curation "
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

    vehicle_ref = (row.get("vehicle_ref") or "").strip()
    station = (row.get("station") or "").strip()
    product = (row.get("product") or "").strip()
    if not vehicle_ref or not station or not product:
        raise ValueError(f"row {line_seq}: vehicle_ref/station/product must not be blank")

    net_local = _decimal(row.get("net_local"), "net_local", line_seq)
    supplier_net_eur = _decimal(row.get("net_eur"), "net_eur", line_seq)

    stated_net_eur: Decimal | None
    provenance_note: str | None
    if currency == "EUR":
        # Nothing to state on an EUR line — but the column must be
        # CONSISTENT with the local figure, or the row is malformed (never
        # silently pick one of two disagreeing figures).
        if supplier_net_eur != net_local:
            raise ValueError(
                f"row {line_seq}: EUR row's net_eur column ({supplier_net_eur}) "
                f"must equal net_local ({net_local})"
            )
        stated_net_eur = None
        provenance_note = None
    else:
        stated_net_eur = supplier_net_eur
        # The ONLY human-visible record of the trusted-document basis until
        # G3.3's tie-out exists — mirrors `e100.py`'s reverse-calc note.
        provenance_note = (
            f"supplier-stated per-line EUR: net_eur={supplier_net_eur} as printed "
            "(DKV trusts-the-document basis; VAT pro-rated at the same implied rate)"
        )

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
        vat_local=_decimal(row.get("vat_local"), "vat_local", line_seq),
        gross_local=_decimal(row.get("gross_local"), "gross_local", line_seq),
        invoice_ref=(row.get("invoice_ref") or "").strip() or None,
        provenance_note=provenance_note,
        stated_net_eur=stated_net_eur,
    )


class DKVParser(FuelCardParser):
    """Deterministic parser for a DKV Euro Service statement export — see
    the module docstring for the exact format, the supplier-stated-EUR
    money model ("trusts the supplier's per-line EUR and pro-rates"), and
    why no seller-entity detection is attempted."""

    network = "DKV"

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
            # `eurowag.py`/`e100.py`/`q8.py`).
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"row {row_num}: wrong number of columns")
            lines.append(_line_from_row(row, row_num))

        if not lines:
            raise ValueError("statement carries no transaction rows")

        # No seller-entity detection is ever attempted — see module
        # docstring "WHY DKV GETS NO SELLER-ENTITY DETECTION".
        return ParsedStatement(
            network=self.network,
            lines=lines,
            entities=[],
            warnings=[_NO_ENTITY_DETECTION_WARNING],
        )
