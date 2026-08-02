"""The Q8/Kuwait Petroleum fuel-card statement parser (G3.2 slice 3;
`BA_fleet_fuel.md` §5.1). The THIRD network registered into WO-62's
`fuel_card_parser` registry — see
`docs/plan/plan-a/wo/WO-64-G3.2-slice3.md`.

WHY Q8 IS THE THIRD NETWORK
---------------------------------
WO-63's own report and `TODO.md`'s WO-63 entry both name Q8/Port One as the
recommended next slice, in the exact priority order WO-62 first set out
("2. **Q8/Port One**" of the six-then-five-then-four remaining networks) —
`ARCH_plan.md` line 934-937 confirms G3.2 as one P1/XL task (7 networks)
depending only on G3.1 (satisfied twice over already), independent of any
other in-flight M3 board item. Q8 is therefore both the order WO-63 itself
recommended and the order `ARCH_plan.md`'s dependency graph continues to
prefer.

`BA_fleet_fuel.md` §5.1's own network table row for Q8: *"Invoiced at LIST
price; separate Port One rebate invoice per country -> the `net_eur_eff`
layer. Per-line country + currency."* Two distinct things follow from that
one sentence, and this order deliberately does ONLY the first:

1. **In scope** — Q8's OWN invoice supplies `net_local`/`vat_local`/
   `gross_local` independently, exactly like Eurowag (list-price invoicing
   is not VAT-inclusive-gross the way E100's is) — so this parser reuses
   Eurowag's straightforward "read the three given figures" money model,
   NOT E100's reverse-calculation. What genuinely differs, and what this
   slice exists to PROVE for the first time in this codebase, is "per-line
   country + currency" taken literally: a SINGLE Q8 statement file can
   legitimately carry lines for more than one country (and hence more than
   one currency) in the same upload, because Q8 is invoiced monthly-PER-
   COUNTRY but the underlying transactions still land in one export.
   Neither `eurowag.py` nor `e100.py`'s fixtures ever exercised more than
   one country per file — this parser's own test suite is the first to do
   so on purpose (see `test_g3_2_q8_statement_ingest.py`'s multi-country,
   multi-currency ingestion test).

2. **Out of scope** — the "separate Port One rebate invoice per country"
   half of that same sentence, i.e. reconciling TWO different statement
   files (Q8's own list-price export and a SEPARATE Port One rebate
   export) into one adjusted `net_eur_eff` figure. That is a cross-
   statement MERGE, not a single-file parser concern, and `ARCH_plan.md`
   scopes it as its own, later board item: **G4.2 — "Price basis +
   `net_eur_eff` source guard"** (R49/R50, milestone M5, `ARCH_plan.md`
   line 965), which depends only on G1.2 (not on this parser) and is
   explicitly about GUARDING the input source once a rebate-merge exists —
   not about building the merge itself from an under-specified BA sentence.
   `BA_fleet_fuel.md` gives no worked Port One column layout or invoice-
   matching rule the way it gives Eurowag's footer sentence or E100's
   marker string verbatim; inventing one here would be exactly the kind of
   unharvested guess master-context §10 forbids ("no invented
   functionality"). This parser therefore leaves `net_eur_eff` at
   `fuel_ingest.ingest_transaction`'s existing default (`= net_eur`),
   UNCHANGED — identical to what WO-62 (Eurowag) and WO-63 (E100) already
   did for their own networks, and loudly flagged here so a future G4.2
   order knows exactly which gap it is closing.

WHY Q8 GETS NO SELLER-ENTITY DETECTION (UNLIKE EUROWAG/E100)
------------------------------------------------------------------------
R20's own worked examples in `BA_fleet_fuel.md` §3.B1 are Eurowag (the
per-country footer label) and E100 (the anchor-to-marker discipline) —
`docs/transport/rules.md`'s R20 row already records R20 as **"CLOSED for
Eurowag AND E100"**. No footer label, marker string, or worked invoice
sentence is given anywhere in the harvested spec for Q8 — inventing a
plausible-looking "Q8"/"Kuwait Petroleum" marker regex here would violate
R21's own "marker-only, no fuzzy auto-pairing" discipline at its root: a
marker this codebase invented, rather than one read verbatim off a real
Q8 invoice, is indistinguishable from a fuzzy heuristic dressed up as a
marker. `Q8Parser.parse()` therefore NEVER attempts entity detection —
`parsed.entities` is unconditionally `[]`, and a single explanatory
warning documents WHY (distinct from `eurowag.py`/`e100.py`'s "no marker
found in THIS document" warning, which implies detection was attempted
and failed; Q8's warning says detection was never attempted at all, so a
human reviewing `warnings` cannot mistake the two situations for each
other). The per-country Q8 entity, if ever needed on a claim, is curated
via `supplier_entity.set_registration` exactly like any network with no
R20 worked example — `learn_registration`'s R22 learning path is simply
never invoked by this parser, which is a legitimate, structural way for a
network to interact with G3.1's registry (WO-61 already ships `admin`
curation as a first-class, permanent source, independent of any parser).

WHY A MALFORMED LINE ABORTS THE WHOLE STATEMENT, NOT JUST THAT LINE
------------------------------------------------------------------------
Unchanged discipline from `eurowag.py`/`e100.py` — see either module's
docstring for the full reasoning (never silently under-report a period's
fuel spend).
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

MARKER = "Q8 STATEMENT"
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

# See module docstring "WHY Q8 GETS NO SELLER-ENTITY DETECTION" — this is
# the ONE warning surfaced every parse, distinguishing "never attempted"
# from `eurowag.py`/`e100.py`'s "attempted, none found in this document".
_NO_ENTITY_DETECTION_WARNING = (
    "Q8 seller-entity detection is not implemented for this network (no R20 "
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


class Q8Parser(FuelCardParser):
    """Deterministic parser for a Q8/Kuwait Petroleum statement export — see
    the module docstring for the exact format, the list-price money model
    (identical shape to Eurowag's, NOT E100's reverse-calc), and why no
    seller-entity detection is attempted."""

    network = "Q8"

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
            # `eurowag.py`/`e100.py`).
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"row {row_num}: wrong number of columns")
            lines.append(_line_from_row(row, row_num))

        if not lines:
            raise ValueError("statement carries no transaction rows")

        # No seller-entity detection is ever attempted — see module
        # docstring "WHY Q8 GETS NO SELLER-ENTITY DETECTION".
        return ParsedStatement(
            network=self.network,
            lines=lines,
            entities=[],
            warnings=[_NO_ENTITY_DETECTION_WARNING],
        )
