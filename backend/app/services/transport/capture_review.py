"""The capture review gate (G3.3 / WO-66; `BA_fleet_fuel.md` §2.1a, R25) —
the FIRST of the two independent validation regimes: *"is this captured
batch internally coherent enough to register?"*

WHY THIS IS A SEPARATE REGIME FROM THE ENGINE TIE-OUT (AND FROM THE AP
VALIDATION ENGINE)
------------------------------------------------------------------------
R25 preserves TWO regimes because they answer different questions and fail
differently: this gate blocks REGISTRATION of one captured batch
(`can_commit=False` -> `statement_ingest.ingest_statement` refuses, zero
rows written); the engine tie-out (`app.services.transport.tie_out`)
halts the monthly CLOSE when the period's captured aggregate disagrees
with the figures typed from the invoice PDF. The harvested source kept
them structurally separate (§2.1a: "`validate.py` is not in that chain...
Two separate regimes") — merging them would recreate exactly the
two-engines-in-one-question defect C1.1/WO-7 fixed on the AP side, in
reverse. And per ADR-P3 (transport domain isolation) this module does NOT
reuse `app.services.validation` (the AP engine): that registry's rules are
AP-invoice-shaped (header/line reconciliation over the `Invoice` ORM
model); these rules are captured-fuel-batch-shaped. Same *pattern*
(explicit per-rule severity, tolerance stated per rule, warnings never
block), different domain — the same honest reading WO-62 applied to
`extraction_provider`.

THE VERDICT LATTICE (harvested verbatim, §2.1a)
------------------------------------------------
`ok < warn < error`. Nine rules, in order; rule 4 (net/vat must parse as
numbers) EARLY-RETURNS because every later rule needs the parsed numbers.
**Warnings never block.** The commit gate is exactly the harvested
formula: `can_commit = (errors == 0) and (tie is None or tie.ok)`.

WHY `CapturedLine` CARRIES RAW STRINGS, NOT THE TYPED `ParsedFuelLine`
------------------------------------------------------------------------
Rule 4 ("net/vat parse as numbers", error + early return) is only real
over the raw captured text — a typed `Decimal` field would satisfy it
vacuously. The review gate is the verdict a human sees at the review
surface, where values are still document-shaped strings. The
`review_statement()` adapter maps an already-typed `ParsedStatement`
into this shape (ISO date, `str(Decimal)`) so the SAME rule set serves
both the future review screen and today's `ingest_statement` wiring —
one implementation, never two (the C1.1 lesson).

THE BATCH TIE-OUT IS COMPARED ON DECIMALS
------------------------------------------
`abs(q2(Σ net+vat) − q2(coversheet_total)) <= 0.02` — BOTH sides are
quantized (`app.core.money.q2`, ROUND_HALF_UP) before the comparison so a
diff sitting exactly on the 2-cent boundary never flips on binary-float
noise (§2.1a verbatim; master-context §4.9). `coversheet_total=None`
means "no coversheet figure was captured" — the tie constraint simply
does not apply (`tie is None` in the commit-gate formula), it is NOT a
passed tie.

THE `VAT_RATES` COHERENCE TABLE — what is harvested vs reconstructed
------------------------------------------------------------------------
Harvested verbatim (§2.1a): the table covers 23 countries; the coherence
window is ±0.5 percentage points; `vat == 0` is deliberately SKIPPED
(domestic/exempt is validated elsewhere); the whole check is deliberately
a WARN (a coherence check, not a hard rule, because reduced rates exist);
and four dual entries encode real business cases — **PL diesel 8%** (next
to the 23% standard), **ES gasoleo 10%** (next to 21%), **EE 22 & 20**
(the rate change straddle), **FI 24 & 25.5** (same). The source system's
literal table may not be copied (master-context §10 — the repository is
scheduled for deletion); standard VAT rates are public statute, so the
remaining membership/rates below are a DOCUMENTED RECONSTRUCTION from
public law (the same "documented best effort" discipline
`product_group.py` applies to its non-harvested token sets): the 23
countries are the contiguous-EU road-transport set (EU-27 minus the
islands/peninsula states IE·GR·CY·MT a Baltic haulier does not transit).
A country NOT in this table yields NO rule-9 finding — coherence needs a
known rate to cohere with; unknown inputs fail toward not crying wolf
(the R26 ethos, applied here).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.core.money import q2
from app.services.transport.fuel_card_parser import ParsedStatement

# Verdicts, in lattice order: ok < warn < error. A line with no findings is
# "ok" (no Finding object is emitted for ok — absence IS the ok verdict).
WARN: Literal["warn"] = "warn"
ERROR: Literal["error"] = "error"

# Harvested §2.1a: |q2(sum) - q2(coversheet)| <= 0.02, compared on Decimals.
TIE_TOLERANCE = Decimal("0.02")

# Rule 8's plausibility ceiling — harvested verbatim (`net <= 5,000,000`).
NET_PLAUSIBILITY_CEILING = Decimal("5000000")

# Rule 9's coherence window — harvested verbatim (±0.5 percentage points).
RATE_COHERENCE_PP = Decimal("0.5")

# The 23-country VAT-rate coherence table. See the module docstring for
# exactly which entries are harvested (the four dual entries, the count,
# the semantics) and which are reconstructed from public VAT law. Values
# are percentages; a tuple lists every rate the check accepts for that
# country (rule 9 passes if vat/net is within ±0.5pp of ANY of them).
VAT_RATES: dict[str, tuple[Decimal, ...]] = {
    "AT": (Decimal("20"),),
    "BE": (Decimal("21"),),
    "BG": (Decimal("20"),),
    "CZ": (Decimal("21"),),
    "DE": (Decimal("19"),),
    "DK": (Decimal("25"),),
    # Harvested dual entry — the 22 -> 20 rate-change straddle (§2.1a).
    "EE": (Decimal("22"), Decimal("20")),
    # Harvested dual entry — gasoleo 10% next to the 21% standard (§2.1a).
    "ES": (Decimal("21"), Decimal("10")),
    # Harvested dual entry — the 24 -> 25.5 rate-change straddle (§2.1a).
    "FI": (Decimal("24"), Decimal("25.5")),
    "FR": (Decimal("20"),),
    "HR": (Decimal("25"),),
    "HU": (Decimal("27"),),
    "IT": (Decimal("22"),),
    "LT": (Decimal("21"),),
    "LU": (Decimal("17"),),
    "LV": (Decimal("21"),),
    "NL": (Decimal("21"),),
    # Harvested dual entry — PL diesel 8% next to the 23% standard (§2.1a).
    "PL": (Decimal("23"), Decimal("8")),
    "PT": (Decimal("23"),),
    "RO": (Decimal("19"),),
    "SE": (Decimal("25"),),
    "SI": (Decimal("22"),),
    "SK": (Decimal("23"),),
}
if len(VAT_RATES) != 23:  # pragma: no cover - table-integrity guard, not a runtime path
    raise RuntimeError("the harvested coherence table must cover exactly 23 countries")

# Rule 3's country set IS the coherence table's key set — one 23-country
# list, never two drifting copies.
COUNTRIES = frozenset(VAT_RATES)

# Rule 2 — the literal shape check the source applied ("date matches
# YYYY-MM-DD; empty passes"). Deliberately a SHAPE check, not a calendar
# check (it is a warn — the typed parser layer is what refuses a truly
# unparseable date at error level, before this gate ever runs).
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class CapturedLine:
    """One captured line as RAW document-shaped strings — see the module
    docstring for why this is not `ParsedFuelLine`. `line_seq` is display
    provenance for findings (1-based source position), never a rule input.
    """

    invoice_no: str
    date: str
    country: str
    net: str
    vat: str
    line_seq: int | None = None


@dataclass(frozen=True)
class Finding:
    """One rule verdict for one line. `severity` is `warn` or `error`
    (an ok line emits no Finding). The `{severity, code, message}` shape
    mirrors the platform's persisted-finding convention
    (`app/schemas/validation.py::ValidationFinding`) without importing the
    AP domain's type (ADR-P3)."""

    severity: Literal["warn", "error"]
    code: str
    message: str
    line_seq: int | None = None


@dataclass(frozen=True)
class BatchTie:
    """The batch tie-out verdict vs the invoice coversheet total. Both
    totals are stored ALREADY QUANTIZED (q2) — the comparison the `ok`
    flag records is exactly `diff <= TIE_TOLERANCE` on those Decimals."""

    ok: bool
    computed_total: Decimal
    coversheet_total: Decimal
    diff: Decimal


@dataclass(frozen=True)
class BatchReview:
    """`validate_batch`'s result — the review surface's whole verdict."""

    findings: tuple[Finding, ...]
    tie: BatchTie | None

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity == ERROR)

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity == WARN)

    @property
    def can_commit(self) -> bool:
        """Harvested verbatim (§2.1a): `can_commit = (errors == 0) and
        (tie is None or tie.ok)`. Warnings NEVER block."""
        return self.errors == 0 and (self.tie is None or self.tie.ok)


def _parse_amount(raw: str) -> Decimal | None:
    """`None` = does not parse as a number (rule 4). NaN/Infinity are
    lexically valid `Decimal` inputs but are NOT numbers a money field may
    carry — refused here rather than poisoning every later comparison."""
    try:
        value = Decimal(raw.strip())
    except (InvalidOperation, ValueError, ArithmeticError):
        return None
    if not value.is_finite():
        return None
    return value


def check_line(line: CapturedLine) -> list[Finding]:
    """The nine harvested rules, in order (§2.1a). Rule 4 early-returns —
    every later rule needs the parsed numbers. Fails CLOSED at the batch
    level (an `error` here blocks registration via `can_commit`), but each
    individual rule's severity is exactly the harvested one — softening an
    error or hardening a warn would change which batches a human can
    register, i.e. invent policy."""
    findings: list[Finding] = []
    seq = line.line_seq

    # 1 — invoice number present (error): a batch that cannot name the
    # invoice document it came from is not registrable evidence.
    if not line.invoice_no.strip():
        findings.append(Finding(ERROR, "invoice_number_missing", "invoice number is missing", seq))

    # 2 — date shape YYYY-MM-DD; empty passes (warn).
    if line.date.strip() and not _DATE_RE.match(line.date.strip()):
        findings.append(Finding(WARN, "date_format", f"date '{line.date}' is not YYYY-MM-DD", seq))

    # 3 — country in the 23-country set (warn).
    country = line.country.strip().upper()
    if country not in COUNTRIES:
        findings.append(
            Finding(
                WARN,
                "country_unknown",
                f"country '{line.country}' is not in the 23-country set",
                seq,
            )
        )

    # 4 — net/vat parse as numbers (error + EARLY RETURN).
    net = _parse_amount(line.net)
    vat = _parse_amount(line.vat)
    if net is None or vat is None:
        which = "net" if net is None else "vat"
        raw = line.net if net is None else line.vat
        findings.append(
            Finding(ERROR, "amount_unparseable", f"{which} '{raw}' does not parse as a number", seq)
        )
        return findings

    # 5 — net > 0 (error).
    if net <= 0:
        findings.append(Finding(ERROR, "net_not_positive", f"net {net} must be > 0", seq))

    # 6 — vat >= 0 (error).
    if vat < 0:
        findings.append(Finding(ERROR, "vat_negative", f"vat {vat} must be >= 0", seq))

    # 7 — vat <= net, i.e. an implied VAT rate over 100% (error).
    if vat > net:
        findings.append(Finding(ERROR, "vat_exceeds_net", f"vat {vat} exceeds net {net}", seq))

    # 8 — net plausibility ceiling (warn).
    if net > NET_PLAUSIBILITY_CEILING:
        findings.append(
            Finding(
                WARN, "net_implausibly_large", f"net {net} exceeds {NET_PLAUSIBILITY_CEILING}", seq
            )
        )

    # 9 — VAT-rate coherence (warn): vat/net within ±0.5pp of a known rate
    # for the country. `vat == 0` deliberately SKIPPED (domestic/exempt is
    # validated elsewhere); an unknown country deliberately SKIPPED (no
    # known rate to cohere with — see module docstring). Only meaningful
    # over a positive net (rule 5 already erred otherwise).
    if vat != 0 and net > 0 and country in VAT_RATES:
        implied_pct = vat / net * Decimal(100)
        known = VAT_RATES[country]
        if not any(abs(implied_pct - rate) <= RATE_COHERENCE_PP for rate in known):
            rates = "/".join(str(r) for r in known)
            findings.append(
                Finding(
                    WARN,
                    "vat_rate_incoherent",
                    f"implied VAT rate {implied_pct.quantize(Decimal('0.01'))}% is not "
                    f"within {RATE_COHERENCE_PP}pp of a known {country} rate ({rates}%)",
                    seq,
                )
            )

    return findings


def validate_batch(
    lines: Sequence[CapturedLine], coversheet_total: Decimal | None = None
) -> BatchReview:
    """Run `check_line` over the whole batch and (when a coversheet total
    was captured) the batch tie-out. The tie sum covers every line whose
    amounts parse — a line that does not parse already carries a rule-4
    ERROR, so `can_commit` is False regardless of what the partial sum
    says; the tie result is still computed over the parseable remainder so
    the review surface can show BOTH problems at once, mirroring the
    engine tie-out's "the operator sees all failures at once" discipline.
    """
    findings: list[Finding] = []
    total = Decimal(0)
    for line in lines:
        findings.extend(check_line(line))
        net = _parse_amount(line.net)
        vat = _parse_amount(line.vat)
        if net is not None and vat is not None:
            total += net + vat

    tie: BatchTie | None = None
    if coversheet_total is not None:
        computed = q2(total)
        stated = q2(coversheet_total)
        diff = abs(computed - stated)
        tie = BatchTie(
            ok=diff <= TIE_TOLERANCE, computed_total=computed, coversheet_total=stated, diff=diff
        )

    return BatchReview(findings=tuple(findings), tie=tie)


def review_statement(
    parsed: ParsedStatement, coversheet_total: Decimal | None = None
) -> BatchReview:
    """Adapt a typed `ParsedStatement` into the raw captured-line shape and
    review it — the ONE rule set serving both the future review screen and
    `statement_ingest`'s registration gate (see module docstring). The
    typed-to-string mapping is lossless for these rules: `Decimal.__str__`
    round-trips exactly, `date.isoformat()` is the rule-2 shape."""
    captured = [
        CapturedLine(
            invoice_no=line.invoice_ref or "",
            date=line.txn_date.isoformat(),
            country=line.country,
            net=str(line.net_local),
            vat=str(line.vat_local),
            line_seq=line.line_seq,
        )
        for line in parsed.lines
    ]
    return validate_batch(captured, coversheet_total=coversheet_total)
