"""The BP/Aral (B2Mobility GmbH) fuel-card statement parser (G3.2 slice 7;
`BA_fleet_fuel.md` §5.1). The SEVENTH — and LAST — network registered into
WO-62's `fuel_card_parser` registry: this slice CLOSES G3.2. See
`docs/plan/plan-a/wo/WO-69-G3.2-slice7.md`.

WHY BP/ARAL IS THE SEVENTH NETWORK
------------------------------------
WO-68's own "Out of scope" list and `TODO.md`'s WO-68 entry both name
BP/Aral as the last remaining network, in the priority order WO-62 first
set out and every slice since reaffirmed. `BA_fleet_fuel.md` §5.1's row:
*"BP / Aral (B2Mobility GmbH); SIA «Client-LV»; PLN; monthly; Poland
split-payment (MPP) mandatory; A2 toll ~2.5% ORS fee lines; FX via dated
ECB rate with `month_config.FX` fallback."* The network code is `"BP"` —
§4.2's own supplier column (Q8, BP, TFC, E100, MOEVE, DKV).

THE MONEY MODEL IS DELIBERATELY THE SIMPLE ONE
------------------------------------------------------------------------
Nothing in the harvested row says BP's figures are derived (TFC), reverse
VAT-inclusive (E100/Moeve) or supplier-stated-EUR (DKV) — the figures are
INDEPENDENTLY GIVEN, Eurowag/Q8's shape: `net_local`/`vat_local`/
`gross_local` are read VERBATIM off the file as unrounded Decimals. What
distinguishes the network lives elsewhere:

1.  It is the first ALL-PLN network, so ingestion's EXISTING ECB branch
    (`fx.to_eur` at each line's own `txn_date`, `fx_source="ecb"`) is the
    DEFAULT conversion path rather than Q8's one-line exception. The
    harvested "FX via dated ECB rate" IS that branch. The harvested
    "`month_config.FX` fallback" is deliberately NOT reproduced: a static
    fallback table is the retired system's compromise and would violate
    master-context §4.15 verbatim ("`unknown` yields `NULL`, never a
    guessed number") — the platform refuses (`fx_rate_unavailable`, zero
    rows, the two-phase guarantee) instead of guessing. The stricter of
    the two harvested rules wins.

2.  THE ORS-FEE-LINE DECISION (documented in the work order): an ORS fee
    line IS a `ParsedFuelLine` — an ordinary VAT-bearing statement line;
    never a coversheet adjustment, never merged into its toll line. §5.1
    calls them fee LINES; §4.2's canonical schema models non-fuel lines
    (`product_group` `Toll/Fees` / `Service/Other`), and the closest
    harvested precedent — DKV's 5.63% service fee — is ON-INVOICE. The
    fee's own 23% VAT is exactly as reclaimable as any other line's, and
    the batch tie-out ties the INVOICE total, fee lines included. The
    "~2.5% of the toll" relationship is a CONTRACT term (the "~" says
    approximate) — verifying it is G3.4/M5 contract-audit territory,
    exactly like TFC's tier grant; this parser only surfaces ONE advisory
    warning (ORS-fee count + per-currency ORS and toll gross sums) so a
    reviewer can eyeball the contracted ratio without any number changing.

3.  THE MPP DECISION (documented in the work order): Polish split-payment
    (mechanizm podzielonej płatności) is SETTLEMENT — it routes the VAT
    portion of the PAYMENT to the supplier's dedicated VAT account; the
    invoice's printed net/vat/gross are unchanged by how the payment is
    routed. Modelling the split payment is M4 territory (the same
    boundary WO-68's cash-at-pump decision drew). At CAPTURE time the
    observable is the statutory annotation printed on the document: this
    parser scans for the literal `"MECHANIZM PODZIELONEJ PŁATNOŚCI"`
    line. Present ⇒ ONE advisory warning with the per-currency VAT total
    the mechanism directs to the VAT account ("settlement-side only").
    Absent ⇒ ONE advisory warning that the annotation is missing on a
    mandatory-MPP network — deliberately fail-OPEN: an absent settlement
    annotation corrupts no captured figure, so blocking on it would
    invent policy. Warnings never block (§2.1a). Advisory totals are
    PER-CURRENCY, never a raw cross-currency sum (§4.14).

WHY RATE POLICY LIVES IN THE GATE, NOT HERE
------------------------------------------------------------------------
No VAT-rate whitelist and no line_type→rate mapping: the WO-66
capture-review gate's rule-9 coherence table carries the HARVESTED PL
dual entry `(23, 8)` — "PL diesel 8% next to the 23% standard" — which
coheres exactly this network's line mix (23% fuel and ORS fee, 8% A2
toll). Re-encoding that policy here would duplicate the rule; one
implementation serves every network (the same argument WO-68 documented
for Moeve's ES rates).

WHAT IS VERBATIM FROM THE SOURCE, AND WHAT IS A DOCUMENTED INTERPRETATION
---------------------------------------------------------------------------
VERBATIM: the PLN network expectation; mandatory Polish split-payment
(MPP); the A2-toll ~2.5% ORS fee lines; FX via the dated ECB rate; the
`"BP"` supplier code; monthly cadence (G3.5's concern, not this
parser's).

A DOCUMENTED INTERPRETATION (no worked BP/Aral column layout exists in
the harvested spec — the same flagging discipline as every prior
parser's shape choice): a `"BP ARAL STATEMENT"` marker header line, an
optional statement-level MPP annotation line (the statutory wording),
then a transaction CSV block (`txn_date,txn_time,vehicle_ref,station,
country,product,qty,currency,net_local,vat_local,gross_local,line_type,
invoice_ref`), optionally followed by a `"---"` separator. `line_type`
(`fuel` / `toll` / `ors_fee`) is the CSV-column home this format gives
the harvested fee-line/toll distinction.

No currency enforcement: like E100/Moeve (and unlike TFC, whose EUR/L
tier constants make the check load-bearing there), this money model
never depends on the currency — §5.1's PLN expectation lives in the
fixtures, and a PLN line simply rides ingestion's existing ECB branch.

FAIL-CLOSED REFUSALS (each aborts the WHOLE statement)
------------------------------------------------------------------------
- An UNKNOWN `line_type` value: a guessed line kind would silently
  mis-map the Art. 9 goods-code path downstream (fuel=1 via `Diesel`,
  road tolls=4 via `Toll/Fees`, other=10 via `Service/Other`) — a wrong
  goods code on a legal filing. The same discipline as TFC's
  `station_class` and Moeve's `payment` channel.

WHY BP GETS NO SELLER-ENTITY DETECTION (LIKE Q8/DKV/TFC/MOEVE)
------------------------------------------------------------------------
R20's worked examples in `BA_fleet_fuel.md` §3.B1 are Eurowag and E100
only; no footer label or anchor marker is given anywhere for BP/Aral.
Inventing a plausible-looking "B2Mobility" marker regex would violate
R21's "marker-only, no fuzzy auto-pairing" discipline at its root —
identical reasoning to `q8.py`/`dkv.py`/`tfc.py`/`moeve.py`. `parse()`
therefore NEVER attempts entity detection: `entities` is unconditionally
`[]` with one explanatory warning whose wording says detection was never
attempted. The BP entity, if ever needed on a claim, is admin-curated
via `supplier_entity.set_registration`. R20 stays CLOSED at exactly
Eurowag+E100.

WHY A MALFORMED LINE ABORTS THE WHOLE STATEMENT, NOT JUST THAT LINE
------------------------------------------------------------------------
Unchanged discipline from every prior network parser — see
`eurowag.py`'s docstring (never silently under-report a period's fuel
spend). The unknown-`line_type` refusal above is treated exactly as
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

MARKER = "BP ARAL STATEMENT"
SEPARATOR = "---"

# The statutory Polish split-payment annotation this parser scans for —
# a statement-level literal, never a fuzzy match (THE MPP DECISION in the
# module docstring / the work order).
MPP_ANNOTATION = "MECHANIZM PODZIELONEJ PŁATNOŚCI"

# The per-line kinds. `fuel` is an ordinary fuel line; `toll` an A2-toll
# line; `ors_fee` the ~2.5% ORS fee riding on the tolls (an ordinary
# VAT-bearing line — THE ORS-FEE-LINE DECISION). Absence from this set
# REFUSES; a line kind is never guessed (it feeds the Art. 9 goods-code
# path downstream).
LINE_TYPES = frozenset({"fuel", "toll", "ors_fee"})

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
    "line_type",
    "invoice_ref",
)

# See module docstring "WHY BP GETS NO SELLER-ENTITY DETECTION" — this is
# the ONE warning surfaced every parse, distinguishing "never attempted"
# from `eurowag.py`/`e100.py`'s "attempted, none found".
_NO_ENTITY_DETECTION_WARNING = (
    "BP seller-entity detection is not implemented for this network (no R20 "
    "worked marker documented in BA_fleet_fuel.md, unlike Eurowag/E100) — "
    "register the entity via admin curation "
    "(supplier_entity.set_registration) if needed on a claim."
)

# THE MPP DECISION's fail-OPEN advisory for a statement missing the
# statutory annotation — a warning, never a block (an absent settlement
# annotation corrupts no captured figure).
_MPP_MISSING_WARNING = (
    "no split-payment (MPP) annotation found, though Polish split-payment "
    "(mechanizm podzielonej płatności) is mandatory for this network — "
    "review the source document"
)


def _decimal(raw: str | None, field: str, row_num: int) -> Decimal:
    value = (raw or "").strip()
    if not value:
        raise ValueError(f"row {row_num}: missing {field}")
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ValueError(f"row {row_num}: invalid decimal for {field}: {value!r}") from None


def _per_currency(totals: dict[str, Decimal]) -> str:
    """Render per-currency totals as `'123.45 PLN'` (comma-joined when a
    statement legitimately spans currencies) — never a raw cross-currency
    sum (master-context §4.14)."""
    return ", ".join(f"{totals[ccy]} {ccy}" for ccy in sorted(totals))


def _line_from_row(row: dict[str, str], line_seq: int) -> tuple[ParsedFuelLine, str]:
    """Parse one CSV row. Returns `(line, line_type)` — the normalized kind
    is reported so `parse()` can build the advisory ORS/MPP summaries
    without re-reading provenance strings."""
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

    line_type = (row.get("line_type") or "").strip().lower()
    if line_type not in LINE_TYPES:
        # Never guess a line kind — it feeds the Art. 9 goods-code path
        # (see module docstring's fail-closed refusals).
        known = "/".join(sorted(LINE_TYPES))
        raise ValueError(
            f"row {line_seq}: unknown line_type {line_type!r} "
            f"(expected one of: {known}) — a line kind is never guessed"
        )

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
        # Independently-given figures, read VERBATIM — no derivation, no
        # rounding here (q2 stays at the ingest boundary; see module
        # docstring "the money model is deliberately the simple one").
        net_local=_decimal(row.get("net_local"), "net_local", line_seq),
        vat_local=_decimal(row.get("vat_local"), "vat_local", line_seq),
        gross_local=_decimal(row.get("gross_local"), "gross_local", line_seq),
        invoice_ref=(row.get("invoice_ref") or "").strip() or None,
        provenance_note=f"line_type={line_type}",
    )
    return line, line_type


class BPParser(FuelCardParser):
    """Deterministic parser for a BP/Aral (B2Mobility) statement export —
    see the module docstring for the exact format, the PLN/ECB money
    model, the ORS-fee-line and MPP decisions, the fail-closed refusal,
    and why no seller-entity detection is attempted."""

    network = "BP"

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

        # The statement-level MPP annotation scan — a literal, checked over
        # the NON-CSV portion of the file (header block + trailer), so a
        # station or product cell can never accidentally satisfy it.
        annotation_lines = raw_lines[:header_idx] + raw_lines[sep_idx:]
        has_mpp = any(MPP_ANNOTATION in line.upper() for line in annotation_lines)

        csv_block = "\n".join(raw_lines[header_idx:sep_idx])
        reader = csv.DictReader(io.StringIO(csv_block))
        fieldnames = reader.fieldnames or []
        missing = [c for c in _REQUIRED_COLUMNS if c not in fieldnames]
        if missing:
            raise ValueError(f"statement CSV is missing required column(s): {missing}")

        lines: list[ParsedFuelLine] = []
        vat_by_currency: dict[str, Decimal] = {}
        ors_gross_by_currency: dict[str, Decimal] = {}
        toll_gross_by_currency: dict[str, Decimal] = {}
        ors_count = 0
        for row_num, row in enumerate(reader, start=1):
            # A row with the wrong number of columns puts `None` either in a
            # declared cell (too few) or under the `None` restkey (too many)
            # — never silently coerce, refuse the whole statement (mirrors
            # every prior network parser).
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"row {row_num}: wrong number of columns")
            line, line_type = _line_from_row(row, row_num)
            lines.append(line)
            ccy = line.currency
            vat_by_currency[ccy] = vat_by_currency.get(ccy, Decimal(0)) + line.vat_local
            if line_type == "ors_fee":
                ors_count += 1
                ors_gross_by_currency[ccy] = (
                    ors_gross_by_currency.get(ccy, Decimal(0)) + line.gross_local
                )
            elif line_type == "toll":
                toll_gross_by_currency[ccy] = (
                    toll_gross_by_currency.get(ccy, Decimal(0)) + line.gross_local
                )

        if not lines:
            raise ValueError("statement carries no transaction rows")

        # No seller-entity detection is ever attempted — see module
        # docstring "WHY BP GETS NO SELLER-ENTITY DETECTION".
        warnings = [_NO_ENTITY_DETECTION_WARNING]

        # THE MPP DECISION's two advisory wordings — settlement-side
        # information only; the invoiced figures and the VAT claim are
        # unchanged either way, and neither warning ever blocks (§2.1a).
        if has_mpp:
            warnings.append(
                "statement is annotated MECHANIZM PODZIELONEJ PŁATNOŚCI (split "
                f"payment, MPP): the VAT total of {_per_currency(vat_by_currency)} "
                "is settled to the supplier's dedicated VAT account "
                "(settlement-side only; the invoiced figures and the VAT claim "
                "are unchanged)"
            )
        else:
            warnings.append(_MPP_MISSING_WARNING)

        if ors_count:
            # THE ORS-FEE-LINE DECISION's one advisory summary: the fee's
            # contracted ~2.5%-of-toll ratio is NOT verified here (G3.4/M5
            # contract-audit territory) — the per-currency sums let a
            # reviewer eyeball it without any number changing.
            toll_part = (
                f" against toll lines totalling gross {_per_currency(toll_gross_by_currency)}"
                if toll_gross_by_currency
                else ""
            )
            warnings.append(
                f"{ors_count} ORS fee line(s) totalling gross "
                f"{_per_currency(ors_gross_by_currency)}{toll_part} — the "
                "contracted ~2.5% ratio is not verified at capture (advisory "
                "only; contract verification is G3.4 territory)"
            )

        return ParsedStatement(
            network=self.network,
            lines=lines,
            entities=[],
            warnings=warnings,
        )
