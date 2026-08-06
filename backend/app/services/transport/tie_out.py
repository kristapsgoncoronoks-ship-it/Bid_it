"""The engine tie-out (G3.3 / WO-66; `BA_fleet_fuel.md` §2.1a, R25) — the
SECOND of the two independent validation regimes: *"does what we parsed
equal what the invoice PDF says?"*

HOW THE TWO REGIMES DIVIDE THE WORK (R25)
--------------------------------------------
`capture_review` blocks the REGISTRATION of one incoherent batch; THIS
module halts the monthly CLOSE when the period's captured aggregate
disagrees with the figures a human typed from the supplier's invoice PDF
(`FuelTieOutExpectation`). They stay structurally separate — the
harvested source's own architecture note (§2.1a: "Two separate regimes")
— because they catch different failure classes: a coherent-looking batch
can still be one line short of the invoice (only the tie-out sees that),
and a batch matching its coversheet can still carry VAT > net on a line
(only the review gate sees that).

FAIL-OPEN BY ABSENCE, FAIL-CLOSED ONCE TYPED
-----------------------------------------------
A (entity, supplier, period, currency) with NO expectation row is not
checked — the expectation IS the per-supplier opt-in training target
(§5.1's onboarding contract; spec open question 16). Once a row is typed,
every comparison fails CLOSED: any mismatch beyond its tolerance halts
the whole close. The close evaluates EVERY expectation row before
raising ONE error naming ALL failures — the harvested "exit AFTER
processing every supplier so the operator sees all failures at once".

WHY AGGREGATION HAPPENS IN PYTHON, IN Decimal
------------------------------------------------
`check_period` selects the matching rows' columns and sums with
`Decimal` — never a SQL SUM whose return coercion differs by dialect
(SQLite float vs Postgres Numeric), never float (master-context §4.9).
Money comparisons quantize BOTH sides (`q2`, ROUND_HALF_UP) before the
tolerance test so a diff on the exact boundary never flips on
representation noise — the same discipline as `capture_review`'s batch
tie. Litres compare at their own 3-dp basis (litres are not money —
`fuel_transactions.qty`'s own carve-out).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionError, ValidationError
from app.core.money import q2
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.tie_out import FuelTieOutExpectation
from app.services import audit, issuer, modules

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

# Harvested tolerances (§2.1a / Appendix B "VALIDATION"): lines EXACT,
# always; the EUR metrics 0.05; gross_local per-row within [0.02, 0.05].
# LITRES_TOLERANCE reuses the 0.05 figure — the source names the
# diesel-litres metric but no tolerance; documented interpretation (see
# the model's docstring).
EUR_TOLERANCE = Decimal("0.05")
LITRES_TOLERANCE = Decimal("0.05")
GROSS_TOLERANCE_MIN = Decimal("0.02")
GROSS_TOLERANCE_MAX = Decimal("0.05")


@dataclass(frozen=True)
class MetricCheck:
    """One metric's comparison. `expected`/`actual` are Decimals on the
    metric's own basis (already quantized for money; 3-dp for litres;
    integral for lines). `tolerance` 0 means EXACT."""

    metric: str
    expected: Decimal
    actual: Decimal
    tolerance: Decimal
    ok: bool


@dataclass(frozen=True)
class TieOutResult:
    """One expectation row's verdict for the period."""

    entity_id: str
    supplier: str
    period: str
    currency: str
    checks: tuple[MetricCheck, ...]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def failures(self) -> tuple[MetricCheck, ...]:
        return tuple(c for c in self.checks if not c.ok)


def _require_module_enabled(m_enabled: bool) -> None:
    if not m_enabled:
        m = modules.MODULES_BY_KEY["transport"]
        raise PermissionError(f"The {m.name} module is not activated.", code="module_not_enabled")


def _validate_period(period: str) -> None:
    if not _PERIOD_RE.match(period):
        raise ValidationError(
            f"'{period}' is not a valid period — use YYYY-MM", code="invalid_period"
        )


async def _get_row(
    db: AsyncSession, org_id: str, entity_id: str, supplier: str, period: str, currency: str
) -> FuelTieOutExpectation | None:
    return await db.scalar(
        select(FuelTieOutExpectation).where(
            FuelTieOutExpectation.org_id == org_id,
            FuelTieOutExpectation.entity_id == entity_id,
            FuelTieOutExpectation.supplier == supplier,
            FuelTieOutExpectation.period == period,
            FuelTieOutExpectation.currency == currency,
        )
    )


def _snapshot(row: FuelTieOutExpectation) -> dict:
    """The audited old/new value set — every typed figure, stringified
    (Decimal is not JSON-serializable; the audit meta is JSON)."""
    return {
        "expected_lines": row.expected_lines,
        "expected_gross_local": (
            str(row.expected_gross_local) if row.expected_gross_local is not None else None
        ),
        "gross_local_tolerance": str(row.gross_local_tolerance),
        "expected_net_eur": str(row.expected_net_eur) if row.expected_net_eur is not None else None,
        "expected_gross_eur": (
            str(row.expected_gross_eur) if row.expected_gross_eur is not None else None
        ),
        "expected_diesel_litres": (
            str(row.expected_diesel_litres) if row.expected_diesel_litres is not None else None
        ),
    }


async def set_expectation(
    db: AsyncSession,
    org_id: str,
    *,
    entity_id: str,
    supplier: str,
    period: str,
    currency: str,
    expected_lines: int,
    expected_gross_local: Decimal | None = None,
    gross_local_tolerance: Decimal = GROSS_TOLERANCE_MIN,
    expected_net_eur: Decimal | None = None,
    expected_gross_eur: Decimal | None = None,
    expected_diesel_litres: Decimal | None = None,
) -> FuelTieOutExpectation:
    """Create or overwrite the (entity, supplier, period, currency)
    expectation — an UPSERT, because the figures are typed by a human from
    the invoice PDF and a typo must be correctable by simply retyping
    (the audit trail keeps old->new; the row itself is not append-only).
    Money inputs are quantized here (q2, ROUND_HALF_UP) — the service is
    the single point that rounds; litres quantize at their own 3 dp."""
    m_enabled = await modules.is_enabled(db, org_id, "transport")
    _require_module_enabled(m_enabled)
    _validate_period(period)
    currency = currency.upper()
    if not _CURRENCY_RE.match(currency):
        raise ValidationError(
            f"'{currency}' is not a valid ISO 4217 currency code", code="invalid_currency"
        )
    if expected_lines < 0:
        raise ValidationError("expected_lines must be >= 0", code="invalid_expected_lines")
    if not (GROSS_TOLERANCE_MIN <= gross_local_tolerance <= GROSS_TOLERANCE_MAX):
        raise ValidationError(
            f"gross_local_tolerance must be within [{GROSS_TOLERANCE_MIN}, "
            f"{GROSS_TOLERANCE_MAX}] (the harvested band)",
            code="invalid_gross_tolerance",
        )
    # Opaque cross-tenant discipline (§4.4): a foreign org's entity id is
    # indistinguishable from a nonexistent one.
    entity = await issuer.get_by_id(db, org_id, entity_id)
    if entity is None:
        raise NotFoundError("Entity not found", code="entity_not_found")

    values = {
        "expected_lines": expected_lines,
        "expected_gross_local": (
            q2(expected_gross_local) if expected_gross_local is not None else None
        ),
        "gross_local_tolerance": q2(gross_local_tolerance),
        "expected_net_eur": q2(expected_net_eur) if expected_net_eur is not None else None,
        "expected_gross_eur": q2(expected_gross_eur) if expected_gross_eur is not None else None,
        "expected_diesel_litres": (
            expected_diesel_litres.quantize(Decimal("0.001"))
            if expected_diesel_litres is not None
            else None
        ),
    }

    row = await _get_row(db, org_id, entity_id, supplier, period, currency)
    if row is None:
        row = FuelTieOutExpectation(
            org_id=org_id,
            entity_id=entity_id,
            supplier=supplier,
            period=period,
            currency=currency,
            **values,
        )
        db.add(row)
        old = None
    else:
        old = _snapshot(row)
        for key, value in values.items():
            setattr(row, key, value)

    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_TIEOUT_EXPECTATION_SET,
        target_type="fuel_tieout_expectation",
        target_id=row.id,
        meta={
            "supplier": supplier,
            "period": period,
            "currency": currency,
            "entity_id": entity_id,
            "old": old,
            "new": _snapshot(row),
        },
        org_id=org_id,  # explicit — callable outside an HTTP request context.
    )
    return row


async def list_expectations(
    db: AsyncSession, org_id: str, period: str
) -> list[FuelTieOutExpectation]:
    """Every expectation typed for (org, period), in the same
    (supplier, currency) order `check_period` evaluates them (WO-77 — the
    route-facing read; `check_period` computes VERDICTS, this returns the
    typed targets themselves). Module-gated + period-validated first, the
    transport-wide entry-point pattern (fails CLOSED, ADR-P3 rule 3)."""
    m_enabled = await modules.is_enabled(db, org_id, "transport")
    _require_module_enabled(m_enabled)
    _validate_period(period)
    return list(
        await db.scalars(
            select(FuelTieOutExpectation)
            .where(
                FuelTieOutExpectation.org_id == org_id,
                FuelTieOutExpectation.period == period,
            )
            .order_by(
                FuelTieOutExpectation.supplier,
                FuelTieOutExpectation.currency,
                FuelTieOutExpectation.entity_id,
            )
        )
    )


async def remove_expectation(
    db: AsyncSession,
    org_id: str,
    *,
    entity_id: str,
    supplier: str,
    period: str,
    currency: str,
) -> None:
    """Delete a typed expectation (the narrow un-halt for a mis-typed
    supplier: the close then treats that key as never-typed, fail-open by
    absence). Opaque absence — a foreign org's key looks nonexistent."""
    m_enabled = await modules.is_enabled(db, org_id, "transport")
    _require_module_enabled(m_enabled)
    _validate_period(period)
    currency = currency.upper()
    row = await _get_row(db, org_id, entity_id, supplier, period, currency)
    if row is None:
        raise NotFoundError("Expectation not found", code="tieout_expectation_not_found")
    old = _snapshot(row)
    row_id = row.id
    await db.delete(row)
    await db.flush()
    await audit.record(
        db,
        audit.A.TRANSPORT_TIEOUT_EXPECTATION_REMOVE,
        target_type="fuel_tieout_expectation",
        target_id=row_id,
        meta={
            "supplier": supplier,
            "period": period,
            "currency": currency,
            "entity_id": entity_id,
            "old": old,
            "new": None,
        },
        org_id=org_id,  # explicit — callable outside an HTTP request context.
    )


def _check_row(
    exp: FuelTieOutExpectation,
    actual_lines: int,
    actual_gross_local: Decimal,
    actual_net_eur: Decimal,
    actual_gross_eur: Decimal,
    actual_diesel_litres: Decimal,
) -> TieOutResult:
    checks: list[MetricCheck] = []

    # lines — tolerance 0, EXACT, always (harvested; never configurable).
    expected_lines = Decimal(exp.expected_lines)
    checks.append(
        MetricCheck(
            metric="lines",
            expected=expected_lines,
            actual=Decimal(actual_lines),
            tolerance=Decimal(0),
            ok=actual_lines == exp.expected_lines,
        )
    )

    def money_check(metric: str, expected: Decimal | None, actual: Decimal, tol: Decimal) -> None:
        if expected is None:
            return  # not typed from the PDF — not checked.
        e, a = q2(expected), q2(actual)
        checks.append(
            MetricCheck(metric=metric, expected=e, actual=a, tolerance=tol, ok=abs(a - e) <= tol)
        )

    money_check(
        "gross_local", exp.expected_gross_local, actual_gross_local, exp.gross_local_tolerance
    )
    money_check("net_eur", exp.expected_net_eur, actual_net_eur, EUR_TOLERANCE)
    money_check("gross_eur", exp.expected_gross_eur, actual_gross_eur, EUR_TOLERANCE)

    if exp.expected_diesel_litres is not None:
        e = exp.expected_diesel_litres.quantize(Decimal("0.001"))
        a = actual_diesel_litres.quantize(Decimal("0.001"))
        checks.append(
            MetricCheck(
                metric="diesel_litres",
                expected=e,
                actual=a,
                tolerance=LITRES_TOLERANCE,
                ok=abs(a - e) <= LITRES_TOLERANCE,
            )
        )

    return TieOutResult(
        entity_id=exp.entity_id,
        supplier=exp.supplier,
        period=exp.period,
        currency=exp.currency,
        checks=tuple(checks),
    )


async def check_period(db: AsyncSession, org_id: str, period: str) -> list[TieOutResult]:
    """Tie out EVERY expectation typed for (org, period) against the
    period's actual `fuel_transactions` — one result per expectation row,
    never short-circuiting on the first failure (the operator sees all
    failures at once; the CALLER decides whether any failure halts, which
    is exactly what `close.run_close` does). Aggregates are computed
    per-(entity, supplier, currency) — `gross_local` is never summed
    across currencies (§4.14)."""
    m_enabled = await modules.is_enabled(db, org_id, "transport")
    _require_module_enabled(m_enabled)
    _validate_period(period)

    expectations = (
        await db.scalars(
            select(FuelTieOutExpectation)
            .where(
                FuelTieOutExpectation.org_id == org_id,
                FuelTieOutExpectation.period == period,
            )
            .order_by(
                FuelTieOutExpectation.supplier,
                FuelTieOutExpectation.currency,
            )
        )
    ).all()
    if not expectations:
        return []

    results: list[TieOutResult] = []
    for exp in expectations:
        rows = (
            await db.execute(
                select(
                    FuelTransaction.qty,
                    FuelTransaction.product_group,
                    FuelTransaction.gross_local,
                    FuelTransaction.net_eur,
                    FuelTransaction.vat_eur,
                ).where(
                    FuelTransaction.org_id == org_id,
                    FuelTransaction.entity_id == exp.entity_id,
                    FuelTransaction.supplier == exp.supplier,
                    FuelTransaction.period == period,
                    FuelTransaction.currency == exp.currency,
                )
            )
        ).all()

        actual_lines = len(rows)
        gross_local = sum((Decimal(r.gross_local) for r in rows), Decimal(0))
        net_eur = sum((Decimal(r.net_eur) for r in rows), Decimal(0))
        gross_eur = sum((Decimal(r.net_eur) + Decimal(r.vat_eur) for r in rows), Decimal(0))
        diesel_litres = sum(
            (Decimal(r.qty) for r in rows if r.product_group == "Diesel"), Decimal(0)
        )

        results.append(
            _check_row(exp, actual_lines, gross_local, net_eur, gross_eur, diesel_litres)
        )

    return results


def describe_failures(results: list[TieOutResult]) -> str:
    """One human-readable line per failing metric across ALL suppliers —
    the message body of the close-halting error (the harvested "the
    operator sees all failures at once")."""
    parts: list[str] = []
    for r in results:
        for c in r.failures:
            parts.append(
                f"{r.supplier}/{r.currency} {c.metric}: expected {c.expected}, "
                f"got {c.actual} (tolerance {c.tolerance})"
            )
    return "; ".join(parts)
