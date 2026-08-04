"""The anti-drift extraction baseline + `regression_check` (G3.3 slice 2 /
WO-70; `BA_fleet_fuel.md` §2.1a: *"`extraction_baseline` records a
confirmed extraction as known-good; `regression_check` flags a drift when
a re-extraction moves net or vat by more than 0.02"*; Appendix B
"VALIDATION": *"Regression drift: > 0.02 on net or vat"*).

WHAT PROBLEM THIS SOLVES (why WO-50's no-op semantics make it urgent)
------------------------------------------------------------------------
`fuel_ingest.ingest_transaction` is deliberately insert-or-no-op on the
natural key — so re-ingesting a statement AFTER a parser edit whose
output drifted writes nothing and warns nobody: the stored rows keep
their old figures, the new parse's divergent figures vanish, and no
surface records that the extractor no longer reproduces the numbers a
claim was built from. The baseline is the automatic memory of every
statement that ever registered; the drift flag is the ONLY visibility of
that divergence. The tie-out (WO-66) is no substitute: it checks only
where a human typed an expectation for that (supplier, period); the
baseline covers every confirmed statement with zero typing.

ADVISORY, NEVER A GATE (the harvested verb is "flags")
--------------------------------------------------------
The spec's two R25 regimes "block" registration and "halt" the close;
the anti-drift sentence says "flags a drift" — and WO-66 records that
anti-drift is NOT part of R25's acceptance. Master-context §4.19's
advisory semantics therefore apply: a drift finding never blocks a
registration, never mutates a figure, never gates a close; nothing
self-adjusts. A human decides: fix the parser, or `rebaseline` (an
explicit, audited act) when the NEW figures are the corrected truth.

THE THRESHOLD IS STRICTLY GREATER THAN 0.02
----------------------------------------------
"moves … by MORE than 0.02" ⇒ `abs(q2(new) − q2(old)) >
DRIFT_TOLERANCE` flags; a move of exactly 0.02 does not — the mirror of
the batch tie's `<= 0.02` allowed. Both sides are quantized (`q2`,
ROUND_HALF_UP) before the comparison so a diff sitting exactly on the
boundary never flips on representation noise (the same discipline as
`capture_review`'s batch tie and `tie_out`'s metric checks — §4.9).
`line_count` compares EXACTLY (any difference flags — a documented
interpretation: a split/merged line can drift compensatingly with equal
totals, and a dropped/duplicated line is T-6's worst failure). A
currency present on one side and absent from the other flags too — the
per-currency row SET is part of what was known-good.

FAIL-OPEN BY ABSENCE, LIKE THE TIE-OUT
-----------------------------------------
A statement digest with no baseline rows is simply not checked
(`regression_check` returns `baselined=False`, no findings, no error) —
the baseline exists only for a statement that actually REGISTERED (the
confirm act; a capture-gate-blocked or FX-failed statement leaves no
baseline). `remove_baseline` restores that state per digest — the
narrow mitigation for a noisy baseline, same posture as WO-66's
expectation removal.

WHY `rebaseline` RE-PARSES INSTEAD OF ACCEPTING TYPED FIGURES
----------------------------------------------------------------
The tie-out's figures are human-typed BECAUSE independence from the
parser is its point. The baseline's figures are BY DEFINITION the
parser's own output — so `rebaseline` re-parses the supplied bytes and
replaces the stored row set (audited old→new), keeping "the baseline is
what the parser said at confirm time" true by construction. It refuses
a digest that was never baselined: registration is the only act that
creates a baseline.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionError, ValidationError
from app.core.money import q2
from app.models.transport.extraction_baseline import FuelExtractionBaseline
from app.services import audit, modules
from app.services.transport import fuel_card_parser
from app.services.transport.fuel_card_parser import ParsedStatement

# The harvested drift threshold — STRICTLY greater than flags (see the
# module docstring).
DRIFT_TOLERANCE = Decimal("0.02")


@dataclass(frozen=True)
class CurrencyAggregate:
    """One currency's extraction aggregate: exact line count + q2'd
    Decimal totals of the parsed LOCAL-currency net and vat."""

    line_count: int
    net_total: Decimal
    vat_total: Decimal


@dataclass(frozen=True)
class DriftFinding:
    """One flagged drift. `baseline`/`actual` are on the metric's own
    basis (int for `lines`; q2'd Decimal for `net`/`vat`; None for a
    currency missing from one side)."""

    currency: str
    metric: str  # "lines" | "net" | "vat" | "currency"
    baseline: Decimal | int | None
    actual: Decimal | int | None
    message: str


@dataclass(frozen=True)
class RegressionCheckResult:
    """What `regression_check` returns. `baselined=False` means the
    digest was never confirmed — not checked, not an error (fail-open
    by absence)."""

    statement_sha256: str
    network: str
    baselined: bool
    findings: tuple[DriftFinding, ...]

    @property
    def drifted(self) -> bool:
        return bool(self.findings)


def digest(content: bytes) -> str:
    """The statement bytes' identity — hex SHA-256."""
    return hashlib.sha256(content).hexdigest()


def aggregate(parsed: ParsedStatement) -> dict[str, CurrencyAggregate]:
    """Per-currency extraction aggregates over the PARSED, pre-FX,
    local-currency figures. Decimal sums over the unrounded parsed
    values with `q2` applied to the SUM (the `capture_review` batch-tie
    discipline: quantize the total, not the addends). Never sums across
    currencies (§4.14)."""
    counts: dict[str, int] = {}
    nets: dict[str, Decimal] = {}
    vats: dict[str, Decimal] = {}
    for line in parsed.lines:
        cur = line.currency
        counts[cur] = counts.get(cur, 0) + 1
        nets[cur] = nets.get(cur, Decimal(0)) + line.net_local
        vats[cur] = vats.get(cur, Decimal(0)) + line.vat_local
    return {
        cur: CurrencyAggregate(
            line_count=counts[cur], net_total=q2(nets[cur]), vat_total=q2(vats[cur])
        )
        for cur in counts
    }


def compare(
    baselines: list[FuelExtractionBaseline], actual: dict[str, CurrencyAggregate]
) -> tuple[DriftFinding, ...]:
    """Pure drift comparison — no DB, no clock. Empty tuple = no drift.
    Money metrics flag STRICTLY beyond DRIFT_TOLERANCE on q2'd Decimals;
    `lines` flags on ANY difference; a currency on one side only flags
    as metric "currency" (see the module docstring)."""
    findings: list[DriftFinding] = []
    by_currency = {b.currency: b for b in baselines}

    for cur in sorted(set(by_currency) | set(actual)):
        base = by_currency.get(cur)
        agg = actual.get(cur)
        if base is None:
            # agg cannot be None here: cur came from one of the two sets.
            assert agg is not None
            findings.append(
                DriftFinding(
                    currency=cur,
                    metric="currency",
                    baseline=None,
                    actual=agg.line_count,
                    message=(
                        f"currency {cur} appears in the re-extraction "
                        f"({agg.line_count} lines) but not in the baseline"
                    ),
                )
            )
            continue
        if agg is None:
            findings.append(
                DriftFinding(
                    currency=cur,
                    metric="currency",
                    baseline=base.line_count,
                    actual=None,
                    message=(
                        f"currency {cur} is in the baseline ({base.line_count} lines) "
                        "but absent from the re-extraction"
                    ),
                )
            )
            continue

        if agg.line_count != base.line_count:
            findings.append(
                DriftFinding(
                    currency=cur,
                    metric="lines",
                    baseline=base.line_count,
                    actual=agg.line_count,
                    message=(f"{cur} line count moved from {base.line_count} to {agg.line_count}"),
                )
            )
        for metric, base_value, actual_value in (
            ("net", q2(Decimal(base.net_total)), agg.net_total),
            ("vat", q2(Decimal(base.vat_total)), agg.vat_total),
        ):
            if abs(actual_value - base_value) > DRIFT_TOLERANCE:
                findings.append(
                    DriftFinding(
                        currency=cur,
                        metric=metric,
                        baseline=base_value,
                        actual=actual_value,
                        message=(
                            f"{cur} {metric} moved from {base_value} to {actual_value} "
                            f"(more than {DRIFT_TOLERANCE})"
                        ),
                    )
                )
    return tuple(findings)


def _require_module_enabled(m_enabled: bool) -> None:
    if not m_enabled:
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")


async def get_baselines(
    db: AsyncSession, org_id: str, statement_sha256: str
) -> list[FuelExtractionBaseline]:
    """The digest's baseline row set for this org (empty = never
    confirmed here). Org-scoped per query (§4.1) — the same bytes in two
    orgs baseline independently."""
    rows = await db.scalars(
        select(FuelExtractionBaseline)
        .where(
            FuelExtractionBaseline.org_id == org_id,
            FuelExtractionBaseline.statement_sha256 == statement_sha256,
        )
        .order_by(FuelExtractionBaseline.currency)
    )
    return list(rows.all())


def _snapshot(rows: list[FuelExtractionBaseline]) -> dict:
    """The audited old/new value set, per currency (Decimal stringified —
    the audit meta is JSON)."""
    return {
        r.currency: {
            "lines": r.line_count,
            "net": str(r.net_total),
            "vat": str(r.vat_total),
        }
        for r in rows
    }


async def record(
    db: AsyncSession,
    org_id: str,
    parsed: ParsedStatement,
    *,
    statement_sha256: str,
    filename: str,
) -> list[FuelExtractionBaseline]:
    """The confirm-time FIRST record — called only from
    `statement_ingest.ingest_statement` after the whole registration
    succeeded (capture gate passed, FX resolved, rows written). Assumes
    the caller already holds the module gate and that no baseline rows
    exist for (org, digest) — `ingest_statement` checks both; a racing
    duplicate is refused by the unique key at flush. One audit event per
    statement (the statement is the operation's natural grain, unlike the
    tie-out's per-row human typing)."""
    created = [
        FuelExtractionBaseline(
            org_id=org_id,
            statement_sha256=statement_sha256,
            currency=cur,
            network=parsed.network,
            filename=filename,
            line_count=agg.line_count,
            net_total=agg.net_total,
            vat_total=agg.vat_total,
        )
        for cur, agg in sorted(aggregate(parsed).items())
    ]
    for row in created:
        db.add(row)
    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_EXTRACTION_BASELINE_SET,
        target_type="fuel_extraction_baseline",
        target_id=statement_sha256,
        meta={
            "sha256": statement_sha256,
            "network": parsed.network,
            "filename": filename,
            "old": None,
            "new": _snapshot(created),
        },
        org_id=org_id,  # explicit — callable outside an HTTP request context.
    )
    return created


async def regression_check(
    db: AsyncSession, org_id: str, *, filename: str, content: bytes
) -> RegressionCheckResult:
    """The explicit re-extraction caller: parse the supplied bytes
    through the deterministic registry and compare against the stored
    baseline. READ-ONLY — records nothing, mutates nothing (a pure check
    a route or CI-style sweep can call repeatedly). An unrecognized or
    malformed statement refuses exactly like `ingest_statement`."""
    m_enabled = await modules.is_enabled(db, org_id, "transport")
    _require_module_enabled(m_enabled)
    try:
        parsed = fuel_card_parser.run(filename, content)
    except ValueError as exc:
        raise ValidationError(str(exc), code="unrecognized_fuel_card_statement") from exc

    sha = digest(content)
    baselines = await get_baselines(db, org_id, sha)
    if not baselines:
        return RegressionCheckResult(
            statement_sha256=sha, network=parsed.network, baselined=False, findings=()
        )
    return RegressionCheckResult(
        statement_sha256=sha,
        network=parsed.network,
        baselined=True,
        findings=compare(baselines, aggregate(parsed)),
    )


async def rebaseline(
    db: AsyncSession, org_id: str, *, filename: str, content: bytes
) -> list[FuelExtractionBaseline]:
    """The ONLY overwrite path — the explicit, audited human act of
    accepting a re-parse's figures as the new known-good (see the module
    docstring for why it re-parses instead of accepting typed figures).
    Refuses a digest that was never baselined (`baseline_not_found`) —
    registration is the only act that CREATES a baseline; opaque across
    tenants (§4.4: a foreign org's digest looks nonexistent)."""
    m_enabled = await modules.is_enabled(db, org_id, "transport")
    _require_module_enabled(m_enabled)
    try:
        parsed = fuel_card_parser.run(filename, content)
    except ValueError as exc:
        raise ValidationError(str(exc), code="unrecognized_fuel_card_statement") from exc

    sha = digest(content)
    existing = await get_baselines(db, org_id, sha)
    if not existing:
        raise NotFoundError("Baseline not found", code="baseline_not_found")
    old = _snapshot(existing)

    by_currency = {r.currency: r for r in existing}
    aggregates = aggregate(parsed)
    kept: list[FuelExtractionBaseline] = []
    for cur, agg in sorted(aggregates.items()):
        row = by_currency.pop(cur, None)
        if row is None:
            row = FuelExtractionBaseline(
                org_id=org_id,
                statement_sha256=sha,
                currency=cur,
                network=parsed.network,
                filename=filename,
                line_count=agg.line_count,
                net_total=agg.net_total,
                vat_total=agg.vat_total,
            )
            db.add(row)
        else:
            row.network = parsed.network
            row.filename = filename
            row.line_count = agg.line_count
            row.net_total = agg.net_total
            row.vat_total = agg.vat_total
        kept.append(row)
    for vanished in by_currency.values():
        await db.delete(vanished)
    await db.flush()

    await audit.record(
        db,
        audit.A.TRANSPORT_EXTRACTION_BASELINE_SET,
        target_type="fuel_extraction_baseline",
        target_id=sha,
        meta={
            "sha256": sha,
            "network": parsed.network,
            "filename": filename,
            "old": old,
            "new": _snapshot(kept),
        },
        org_id=org_id,  # explicit — callable outside an HTTP request context.
    )
    return kept


async def remove_baseline(db: AsyncSession, org_id: str, *, statement_sha256: str) -> None:
    """Delete a digest's baseline row set (the narrow fail-open
    mitigation: that statement is then simply unchecked, exactly like a
    statement registered before this order shipped). Opaque absence —
    a foreign org's digest looks nonexistent (§4.4)."""
    m_enabled = await modules.is_enabled(db, org_id, "transport")
    _require_module_enabled(m_enabled)
    existing = await get_baselines(db, org_id, statement_sha256)
    if not existing:
        raise NotFoundError("Baseline not found", code="baseline_not_found")
    old = _snapshot(existing)
    for row in existing:
        await db.delete(row)
    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_EXTRACTION_BASELINE_REMOVE,
        target_type="fuel_extraction_baseline",
        target_id=statement_sha256,
        meta={"sha256": statement_sha256, "old": old, "new": None},
        org_id=org_id,  # explicit — callable outside an HTTP request context.
    )
