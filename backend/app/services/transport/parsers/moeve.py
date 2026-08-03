"""The Moeve (ex-Cepsa) fuel-card statement parser (G3.2 slice 6;
`BA_fleet_fuel.md` §5.1). The SIXTH network registered into WO-62's
`fuel_card_parser` registry — see
`docs/plan/plan-a/wo/WO-68-G3.2-slice6.md`.

WHY MOEVE IS THE SIXTH NETWORK
---------------------------------
WO-67's own follow-up list and `TODO.md`'s WO-67 entry both name Moeve as
the recommended next slice, in the priority order WO-62 first set out and
every slice since reaffirmed ("Moeve, then BP/Aral"). `BA_fleet_fuel.md`
§5.1's Moeve row: *"Moeve Pro Services S.A.U. (ex-Cepsa); UAB
«Client-LT-2»; EUR; monthly; ALL amounts VAT-inclusive; per-line IVA rate
(10% gasoleo / 21% EcoBlue); cash-at-pump nets against transfer; 6-dp
internal calc."* WO-63's E100 docstring predicted this slice: "Every
subsequent VAT-inclusive network this codebase onboards (Moeve is next)
needs this exact code path to exist first."

THE PER-LINE-IVA VAT-INCLUSIVE MONEY MODEL — THE REASON THIS SLICE EXISTS
------------------------------------------------------------------------
A VARIATION of E100's reverse VAT-inclusive-gross model with three
Moeve-only harvested properties:

1.  The IVA rate genuinely VARIES line-by-line within one statement
    (10% Spanish gasoleo next to 21% EcoBlue/services). Each line is
    reverse-calculated at ITS OWN printed rate:

        net_local  = q6(gross_local / (1 + iva_rate / 100))
        vat_local  = gross_local - net_local          (EXACT)

2.  The "6-dp internal calc" is harvested: `net_local` is quantized to
    SIX decimal places (ROUND_HALF_UP, `app.core.money.q`) inside the
    parser — the supplier's own printed working precision — and
    `vat_local` is the EXACT remainder, so `net + vat == gross` holds
    identically to the last digit and the implied rate stays within the
    WO-66 gate's rule-9 ±0.5pp window. The ONE 2-dp rounding point
    remains `fuel_ingest.ingest_transaction`'s `q2` (WO-50's
    convention). E100's fully-unrounded derivation is deliberately NOT
    changed — the 6-dp step is Moeve's quirk, not a shared convention.

3.  A per-line CASH-AT-PUMP settlement flag ("cash-at-pump nets against
    transfer"; §4.2's note column carried it per line). THE DECISION
    (documented in the work order): a cash-at-pump row IS a fuel line —
    it is invoiced, VAT-bearing fuel whose VAT is exactly as reclaimable
    as any other line's; what "nets against transfer" describes is
    SETTLEMENT (the monthly bank transfer pays the invoice total minus
    what was already paid in cash), which is M4/G3.5 territory, not
    capture. The flag therefore rides `provenance_note` (WO-50's
    note-split assigned provenance exactly this channel), NEVER mutates
    a figure, and the parse surfaces ONE advisory warning (count +
    gross sum of the flagged lines) so the review surface shows the
    netting figure without any number changing. The batch tie-out ties
    against the INVOICE total — all lines, cash included.

WHY RATE POLICY LIVES IN THE GATE, NOT HERE
------------------------------------------------------------------------
`iva_rate` is bounded to `[0, 100]` (E100's defensive bound — no real EU
VAT rate falls outside it; out of range refuses the statement) but there
is deliberately NO `{10, 21}` whitelist and NO product→rate mapping: the
WO-66 capture-review gate's rule-9 coherence table carries the HARVESTED
ES dual entry `(21, 10)` for exactly this network's rates ("ES gasoleo
10% next to the 21% standard", §2.1a). Re-encoding that policy here would
duplicate the rule — an off-table rate parses (the arithmetic is
self-consistent) and rule 9 flags it `vat_rate_incoherent` (warn, never
blocks) downstream, one implementation serving every network.

The MOEVE "PRN off pump PVP" discount is ON-INVOICE (`BA_fleet_fuel.md`
§4.2: already inside the printed gross this parser reverse-calculates
from) — nothing off-invoice is computed and `net_eur_eff` stays at
ingestion's default (= `net_eur`). Verifying the contracted discount was
granted is G3.4/M5 territory, exactly like TFC's tier-grant check.

WHAT IS VERBATIM FROM THE SOURCE, AND WHAT IS A DOCUMENTED INTERPRETATION
---------------------------------------------------------------------------
VERBATIM: ALL amounts VAT-inclusive; the per-line IVA rate (10/21 as the
harvested examples); the 6-dp internal calc; cash-at-pump nets against
the transfer; the EUR network expectation; the ON-INVOICE PRN discount.

A DOCUMENTED INTERPRETATION (no worked Moeve column layout exists in the
harvested spec — the same flagging discipline as every prior parser's
shape choice): a `"MOEVE STATEMENT"` marker header line, then a
transaction CSV block (`txn_date,txn_time,vehicle_ref,station,country,
product,qty,currency,gross_local,iva_rate,payment,invoice_ref`),
optionally followed by a `"---"` separator. `payment` is the per-line
settlement channel (`transfer` / `cash_at_pump`) — the CSV-column home
this format gives the harvested per-line cash-at-pump flag.

No currency enforcement: like E100 (and unlike TFC, whose EUR/L tier
constants make the check load-bearing there), this money model never
depends on the currency — §5.1's EUR expectation lives in the fixtures,
and a non-EUR line would simply ride ingestion's existing ECB branch.

FAIL-CLOSED REFUSALS (each aborts the WHOLE statement)
------------------------------------------------------------------------
- An `iva_rate` outside `[0, 100]` (never silently clamped — E100's own
  refusal, unchanged).
- An UNKNOWN `payment` value: a guessed settlement channel would corrupt
  the future M4 netting picture the same way a guessed tier corrupts a
  TFC net — the same discipline as `tfc.py`'s `station_class`.

WHY MOEVE GETS NO SELLER-ENTITY DETECTION (LIKE Q8/DKV/TFC)
------------------------------------------------------------------------
R20's worked examples in `BA_fleet_fuel.md` §3.B1 are Eurowag and E100
only; no footer label or anchor marker is given anywhere for Moeve.
Inventing a plausible-looking "Moeve Pro Services" marker regex would
violate R21's "marker-only, no fuzzy auto-pairing" discipline at its
root — identical reasoning to `q8.py`/`dkv.py`/`tfc.py`. `parse()`
therefore NEVER attempts entity detection: `entities` is unconditionally
`[]` with one explanatory warning whose wording says detection was never
attempted. The Moeve entity, if ever needed on a claim, is admin-curated
via `supplier_entity.set_registration`.

WHY A MALFORMED LINE ABORTS THE WHOLE STATEMENT, NOT JUST THAT LINE
------------------------------------------------------------------------
Unchanged discipline from every prior network parser — see
`eurowag.py`'s docstring (never silently under-report a period's fuel
spend). The out-of-range-rate / unknown-payment refusals above are
treated exactly as fatal as an unparseable decimal.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation

from app.core.money import q
from app.services.transport.fuel_card_parser import (
    FuelCardParser,
    ParsedFuelLine,
    ParsedStatement,
)

MARKER = "MOEVE STATEMENT"
SEPARATOR = "---"

# The harvested "6-dp internal calc" — the ONE place it lives (see module
# docstring). `q2` at the ingest boundary is unchanged.
SIX_DP = Decimal("0.000001")

# The per-line settlement channels. `transfer` is the ordinary
# monthly-bank-transfer line; `cash_at_pump` marks a line already paid in
# cash at the pump ("nets against transfer" — settlement-side only, never
# a figure change). Absence from this set REFUSES; a settlement channel
# is never guessed.
PAYMENT_CHANNELS = frozenset({"transfer", "cash_at_pump"})

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
    "iva_rate",
    "payment",
    "invoice_ref",
)

# See module docstring "WHY MOEVE GETS NO SELLER-ENTITY DETECTION" — this
# is the ONE warning surfaced every parse, distinguishing "never
# attempted" from `eurowag.py`/`e100.py`'s "attempted, none found".
_NO_ENTITY_DETECTION_WARNING = (
    "Moeve seller-entity detection is not implemented for this network (no R20 "
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


def _line_from_row(row: dict[str, str], line_seq: int) -> tuple[ParsedFuelLine, bool]:
    """Parse one CSV row. Returns `(line, is_cash_at_pump)` — the flag is
    reported so `parse()` can surface the ONE advisory cash-at-pump
    summary warning without re-reading provenance strings."""
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

    payment = (row.get("payment") or "").strip().lower()
    if payment not in PAYMENT_CHANNELS:
        # Never guess a settlement channel — see module docstring.
        known = "/".join(sorted(PAYMENT_CHANNELS))
        raise ValueError(
            f"row {line_seq}: unknown payment channel {payment!r} "
            f"(expected one of: {known}) — a settlement channel is never guessed"
        )
    is_cash = payment == "cash_at_pump"

    gross_local = _decimal(row.get("gross_local"), "gross_local", line_seq)
    iva_rate = _decimal(row.get("iva_rate"), "iva_rate", line_seq)
    if not (Decimal(0) <= iva_rate <= Decimal(100)):
        raise ValueError(f"row {line_seq}: iva_rate {iva_rate} out of range [0, 100]")

    # The per-line-IVA VAT-inclusive reverse calculation at the harvested
    # 6-dp internal precision — see module docstring. Pure Decimal
    # arithmetic throughout; never routed through float(). `vat_local` is
    # the EXACT remainder so `net + vat == gross` holds identically.
    net_local = q(gross_local / (Decimal(1) + iva_rate / Decimal(100)), SIX_DP)
    vat_local = gross_local - net_local

    provenance = (
        f"reverse-calculated from VAT-inclusive gross_local={gross_local} at "
        f"iva_rate={iva_rate}% (6-dp internal calc)"
    )
    if is_cash:
        # Settlement-side flag ONLY — the figures above are byte-identical
        # to a transfer line's (see the work order's cash-at-pump decision).
        provenance += "; paid cash at pump — nets against the bank transfer"

    line = ParsedFuelLine(
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
        provenance_note=provenance,
    )
    return line, is_cash


class MoeveParser(FuelCardParser):
    """Deterministic parser for a Moeve (ex-Cepsa) statement export — see
    the module docstring for the exact format, the per-line-IVA
    VAT-inclusive 6-dp money model, the cash-at-pump decision, the
    fail-closed refusals, and why no seller-entity detection is
    attempted."""

    network = "Moeve"

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
        cash_lines: list[ParsedFuelLine] = []
        for row_num, row in enumerate(reader, start=1):
            # A row with the wrong number of columns puts `None` either in a
            # declared cell (too few) or under the `None` restkey (too many)
            # — never silently coerce, refuse the whole statement (mirrors
            # every prior network parser).
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"row {row_num}: wrong number of columns")
            line, is_cash = _line_from_row(row, row_num)
            lines.append(line)
            if is_cash:
                cash_lines.append(line)

        if not lines:
            raise ValueError("statement carries no transaction rows")

        # No seller-entity detection is ever attempted — see module
        # docstring "WHY MOEVE GETS NO SELLER-ENTITY DETECTION".
        warnings = [_NO_ENTITY_DETECTION_WARNING]
        if cash_lines:
            # The ONE advisory cash-at-pump summary (see the work order's
            # documented decision): settlement-side information only — the
            # invoiced figures and the batch tie-out are unchanged.
            cash_gross = sum((line.gross_local for line in cash_lines), Decimal(0))
            warnings.append(
                f"{len(cash_lines)} cash-at-pump line(s) totalling gross {cash_gross} "
                "are netted against the bank transfer (settlement-side only; the "
                "invoiced figures and the VAT claim are unchanged)"
            )

        return ParsedStatement(
            network=self.network,
            lines=lines,
            entities=[],
            warnings=warnings,
        )
