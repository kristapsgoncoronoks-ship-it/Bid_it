"""Expense spending policy (Phase 09 → expense-management build).

One configurable policy per org. `evaluate()` runs every enabled check over a
report's items and returns findings, each tagged `warn` or `block`.

Design rule (product requirement): suspicious expenses are FLAGGED for review,
never auto-rejected. A finding blocks submission ONLY when its rule code is listed
in the policy's `blocking_rules`; otherwise it is advisory. `blocking(findings)`
tells the submit gate whether any hard block fired.

Any cap/threshold left unset is not enforced. With no active policy there are no
findings at all.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.expense import ExpenseItem, ExpensePolicy, ExpenseReport

# Every rule code the engine can emit (stable identifiers used by `blocking_rules`
# and the UI). Order is the display/priority order.
RULE_CODES = (
    "over_item_max",
    "over_category_cap",
    "missing_receipt",
    "out_of_policy_category",
    "unsupported_currency",
    "mileage_rate",
    "missing_business_purpose",
    "late_submission",
    "weekend_spend",
    "duplicate_receipt",
    "duplicate_amount_date_merchant",
)


async def get(db: AsyncSession, org_id: str) -> ExpensePolicy | None:
    return await db.scalar(select(ExpensePolicy).where(ExpensePolicy.org_id == org_id))


def _json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return [str(x) for x in val] if isinstance(val, list) else []
    except (ValueError, TypeError):
        return []


def caps_of(policy: ExpensePolicy | None) -> dict[str, Decimal]:
    if policy is None or not policy.category_caps:
        return {}
    try:
        raw = json.loads(policy.category_caps)
        return {str(k): Decimal(str(v)) for k, v in raw.items()}
    except (ValueError, TypeError, AttributeError):
        return {}


def allowed_categories_of(policy: ExpensePolicy | None) -> list[str]:
    return _json_list(policy.allowed_categories) if policy else []


def allowed_currencies_of(policy: ExpensePolicy | None) -> list[str]:
    return [c.upper() for c in _json_list(policy.allowed_currencies)] if policy else []


def blocking_rules_of(policy: ExpensePolicy | None) -> list[str]:
    return _json_list(policy.blocking_rules) if policy else []


async def upsert(
    db: AsyncSession,
    org_id: str,
    *,
    active: bool = True,
    max_item_amount: Decimal | None = None,
    receipt_required_over: Decimal | None = None,
    category_caps: Mapping[str, str | Decimal] | None = None,
    allowed_categories: list[str] | None = None,
    allowed_currencies: list[str] | None = None,
    warn_weekend: bool = False,
    duplicate_detection: bool = True,
    mileage_rate: Decimal | None = None,
    mileage_rate_tolerance: Decimal | None = None,
    require_purpose_over: Decimal | None = None,
    late_submission_days: int | None = None,
    blocking_rules: list[str] | None = None,
) -> ExpensePolicy:
    """Create or replace the org's policy. Flushes; caller commits."""
    policy = await get(db, org_id)
    caps_json = json.dumps({k: str(v) for k, v in category_caps.items()}) if category_caps else None
    if policy is None:
        policy = ExpensePolicy(org_id=org_id)
        db.add(policy)
    policy.active = active
    policy.max_item_amount = max_item_amount
    policy.receipt_required_over = receipt_required_over
    policy.category_caps = caps_json
    policy.allowed_categories = json.dumps(allowed_categories) if allowed_categories else None
    policy.allowed_currencies = (
        json.dumps([c.upper() for c in allowed_currencies]) if allowed_currencies else None
    )
    policy.warn_weekend = warn_weekend
    policy.duplicate_detection = duplicate_detection
    policy.mileage_rate = mileage_rate
    policy.mileage_rate_tolerance = mileage_rate_tolerance
    policy.require_purpose_over = require_purpose_over
    policy.late_submission_days = late_submission_days
    # Only keep rule codes the engine actually knows about.
    valid = [c for c in (blocking_rules or []) if c in RULE_CODES]
    policy.blocking_rules = json.dumps(valid) if valid else None
    policy.version = (policy.version or 0) + 1  # None on a not-yet-flushed new row
    await db.flush()
    return policy


def _finding(code: str, blockers: set[str], message: str, **extra) -> dict:
    return {
        "code": code,
        "severity": "block" if code in blockers else "warn",
        "message": message,
        "item_id": extra.get("item_id"),
        "category": extra.get("category"),
        "amount": extra.get("amount"),
        "limit": extra.get("limit"),
    }


def evaluate(
    policy: ExpensePolicy | None,
    report: ExpenseReport,
    *,
    today: date | None = None,
) -> list[dict]:
    """Run every enabled check over the report's items. Returns findings tagged
    `warn`/`block`. Empty when there is no active policy."""
    if policy is None or not policy.active:
        return []

    items: list[ExpenseItem] = list(report.items)
    today = today or date.today()
    caps = caps_of(policy)
    allowed_cats = allowed_categories_of(policy)
    allowed_ccy = allowed_currencies_of(policy)
    blockers = set(blocking_rules_of(policy))
    report_ccy = (report.currency or "EUR").upper()
    out: list[dict] = []

    # ---- per-item checks ---------------------------------------------------- #
    for it in items:
        amount = Decimal(it.amount or 0)
        cat = it.category
        base = {"item_id": it.id, "category": cat, "amount": str(amount)}

        if policy.max_item_amount is not None and amount > Decimal(policy.max_item_amount):
            out.append(
                _finding(
                    "over_item_max",
                    blockers,
                    f"{amount} exceeds the per-item limit of {policy.max_item_amount}",
                    limit=str(policy.max_item_amount),
                    **base,
                )
            )
        cap = caps.get(cat)
        if cap is not None and amount > cap:
            out.append(
                _finding(
                    "over_category_cap",
                    blockers,
                    f"{amount} exceeds the {cat} cap of {cap}",
                    limit=str(cap),
                    **base,
                )
            )
        if (
            policy.receipt_required_over is not None
            and amount > Decimal(policy.receipt_required_over)
            and not it.receipt_sha256
            and not (it.missing_receipt_declaration and it.missing_receipt_declaration.strip())
        ):
            out.append(
                _finding(
                    "missing_receipt",
                    blockers,
                    f"a receipt (or a missing-receipt declaration) is required over "
                    f"{policy.receipt_required_over}",
                    limit=str(policy.receipt_required_over),
                    **base,
                )
            )
        if allowed_cats and cat not in allowed_cats:
            out.append(
                _finding(
                    "out_of_policy_category",
                    blockers,
                    f"'{cat}' is not an allowed expense category",
                    **base,
                )
            )
        item_ccy = (it.currency or report_ccy).upper()
        if allowed_ccy and item_ccy not in allowed_ccy:
            out.append(
                _finding(
                    "unsupported_currency",
                    blockers,
                    f"currency {item_ccy} is not accepted (allowed: {', '.join(allowed_ccy)})",
                    **base,
                )
            )
        if (
            it.expense_type == "mileage"
            and policy.mileage_rate is not None
            and it.mileage_rate is not None
        ):
            tol = Decimal(policy.mileage_rate_tolerance or 0)
            if abs(Decimal(it.mileage_rate) - Decimal(policy.mileage_rate)) > tol:
                out.append(
                    _finding(
                        "mileage_rate",
                        blockers,
                        f"mileage rate {it.mileage_rate} differs from the sanctioned "
                        f"{policy.mileage_rate}",
                        limit=str(policy.mileage_rate),
                        **base,
                    )
                )
        if (
            policy.require_purpose_over is not None
            and amount > Decimal(policy.require_purpose_over)
            and not (it.comment and it.comment.strip())
        ):
            out.append(
                _finding(
                    "missing_business_purpose",
                    blockers,
                    f"a business purpose is required over {policy.require_purpose_over}",
                    limit=str(policy.require_purpose_over),
                    **base,
                )
            )
        if policy.late_submission_days is not None and it.spend_date is not None:
            age = (today - it.spend_date).days
            if age > policy.late_submission_days:
                out.append(
                    _finding(
                        "late_submission",
                        blockers,
                        f"spend is {age} days old (limit {policy.late_submission_days})",
                        **base,
                    )
                )
        if policy.warn_weekend and it.spend_date is not None and it.spend_date.weekday() >= 5:
            out.append(
                _finding(
                    "weekend_spend",
                    blockers,
                    f"spend dated on a weekend ({it.spend_date.isoformat()})",
                    **base,
                )
            )

    # ---- cross-item duplicate detection ------------------------------------ #
    if policy.duplicate_detection:
        by_receipt: dict[str, list[ExpenseItem]] = defaultdict(list)
        by_triple: dict[tuple, list[ExpenseItem]] = defaultdict(list)
        for it in items:
            if it.receipt_sha256:
                by_receipt[it.receipt_sha256].append(it)
            triple = (
                Decimal(it.amount or 0),
                it.spend_date,
                (it.merchant or "").strip().lower(),
            )
            if triple[2]:  # only when a merchant is present
                by_triple[triple].append(it)
        for group in by_receipt.values():
            if len(group) > 1:
                for it in group:
                    out.append(
                        _finding(
                            "duplicate_receipt",
                            blockers,
                            "the same receipt is attached to more than one item",
                            item_id=it.id,
                            category=it.category,
                            amount=str(Decimal(it.amount or 0)),
                        )
                    )
        for group in by_triple.values():
            if len(group) > 1:
                for it in group:
                    out.append(
                        _finding(
                            "duplicate_amount_date_merchant",
                            blockers,
                            f"same amount + date + merchant as {len(group) - 1} other item(s)",
                            item_id=it.id,
                            category=it.category,
                            amount=str(Decimal(it.amount or 0)),
                        )
                    )
    return out


def blocking(findings: list[dict]) -> list[dict]:
    """The subset of findings that hard-block submission."""
    return [f for f in findings if f.get("severity") == "block"]


def violations(policy: ExpensePolicy | None, items: list[ExpenseItem]) -> list[dict]:
    """Backward-compatible item-only view (the original three caps). Prefer
    `evaluate(policy, report)` — it runs the full configurable rule set."""
    if policy is None or not policy.active:
        return []
    blockers = set(blocking_rules_of(policy))
    caps = caps_of(policy)
    out: list[dict] = []
    for it in items:
        amount = Decimal(it.amount or 0)
        base = {"item_id": it.id, "category": it.category, "amount": str(amount)}
        if policy.max_item_amount is not None and amount > Decimal(policy.max_item_amount):
            out.append(
                _finding(
                    "over_item_max",
                    blockers,
                    f"{amount} exceeds the per-item limit of {policy.max_item_amount}",
                    limit=str(policy.max_item_amount),
                    **base,
                )
            )
        cap = caps.get(it.category)
        if cap is not None and amount > cap:
            out.append(
                _finding(
                    "over_category_cap",
                    blockers,
                    f"{amount} exceeds the {it.category} cap of {cap}",
                    limit=str(cap),
                    **base,
                )
            )
        if (
            policy.receipt_required_over is not None
            and amount > Decimal(policy.receipt_required_over)
            and not it.receipt_sha256
            and not (it.missing_receipt_declaration and it.missing_receipt_declaration.strip())
        ):
            out.append(
                _finding(
                    "missing_receipt",
                    blockers,
                    f"a receipt is required for items over {policy.receipt_required_over}",
                    limit=str(policy.receipt_required_over),
                    **base,
                )
            )
    return out
