"""Deterministic advisory post-capture checks (G3.4 / R26 — WO-71).

The harvested spec names this module verbatim (`BA_fleet_fuel.md` §3.I7:
"Deterministic post-capture checks (`capture_checks.py`), advisory only").
Four checks, nothing more:

1. IBAN — ISO 13616 structure + ISO 7064 MOD-97, severity ``error``.
   Composes `app.core.bank_id` (never reimplements). ONE documented
   divergence from that module's posture: `bank_id` fails CLOSED on an
   unknown country prefix because its callers move money (SEPA); here an
   unknown-country IBAN is UNCHECKABLE and yields NO finding — R26's
   "unknown or uncheckable inputs yield no finding". The SEPA path keeps
   its own fail-closed gate untouched.
2. VAT-ID — STRUCTURAL check only (character classes + length from the
   public VIES format table, EU-27). Registry allocation ranges are
   deliberately NOT modelled: they change by statute, and a false
   "malformed" on a real seller VAT is exactly the crying-wolf R26
   forbids. Severity ``warn``.
3. ``vies_check`` — the live EU VIES lookup is deliberately NOT done
   inline (rate-limited / frequently unavailable). This function performs
   NO I/O AT ALL — no HTTP client, no SOAP dependency — and ALWAYS
   returns ``not_checked``. It is offline-graceful by construction:
   structurally unreachable, never raises, never blocks. A future ONLINE
   VIES job would be new scope no R-rule requires — do not "finish" this
   stub inline.
4. Cross-entity invoice duplicates — "same normalized invoice number +
   amount, across ALL entities, not just this supplier+statement". A
   prior already-registered duplicate is ``error``; an in-batch repeat
   (two raw refs normalizing together) is ``warn``.

EVERY finding is ADVISORY (§4.19): this module contains no refusal path,
mutates nothing, and gates nothing. Findings are a DELIBERATELY DIFFERENT
type from `capture_review`'s — feeding one into the blocking lattice is a
type error, so an ``error``-SEVERITY advisory finding can never block
registration. Severity is reviewer triage rank, not a gate. Every path
fails toward NO finding (open) on unknown/uncheckable input — "fail
toward not crying wolf" (R26 verbatim).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bank_id import IBAN_LENGTHS, is_valid_iban, normalize_iban
from app.core.errors import PermissionError
from app.core.money import q2
from app.models.transport.fuel_transaction import FuelTransaction
from app.services import modules
from app.services.transport import queries
from app.services.transport.fuel_card_parser import ParsedFuelLine

ERROR = "error"
WARN = "warn"

NOT_CHECKED = "not_checked"


@dataclass(frozen=True)
class CheckFinding:
    """One advisory post-capture finding. Same `{severity, code, message}`
    convention as `capture_review.Finding`, deliberately a DIFFERENT type
    (see module docstring — mypy structurally prevents feeding an advisory
    finding into the blocking lattice)."""

    severity: str
    code: str
    message: str


# --------------------------------------------------------------------------
# 1. IBAN (composes core/bank_id — decision 4)
# --------------------------------------------------------------------------


def check_iban(value: str | None) -> CheckFinding | None:
    """MOD-97/structure advisory check. `None`/empty/unknown-country →
    no finding (uncheckable); a checkable IBAN that fails → ``error``."""
    if not value:
        return None
    iban = normalize_iban(value)
    if len(iban) < 2 or iban[:2] not in IBAN_LENGTHS:
        return None  # uncheckable country — no finding, never a guess
    if is_valid_iban(iban):
        return None
    return CheckFinding(
        severity=ERROR,
        code="iban_invalid",
        message=(
            f"IBAN '{value}' fails the ISO 13616/MOD-97 check — "
            "a captured payout account this shape cannot be real."
        ),
    )


# --------------------------------------------------------------------------
# 2. VAT-ID structure (public VIES format table, EU-27 — decision 5)
# --------------------------------------------------------------------------

# Character classes + length only. Allocation ranges deliberately absent.
_VAT_BODY: dict[str, re.Pattern[str]] = {
    cc: re.compile(pattern)
    for cc, pattern in {
        "AT": r"^U\d{8}$",
        "BE": r"^\d{10}$",
        "BG": r"^\d{9,10}$",
        "CY": r"^\d{8}[A-Z]$",
        "CZ": r"^\d{8,10}$",
        "DE": r"^\d{9}$",
        "DK": r"^\d{8}$",
        "EE": r"^\d{9}$",
        "ES": r"^[A-Z0-9]\d{7}[A-Z0-9]$",
        "FI": r"^\d{8}$",
        "FR": r"^[A-Z0-9]{2}\d{9}$",
        "GR": r"^\d{9}$",
        "HR": r"^\d{11}$",
        "HU": r"^\d{8}$",
        "IE": r"^(\d{7}[A-Z]{1,2}|\d[A-Z0-9+*]\d{5}[A-Z])$",
        "IT": r"^\d{11}$",
        "LT": r"^(\d{9}|\d{12})$",
        "LU": r"^\d{8}$",
        "LV": r"^\d{11}$",
        "MT": r"^\d{8}$",
        "NL": r"^\d{9}B\d{2}$",
        "PL": r"^\d{10}$",
        "PT": r"^\d{9}$",
        "RO": r"^\d{2,10}$",
        "SE": r"^\d{12}$",
        "SI": r"^\d{8}$",
        "SK": r"^\d{10}$",
    }.items()
}

# Prefix EL maps to Greece — the one documented exception, same table as
# `parsers/eurowag.py::_VAT_PREFIX_TO_COUNTRY`.
_VAT_PREFIX_TO_COUNTRY = {"EL": "GR"}


def check_vat_id(value: str | None) -> CheckFinding | None:
    """Per-country STRUCTURAL check. Unknown prefix (XI, CH, a non-country
    token) → no finding; a known-country VAT id whose body doesn't match
    the public VIES shape → ``warn`` (harvested severity, verbatim)."""
    if not value:
        return None
    vat = value.replace(" ", "").upper()
    prefix = vat[:2]
    if len(vat) < 3 or not prefix.isalpha():
        return None
    country = _VAT_PREFIX_TO_COUNTRY.get(prefix, prefix)
    pattern = _VAT_BODY.get(country)
    if pattern is None:
        return None  # not an EU-27 prefix we can check — no finding
    if pattern.match(vat[2:]):
        return None
    return CheckFinding(
        severity=WARN,
        code="vat_id_malformed",
        message=(
            f"VAT id '{value}' does not match the {country} structure from the "
            "public VIES format table — verify the captured seller identifier."
        ),
    )


# --------------------------------------------------------------------------
# 3. VIES — offline by construction (decision 6)
# --------------------------------------------------------------------------


def vies_check(vat_number: str | None = None) -> str:  # noqa: ARG001 - documented stub
    """ALWAYS returns ``not_checked``. Performs no I/O of any kind — the
    live VIES lookup is deliberately not done inline (see module
    docstring). Never raises, never blocks; a ``not_checked`` result
    yields no finding (un-verified is not a defect)."""
    return NOT_CHECKED


# --------------------------------------------------------------------------
# 4. Cross-entity invoice duplicates (decision 3)
# --------------------------------------------------------------------------


def _normalize_ref(ref: str) -> str:
    """Uppercase + strip ASCII spaces — deliberately NARROW (separators are
    kept: '2024/001' != '2024001') per "fail toward not crying wolf". The
    SQL filter applies the identical rule portably."""
    return ref.replace(" ", "").upper()


@dataclass(frozen=True)
class _Candidate:
    raw_ref: str
    normalized: str
    currency: str
    amount: Decimal  # q2(sum net_local + vat_local) over the invoice's lines


def _candidates(lines: list[ParsedFuelLine]) -> list[_Candidate]:
    """One candidate per distinct RAW `invoice_ref` per currency — invoice
    grain, never line grain. Lines without a ref are uncheckable."""
    sums: dict[tuple[str, str], Decimal] = {}
    for line in lines:
        if line.invoice_ref is None:
            continue
        key = (line.invoice_ref, line.currency)
        sums[key] = sums.get(key, Decimal("0")) + line.net_local + line.vat_local
    return [
        _Candidate(
            raw_ref=raw,
            normalized=_normalize_ref(raw),
            currency=currency,
            amount=q2(total),
        )
        for (raw, currency), total in sums.items()
    ]


async def find_duplicates(
    db: AsyncSession,
    org_id: str,
    *,
    entity_id: str,
    supplier: str,
    period: str,
    lines: list[ParsedFuelLine],
) -> list[CheckFinding]:
    """Prior cross-entity duplicate (``error``) + in-batch normalization
    collision (``warn``). Same org, ANY entity ("across ALL entities"),
    EXCLUDING the current `(entity, supplier, period)` ingestion group so
    an idempotent replay never self-flags. Equality is exact on
    `(normalized ref, currency, q2 amount)` — same ref with a different
    amount, or the same amount in a different currency, is NOT a
    duplicate."""
    findings: list[CheckFinding] = []
    candidates = _candidates(lines)
    if not candidates:
        return findings

    # In-batch repeat: two+ DISTINCT raw refs normalizing to the same
    # (normalized, currency) with equal q2 amounts.
    by_norm: dict[tuple[str, str], list[_Candidate]] = {}
    for cand in candidates:
        by_norm.setdefault((cand.normalized, cand.currency), []).append(cand)
    for (normalized, currency), group in sorted(by_norm.items()):
        raws = sorted({c.raw_ref for c in group})
        if len(raws) > 1 and len({c.amount for c in group}) == 1:
            findings.append(
                CheckFinding(
                    severity=WARN,
                    code="duplicate_in_batch",
                    message=(
                        f"invoice refs {raws} normalize to the same reference "
                        f"'{normalized}' with equal totals ({group[0].amount} "
                        f"{currency}) — the same invoice may be captured twice "
                        "in this batch."
                    ),
                )
            )

    # Prior duplicate: already-registered fuel_transactions in the SAME org
    # (any entity), grouped at invoice grain, normalized identically in SQL.
    normalized_refs = sorted({c.normalized for c in candidates})
    # ONE normalization expression for the projection, the filter and the
    # grouping — a second spelling of it would group differently from the way
    # it filters, which is why it lives in the registry beside the cut.
    norm_col = queries.NORMALIZED_INVOICE_REF
    rows = (
        await db.execute(
            queries.fuel_transactions_by_normalized_invoice_ref(org_id, normalized_refs)
            .with_only_columns(
                FuelTransaction.entity_id,
                FuelTransaction.supplier,
                FuelTransaction.period,
                norm_col.label("normalized"),
                FuelTransaction.currency,
                func.sum(FuelTransaction.net_local + FuelTransaction.vat_local).label("total"),
            )
            .group_by(
                FuelTransaction.entity_id,
                FuelTransaction.supplier,
                FuelTransaction.period,
                norm_col,
                FuelTransaction.currency,
            )
        )
    ).all()

    by_key: dict[tuple[str, str], list[tuple[str, str, str, Decimal]]] = {}
    for row in rows:
        # Exclude the CURRENT ingestion group — insert-or-no-op replays
        # re-see their own rows and must not self-flag.
        if row.entity_id == entity_id and row.supplier == supplier and row.period == period:
            continue
        by_key.setdefault((row.normalized, row.currency), []).append(
            (row.entity_id, row.supplier, row.period, q2(Decimal(str(row.total))))
        )

    for cand in sorted(candidates, key=lambda c: (c.normalized, c.currency)):
        for prior_entity, prior_supplier, prior_period, prior_total in by_key.get(
            (cand.normalized, cand.currency), []
        ):
            if prior_total == cand.amount:
                findings.append(
                    CheckFinding(
                        severity=ERROR,
                        code="duplicate_prior_registration",
                        message=(
                            f"invoice '{cand.raw_ref}' ({cand.amount} {cand.currency}) "
                            f"is already registered under entity {prior_entity} "
                            f"({prior_supplier}, {prior_period}) — claiming it again "
                            "would be a duplicate VAT-refund submission."
                        ),
                    )
                )
    return findings


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------


async def run_checks(
    db: AsyncSession,
    org_id: str,
    *,
    entity_id: str,
    supplier: str,
    period: str,
    lines: list[ParsedFuelLine],
    vat_ids: tuple[str, ...] = (),
    iban: str | None = None,
) -> list[CheckFinding]:
    """The R26 orchestrator: module-entitlement gate first (identical
    discipline to every transport service entry point), then the pure
    checks + the duplicate scan. Returns advisory findings only — the
    caller renders them as warnings; nothing here blocks anything."""
    if not await modules.is_enabled(db, org_id, "transport"):
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")

    findings: list[CheckFinding] = []
    finding = check_iban(iban)
    if finding is not None:
        findings.append(finding)
    for vat in vat_ids:
        finding = check_vat_id(vat)
        if finding is not None:
            findings.append(finding)
    findings.extend(
        await find_duplicates(
            db,
            org_id,
            entity_id=entity_id,
            supplier=supplier,
            period=period,
            lines=lines,
        )
    )
    return findings
