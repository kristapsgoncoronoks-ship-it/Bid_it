"""The admin automation engine (WO-J) — trigger, condition, actions.

The research's rulings, encoded (docs/design/workflow-builder-research.md):

- **Sweep, not event bus.** Triggers are DERIVED from queries — the same
  model as the Next-actions generators — evaluated by one daily per-tenant
  job. Far fewer integration points than hooking every mutation, and a rule
  can never fire mid-transaction with half-written state. The trade: rules
  react within a day, not within a second — stated in the UI copy.
- **Declarative conditions only.** A ~sixty-line JSON-Logic-subset
  evaluator (`var ==, !=, >, >=, <, <=, in, and, or, !`) over a flat,
  trigger-supplied context. No eval, no RestrictedPython (not a sandbox),
  no Jinja in the template position — action texts use `{{var}}` LOOKUP
  substitution only.
- **A fixed action catalog.** Every action is an existing, audited service.
  The engine composes capability; it never invents any.
- **Guardrails from shipped precedent**: fire-once-per-record default
  (HubSpot's enrollment model), cooldown as the opt-in re-fire, a per-rule
  per-sweep send cap with a visible `throttled` run row (Jira's
  throttle-then-log), and runs pinned to the PUBLISHED version snapshot.

Services never commit; the sweep's job handler commits per org sweep.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation import (
    ACTIONS,
    AUTOMATION_EVENT,
    FIRE_POLICIES,
    TRIGGERS,
    AutomationRule,
    AutomationRuleVersion,
    AutomationRun,
)
from app.models.costing import Project
from app.models.customer import Customer
from app.models.issued_invoice import IssuedInvoice
from app.models.organization import Organization
from app.models.project_assignment import ProjectAssignment
from app.models.project_offer import ProjectOffer
from app.models.user import User, UserRole
from app.services import crm, mailer, webhooks

#: Per rule per sweep: more sends than this in one pass is a runaway, not a
#: workflow — the remainder is recorded as `throttled`, never silently cut.
MAX_FIRES_PER_SWEEP = 25
MAX_PREVIEW_RECORDS = 500
MAX_PREVIEW_BYTES = 2 * 1024 * 1024

AUTOMATION_SWEEP = "automation.sweep"


class AutomationError(Exception):
    """Base for automation failures the route maps to HTTP."""


class ConflictError(AutomationError):
    """The reviewed revision changed, or legacy execution needs reconciliation."""


class PreconditionError(AutomationError):
    """An explicit reviewed revision is required."""


class NotFoundError(AutomationError):
    """Unknown (or other-tenant — indistinguishable, §4.4) id."""


# --------------------------------------------------------------------------- #
# The safe condition evaluator (JSON-Logic subset)
# --------------------------------------------------------------------------- #

_OPS = {"==", "!=", ">", ">=", "<", "<=", "in", "and", "or", "!", "var"}


# Trigger contexts are flat and supplied only by the native matchers below.
_CONTEXT_TYPES = {
    "offer.sent_stale": {
        "offer_number": "string",
        "offer_title": "string",
        "total": "number",
        "days_quiet": "number",
        "project_id": "string",
    },
    "issued.overdue": {
        "invoice_number": "string",
        "total": "number",
        "outstanding": "number",
        "days_overdue": "number",
        "customer_id": "string",
    },
    "project.accepted": {
        "project_code": "string",
        "project_name": "string",
        "days_since_accepted": "number",
        "customer_id": "string",
    },
    "assignment.done_all": {
        "project_code": "string",
        "project_name": "string",
        "done_count": "number",
        "customer_id": "string",
    },
    "customer.dormant": {
        "customer_name": "string",
        "lifecycle": "string",
        "days_since_last_invoice": "number",
        "customer_id": "string",
    },
}
MAX_CONDITION_DEPTH = 16
MAX_CONDITION_NODES = 256
MAX_CONDITION_BYTES = 16384
MAX_ACTIONS = 10


def validate_condition(node: Any, *, fields: dict[str, str] | None = None) -> None:
    """Bounded, typed JSON-Logic subset. Null/missing ordered values never match."""
    count = 0

    def visit(value, depth=0):
        nonlocal count
        count += 1
        if count > MAX_CONDITION_NODES or depth > MAX_CONDITION_DEPTH:
            raise AutomationError("condition exceeds the node or depth limit")
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)):
            if not Decimal(str(value)).is_finite():
                raise AutomationError("condition numbers must be finite")
            return "number"
        if isinstance(value, str):
            if len(value) > 2048:
                raise AutomationError("condition strings exceed 2048 characters")
            return "string"
        if isinstance(value, list):
            return ("list", [visit(item, depth + 1) for item in value])
        if not isinstance(value, dict) or len(value) != 1:
            raise AutomationError("a condition node must have exactly one operator")
        op, args = next(iter(value.items()))
        if op not in _OPS:
            raise AutomationError(f"unsupported condition operator '{op}'")
        if op == "var":
            if not isinstance(args, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,63}", args):
                raise AutomationError("var needs one supported field name")
            if fields is not None and args not in fields:
                raise AutomationError(f"unsupported trigger field '{args}'")
            return fields[args] if fields is not None else "unknown"
        if op == "!":
            if isinstance(args, list):
                if len(args) != 1:
                    raise AutomationError("! needs exactly one operand")
                args = args[0]
            kind = visit(args, depth + 1)
            if kind not in ("boolean", "unknown", "null"):
                raise AutomationError("! needs a boolean expression")
            return "boolean"
        if not isinstance(args, list) or (not args if op in ("and", "or") else len(args) != 2):
            raise AutomationError(f"{op} has invalid operand count")
        types = [visit(arg, depth + 1) for arg in args]
        if op in ("and", "or"):
            if any(kind not in ("boolean", "unknown", "null") for kind in types):
                raise AutomationError("logical operators need boolean expressions")
        elif op in (">", ">=", "<", "<="):
            if any(kind not in ("number", "unknown", "null") for kind in types):
                raise AutomationError("ordered comparisons need numeric operands")
        elif op == "in":
            left, right = types
            if isinstance(right, tuple):
                if any(
                    kind not in (left, "null", "unknown") and left != "unknown" for kind in right[1]
                ):
                    raise AutomationError("membership list must match its operand type")
            elif right not in ("string", "unknown") or left not in ("string", "unknown"):
                raise AutomationError("in needs a matching literal list or string")
        elif types[0] != types[1] and not any(kind in ("null", "unknown") for kind in types):
            raise AutomationError("equality operands must have matching types")
        return "boolean"

    root = visit(node)
    if root not in ("boolean", "null", "unknown"):
        raise AutomationError("condition root must be a boolean expression")
    if len(json.dumps(node, allow_nan=False).encode()) > MAX_CONDITION_BYTES:
        raise AutomationError("condition exceeds its 16 KiB budget")


def eval_condition(node: Any, ctx: dict[str, Any]) -> Any:
    """Validate at the public boundary, including legacy stored conditions."""
    validate_condition(node)
    return _eval_condition(node, ctx)


def _eval_condition(node: Any, ctx: dict[str, Any]) -> Any:
    if node is None or isinstance(node, (str, int, float, bool)):
        return node
    if isinstance(node, list):
        return [_eval_condition(item, ctx) for item in node]
    op, args = next(iter(node.items()))
    if op == "var":
        return ctx.get(args)
    if op == "!":
        return not _eval_condition(args[0] if isinstance(args, list) else args, ctx)
    vals = [_eval_condition(a, ctx) for a in args]
    if op == "and":
        return all(vals)
    if op == "or":
        return any(vals)
    a, b = vals
    if op == "in":
        if not isinstance(b, (str, list)) or (isinstance(b, str) and not isinstance(a, str)):
            return False
        return a in b
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if a is None or b is None or isinstance(a, bool) or isinstance(b, bool):
        return False
    try:
        a, b = Decimal(str(a)), Decimal(str(b))
        if not a.is_finite() or not b.is_finite():
            return False
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    except (InvalidOperation, TypeError, ValueError):
        return False


_VAR = re.compile(r"\{\{\s*([a-z_][a-z0-9_.]*)\s*\}\}")


def render_text(template: str, ctx: dict[str, Any]) -> str:
    """`{{var}}` lookup substitution ONLY — input is data, never template
    code (the SSTI ruling). Unknown variables stay visible, like the
    document-template machinery does."""
    return _VAR.sub(lambda m: str(ctx.get(m.group(1), m.group(0))), template)


# --------------------------------------------------------------------------- #
# Rule lifecycle
# --------------------------------------------------------------------------- #


def _validate_definition(
    trigger: str, condition: Any, actions: list[dict], fire_policy: str, cooldown_hours: int | None
) -> None:
    if trigger not in TRIGGERS:
        raise AutomationError(f"unknown trigger '{trigger}'")
    validate_condition(condition, fields=_CONTEXT_TYPES[trigger])
    if not isinstance(actions, list) or not 1 <= len(actions) <= MAX_ACTIONS:
        raise AutomationError("a rule needs between 1 and 10 actions")
    for a in actions:
        if not isinstance(a, dict) or set(a) - {"kind", "subject", "body"}:
            raise AutomationError("unsupported action fields")
        for key, limit in (("subject", 200), ("body", 16000)):
            value = a.get(key)
            if value is not None:
                if not isinstance(value, str) or len(value) > limit:
                    raise AutomationError(
                        f"action {key} must be text of at most {limit} characters"
                    )
                if any(match not in _CONTEXT_TYPES[trigger] for match in _VAR.findall(value)):
                    raise AutomationError("action template references an unsupported trigger field")
        if a.get("kind") not in ACTIONS:
            raise AutomationError(f"unknown action '{a.get('kind')}'")
        if a["kind"] in ("notify_owner_email", "notify_customer_email"):
            if not (a.get("subject") or "").strip() or not (a.get("body") or "").strip():
                raise AutomationError("an email action needs a subject and a body")
        if a["kind"] == "create_customer_note" and not (a.get("body") or "").strip():
            raise AutomationError("a note action needs a body")
        # `emit_webhook` requires nothing: its payload is built from the record
        # and the rule, and its optional `body` is a human note. There is no
        # URL to validate here either — endpoints are registered separately and
        # subscribe themselves, so a rule cannot name an arbitrary address.
    if fire_policy not in FIRE_POLICIES:
        raise AutomationError(f"unknown fire policy '{fire_policy}'")
    if cooldown_hours is not None and (
        type(cooldown_hours) is not int or not 1 <= cooldown_hours <= 8760
    ):
        raise AutomationError("cooldown_hours must be an integer from 1 to 8760")
    if fire_policy == "cooldown" and not cooldown_hours:
        raise AutomationError("a cooldown policy needs cooldown_hours")


async def _rule(db: AsyncSession, org_id: str, rule_id: str) -> AutomationRule:
    row = await db.scalar(
        select(AutomationRule)
        .where(AutomationRule.org_id == org_id, AutomationRule.id == rule_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFoundError("rule not found")
    return row


async def create_rule(
    db: AsyncSession,
    org_id: str,
    *,
    name: str,
    trigger: str,
    condition: Any,
    actions: list[dict],
    fire_policy: str = "once_per_record",
    cooldown_hours: int | None = None,
    created_by: str | None = None,
) -> AutomationRule:
    _validate_definition(trigger, condition, actions, fire_policy, cooldown_hours)
    if not name.strip() or len(name) > 200:
        raise AutomationError("rule name must contain 1 to 200 characters")
    row = AutomationRule(
        org_id=org_id,
        name=name.strip(),
        trigger=trigger,
        condition_json=json.dumps(condition) if condition is not None else None,
        actions_json=json.dumps(actions),
        fire_policy=fire_policy,
        cooldown_hours=cooldown_hours,
        created_by=created_by,
    )
    db.add(row)
    await db.flush()
    return row


async def update_rule(
    db: AsyncSession,
    org_id: str,
    rule_id: str,
    *,
    expected_revision: str | None = None,
    **fields: Any,
) -> AutomationRule:
    """Edits land on the DRAFT definition; the published snapshot keeps
    running until the next publish."""
    row = await _rule(db, org_id, rule_id)
    _check_revision(row, expected_revision)
    if "name" in fields and fields["name"] is not None:
        if (
            not isinstance(fields["name"], str)
            or not fields["name"].strip()
            or len(fields["name"]) > 200
        ):
            raise AutomationError("rule name must contain 1 to 200 characters")
        row.name = fields["name"].strip()
    trigger = fields.get("trigger", row.trigger)
    condition = (
        fields["condition"]
        if "condition" in fields
        else (json.loads(row.condition_json) if row.condition_json else None)
    )
    actions = fields.get("actions", json.loads(row.actions_json))
    fire_policy = fields.get("fire_policy", row.fire_policy)
    cooldown = fields.get("cooldown_hours", row.cooldown_hours)
    _validate_definition(trigger, condition, actions, fire_policy, cooldown)
    row.trigger = trigger
    row.condition_json = json.dumps(condition) if condition is not None else None
    row.actions_json = json.dumps(actions)
    row.fire_policy = fire_policy
    row.cooldown_hours = cooldown
    await db.flush()
    return row


def _snapshot(row: AutomationRule) -> dict:
    return {
        "name": row.name,
        "trigger": row.trigger,
        "condition": json.loads(row.condition_json) if row.condition_json else None,
        "actions": json.loads(row.actions_json),
        "fire_policy": row.fire_policy,
        "cooldown_hours": row.cooldown_hours,
    }


def revision(row: AutomationRule) -> str:
    content = {
        "definition": _snapshot(row),
        "status": row.status,
        "published_version": row.published_version,
    }
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _check_revision(row: AutomationRule, expected: str | None) -> None:
    if expected is None:
        raise PreconditionError(
            "Refresh this rule and supply its reviewed revision before changing it."
        )
    if expected != revision(row):
        raise ConflictError(
            "This rule changed. Reload and review it before saving or publishing; your edits were not applied."
        )


def _validate_snapshot(snap: dict) -> None:
    try:
        _validate_definition(
            snap["trigger"],
            snap["condition"],
            snap["actions"],
            snap["fire_policy"],
            snap["cooldown_hours"],
        )
        if not isinstance(snap["name"], str) or not snap["name"].strip() or len(snap["name"]) > 200:
            raise AutomationError("invalid snapshot name")
    except (KeyError, TypeError, ValueError) as exc:
        raise AutomationError(
            "Invalid stored rule definition; review and publish a valid definition."
        ) from exc


async def _execution_rule(db: AsyncSession, org_id: str, row: AutomationRule):
    if row.org_id != org_id:
        raise NotFoundError("rule not found")
    row = await _rule(db, org_id, row.id)
    if row.status != "published" or row.published_version is None:
        raise AutomationError("Only a published rule can execute.")
    version = await db.scalar(
        select(AutomationRuleVersion).where(
            AutomationRuleVersion.org_id == org_id,
            AutomationRuleVersion.rule_id == row.id,
            AutomationRuleVersion.version == row.published_version,
        )
    )
    if version is None:
        raise AutomationError("Published definition missing; review and republish this rule.")
    try:
        snap = json.loads(version.snapshot_json)
        _validate_snapshot(snap)
    except (ValueError, TypeError) as exc:
        raise AutomationError(
            "Published definition is invalid; review and republish this rule."
        ) from exc
    # Old software executed mutable columns. Choosing either differing legacy
    # definition silently would choose a business policy on the owner's behalf.
    if snap.get("definition_version") not in (None, 1, 2):
        raise AutomationError(
            "Unsupported published definition version; review this rule before execution."
        )
    if snap.get("definition_version") != 2:
        legacy = {key: value for key, value in snap.items() if key != "definition_version"}
        if legacy != _snapshot(row):
            raise ConflictError(
                "Legacy draft and published definitions differ. Execution is blocked until an owner reviews and explicitly publishes the intended definition."
            )
    return SimpleNamespace(
        condition_json=json.dumps(snap["condition"]),
        actions_json=json.dumps(snap["actions"]),
        id=row.id,
        org_id=org_id,
        published_version=row.published_version,
        **{
            key: snap[key]
            for key in ("name", "trigger", "condition", "actions", "fire_policy", "cooldown_hours")
        },
    )


async def preflight(db: AsyncSession, org_id: str, rule_id: str) -> dict:
    row = await _rule(db, org_id, rule_id)
    result: dict[str, Any] = {
        "revision": revision(row),
        "published_version": row.published_version,
        "requires_reconciliation": False,
        "execution_ready": False,
        "issues": [],
    }
    if row.status != "published":
        result["issues"] = ["Rule is not enabled/published."]
        return result
    try:
        await _execution_rule(db, org_id, row)
        result["execution_ready"] = True
    except ConflictError as exc:
        result["requires_reconciliation"] = True
        result["issues"] = [str(exc)]
    except AutomationError as exc:
        result["issues"] = [str(exc)]
    return result


async def publish_rule(
    db: AsyncSession,
    org_id: str,
    rule_id: str,
    *,
    published_by: str | None,
    expected_revision: str | None = None,
) -> AutomationRule:
    row = await _rule(db, org_id, rule_id)
    _check_revision(row, expected_revision)
    snap = _snapshot(row)
    _validate_snapshot(snap)
    latest = await db.scalar(
        select(func.max(AutomationRuleVersion.version)).where(
            AutomationRuleVersion.org_id == org_id, AutomationRuleVersion.rule_id == rule_id
        )
    )
    version = int(latest or 0) + 1
    db.add(
        AutomationRuleVersion(
            org_id=org_id,
            rule_id=rule_id,
            version=version,
            snapshot_json=json.dumps({**snap, "definition_version": 2}),
            published_by=published_by,
        )
    )
    row.status = "published"
    row.published_version = version
    await db.flush()
    return row


async def revert_rule(
    db: AsyncSession,
    org_id: str,
    rule_id: str,
    version: int,
    *,
    published_by: str | None,
    expected_revision: str | None = None,
) -> AutomationRule:
    """Revert = load an old snapshot into the definition and publish it as a
    NEW version. History is append-only; nothing is rewritten."""
    row = await _rule(db, org_id, rule_id)
    _check_revision(row, expected_revision)
    snap_row = await db.scalar(
        select(AutomationRuleVersion).where(
            AutomationRuleVersion.org_id == org_id,
            AutomationRuleVersion.rule_id == rule_id,
            AutomationRuleVersion.version == version,
        )
    )
    if snap_row is None:
        raise NotFoundError("version not found")
    snap = json.loads(snap_row.snapshot_json)
    _validate_snapshot(snap)
    row.name = snap["name"]
    row.trigger = snap["trigger"]
    row.condition_json = json.dumps(snap["condition"]) if snap["condition"] is not None else None
    row.actions_json = json.dumps(snap["actions"])
    row.fire_policy = snap["fire_policy"]
    row.cooldown_hours = snap["cooldown_hours"]
    await db.flush()
    return await publish_rule(
        db, org_id, rule_id, published_by=published_by, expected_revision=revision(row)
    )


async def set_status(
    db: AsyncSession,
    org_id: str,
    rule_id: str,
    status: str,
    *,
    expected_revision: str | None = None,
) -> AutomationRule:
    if status not in ("published", "disabled"):
        raise AutomationError("status must be 'published' or 'disabled'")
    row = await _rule(db, org_id, rule_id)
    _check_revision(row, expected_revision)
    if status == "published" and row.published_version is None:
        raise AutomationError("publish the rule first — a draft has no version to run")
    row.status = status
    await db.flush()
    return row


# --------------------------------------------------------------------------- #
# Trigger derivation — each returns [(ref_id, context)] like the
# Next-actions generators: queries over CURRENT state, no event bus.
# --------------------------------------------------------------------------- #


def _days(a: datetime | None, now: datetime) -> int | None:
    if a is None:
        return None
    a = a if a.tzinfo else a.replace(tzinfo=UTC)
    return max(0, (now - a).days)


async def _match_offer_sent_stale(db, org_id: str, now: datetime) -> list[tuple[str, dict]]:
    out = []
    for o in await db.scalars(
        select(ProjectOffer).where(ProjectOffer.org_id == org_id, ProjectOffer.status == "sent")
    ):
        out.append(
            (
                o.id,
                {
                    "offer_number": o.number,
                    "offer_title": o.title or "",
                    "total": float(o.total),
                    "days_quiet": _days(o.updated_at, now),
                    "project_id": o.project_id,
                },
            )
        )
    return out


async def _match_issued_overdue(db, org_id: str, now: datetime) -> list[tuple[str, dict]]:
    today = now.date()
    out = []
    for inv in await db.scalars(
        select(IssuedInvoice).where(
            IssuedInvoice.org_id == org_id,
            IssuedInvoice.lifecycle.in_(("issued", "partially_paid", "overdue")),
            IssuedInvoice.due_date.is_not(None),
            IssuedInvoice.due_date < today,
        )
    ):
        outstanding = float((inv.total or 0) - (inv.amount_paid or 0))
        if outstanding <= 0:
            continue
        out.append(
            (
                inv.id,
                {
                    "invoice_number": inv.number or "draft",
                    "total": float(inv.total or 0),
                    "outstanding": outstanding,
                    "days_overdue": (today - inv.due_date).days,
                    "customer_id": inv.customer_id,
                },
            )
        )
    return out


async def _match_project_accepted(db, org_id: str, now: datetime) -> list[tuple[str, dict]]:
    out = []
    for p in await db.scalars(
        select(Project).where(Project.org_id == org_id, Project.accepted_at.is_not(None))
    ):
        out.append(
            (
                p.id,
                {
                    "project_code": p.code,
                    "project_name": p.name,
                    "days_since_accepted": _days(p.accepted_at, now),
                    "customer_id": p.customer_id,
                },
            )
        )
    return out


async def _match_assignment_done_all(db, org_id: str, now: datetime) -> list[tuple[str, dict]]:
    rows = (
        await db.execute(
            select(ProjectAssignment.project_id, ProjectAssignment.status, func.count())
            .where(ProjectAssignment.org_id == org_id)
            .group_by(ProjectAssignment.project_id, ProjectAssignment.status)
        )
    ).all()
    by_project: dict[str, dict[str, int]] = {}
    for pid, status, n in rows:
        by_project.setdefault(pid, {})[status] = n
    out = []
    for pid, statuses in by_project.items():
        if statuses.get("done") and not (statuses.get("planned") or statuses.get("confirmed")):
            p = await db.scalar(select(Project).where(Project.org_id == org_id, Project.id == pid))
            if p is None or p.accepted_at is not None:
                continue  # already accepted → the arc moved on
            out.append(
                (
                    pid,
                    {
                        "project_code": p.code,
                        "project_name": p.name,
                        "done_count": statuses.get("done", 0),
                        "customer_id": p.customer_id,
                    },
                )
            )
    return out


async def _match_customer_dormant(db, org_id: str, now: datetime) -> list[tuple[str, dict]]:
    out = []
    for c in await db.scalars(
        select(Customer).where(Customer.org_id == org_id, Customer.is_active.is_(True))
    ):
        last = await db.scalar(
            select(func.max(IssuedInvoice.issue_date)).where(
                IssuedInvoice.org_id == org_id, IssuedInvoice.customer_id == c.id
            )
        )
        days = (now.date() - last).days if isinstance(last, date) else None
        out.append(
            (
                c.id,
                {
                    "customer_name": c.name,
                    "lifecycle": c.lifecycle,
                    "days_since_last_invoice": days,
                    "customer_id": c.id,
                },
            )
        )
    return out


_MATCHERS = {
    "offer.sent_stale": _match_offer_sent_stale,
    "issued.overdue": _match_issued_overdue,
    "project.accepted": _match_project_accepted,
    "assignment.done_all": _match_assignment_done_all,
    "customer.dormant": _match_customer_dormant,
}


# --------------------------------------------------------------------------- #
# Actions — every one an existing, audited capability.
# --------------------------------------------------------------------------- #


async def _owner_email(db, org_id: str) -> str | None:
    return await db.scalar(
        select(User.email).where(User.org_id == org_id, User.role == UserRole.owner).limit(1)
    )


async def _run_action(
    db, org_id: str, action: dict, ctx: dict, *, rule: AutomationRule, ref_id: str
) -> dict:
    """Run ONE action of a firing rule.

    `rule` and `ref_id` are passed explicitly rather than smuggled into `ctx`:
    `ctx` is the RECORD's fields, which is what `{{var}}` templating renders
    from and what a dry-run shows the operator. Mixing engine internals into it
    would put them in the template namespace and on that screen.
    """
    kind = action["kind"]
    if kind == "notify_owner_email":
        to = await _owner_email(db, org_id)
        if not to:
            return {"kind": kind, "ok": False, "reason": "no owner email"}
        await mailer.send(
            db,
            org_id,
            kind="automation",
            to_email=to,
            subject=render_text(action["subject"], ctx),
            body=render_text(action["body"], ctx),
        )
        return {"kind": kind, "ok": True, "to": to}
    if kind == "notify_customer_email":
        cid = ctx.get("customer_id")
        customer = (
            await db.scalar(select(Customer).where(Customer.org_id == org_id, Customer.id == cid))
            if cid
            else None
        )
        if customer is None or not customer.email:
            return {"kind": kind, "ok": False, "reason": "no customer email"}
        await mailer.send(
            db,
            org_id,
            kind="automation",
            to_email=customer.email,
            subject=render_text(action["subject"], ctx),
            body=render_text(action["body"], ctx),
        )
        return {"kind": kind, "ok": True, "to": customer.email}
    if kind == "create_customer_note":
        cid = ctx.get("customer_id")
        if not cid:
            return {"kind": kind, "ok": False, "reason": "no customer on this record"}
        try:
            await crm.add_note(
                db,
                org_id,
                cid,
                body=render_text(action["body"], ctx),
                created_by="automation",
            )
        except crm.CrmError as exc:
            return {"kind": kind, "ok": False, "reason": str(exc)}
        return {"kind": kind, "ok": True}
    if kind == "emit_webhook":
        # The first action that reaches OUTSIDE this workspace. It composes an
        # existing subsystem rather than learning to make an HTTP request:
        # `webhooks.emit` already signs (HMAC-SHA256), already refuses a private
        # address (SSRF guard), and already delivers through the durable queue
        # with retry and dead-lettering.
        #
        # The IDEMPOTENCY KEY is what makes this safe to compose. A sweep can
        # re-evaluate the same record — a cooldown expiring, an every_time
        # policy, a worker retrying — and the run ledger governs whether the
        # rule FIRES, not whether a delivery is duplicated. Keying on
        # (rule, record) means a re-fire that should notify once, notifies once.
        n = await webhooks.emit(
            db,
            org_id,
            AUTOMATION_EVENT,
            {
                "rule_id": rule.id,
                "rule_name": rule.name,
                "trigger": rule.trigger,
                "record": ctx,
                "note": render_text(action.get("body") or "", ctx),
            },
            idempotency_key=f"automation:{rule.id}:{ref_id}",
        )
        # `enqueued == 0` is not a failure: it means no endpoint subscribes to
        # this event, or the same rule already notified for this record. Both
        # are reported honestly rather than dressed as a send.
        return {"kind": kind, "ok": True, "enqueued": n}
    return {"kind": kind, "ok": False, "reason": "unknown action"}  # pragma: no cover


# --------------------------------------------------------------------------- #
# The sweep
# --------------------------------------------------------------------------- #


async def _may_fire(db, org_id: str, rule: AutomationRule, ref_id: str, now: datetime) -> bool:
    if rule.fire_policy == "every_time":
        return True
    last = await db.scalar(
        select(func.max(AutomationRun.created_at)).where(
            AutomationRun.org_id == org_id,
            AutomationRun.rule_id == rule.id,
            AutomationRun.ref_id == ref_id,
            AutomationRun.status == "ok",
        )
    )
    if last is None:
        return True
    if rule.fire_policy == "once_per_record":
        return False
    last = last if last.tzinfo else last.replace(tzinfo=UTC)
    return now - last >= timedelta(hours=rule.cooldown_hours or 0)


async def evaluate_rule(
    db: AsyncSession,
    org_id: str,
    rule: AutomationRule,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    preview_basis: str = "draft",
) -> list[dict]:
    """One rule over current state. Returns the per-record outcomes; with
    dry_run=True nothing is sent, written or logged — the would-be outcome
    is simply reported."""
    now = now or datetime.now(UTC)
    if rule.org_id != org_id:
        raise NotFoundError("rule not found")
    if dry_run and preview_basis == "draft":
        snap = _snapshot(rule)
        _validate_snapshot(snap)
    else:
        rule = await _execution_rule(db, org_id, rule)
        snap = {
            "condition": json.loads(rule.condition_json) if rule.condition_json else None,
            "actions": json.loads(rule.actions_json),
        }
    condition = snap["condition"]
    actions = snap["actions"]
    matches = await _MATCHERS[rule.trigger](db, org_id, now)
    outcomes: list[dict] = []
    fired = 0
    preview_bytes = 0

    def append_preview(entry):
        nonlocal preview_bytes
        preview_bytes += len(json.dumps(entry).encode())
        if len(outcomes) >= MAX_PREVIEW_RECORDS or preview_bytes > MAX_PREVIEW_BYTES:
            raise AutomationError(
                "Preview exceeds 500 matching records or 2 MiB. Narrow the draft condition; no actions were attempted."
            )
        outcomes.append(entry)

    for ref_id, ctx in sorted(matches, key=lambda match: match[0]):
        if condition is not None and not eval_condition(condition, ctx):
            continue
        if not await _may_fire(db, org_id, rule, ref_id, now):
            if dry_run:
                append_preview(
                    {
                        "ref_id": ref_id,
                        "status": "suppressed",
                        "reason": rule.fire_policy,
                        "context": ctx,
                        "actions": [a["kind"] for a in actions],
                    }
                )
            continue
        if dry_run:
            status = "throttled" if fired >= MAX_FIRES_PER_SWEEP else "would_fire"
            append_preview(
                {
                    "ref_id": ref_id,
                    "status": status,
                    "context": ctx,
                    "actions": [a["kind"] for a in actions],
                    "proposals": [
                        {
                            **a,
                            **{
                                key: render_text(a[key], ctx)
                                for key in ("subject", "body")
                                if a.get(key) is not None
                            },
                        }
                        for a in actions
                    ],
                    "delivery": "not_attempted",
                    "cap": MAX_FIRES_PER_SWEEP,
                }
            )
            if status == "would_fire":
                fired += 1
            continue
        if fired >= MAX_FIRES_PER_SWEEP:
            db.add(
                AutomationRun(
                    org_id=org_id,
                    rule_id=rule.id,
                    version=rule.published_version or 0,
                    ref_id=ref_id,
                    status="throttled",
                    detail_json=json.dumps({"cap": MAX_FIRES_PER_SWEEP}),
                )
            )
            outcomes.append({"ref_id": ref_id, "status": "throttled"})
            continue
        results = [await _run_action(db, org_id, a, ctx, rule=rule, ref_id=ref_id) for a in actions]
        ok = all(r["ok"] for r in results)
        db.add(
            AutomationRun(
                org_id=org_id,
                rule_id=rule.id,
                version=rule.published_version or 0,
                ref_id=ref_id,
                status="ok" if ok else "failed",
                detail_json=json.dumps(results),
            )
        )
        fired += 1
        outcomes.append({"ref_id": ref_id, "status": "ok" if ok else "failed"})
    if not dry_run:
        await db.flush()
    return outcomes


async def sweep(db: AsyncSession, org_id: str) -> dict:
    """Every PUBLISHED rule of one tenant, once. The daily job's work."""
    rules = list(
        await db.scalars(
            select(AutomationRule).where(
                AutomationRule.org_id == org_id, AutomationRule.status == "published"
            )
        )
    )
    fired = throttled = failed = 0
    for rule in rules:
        try:
            rule = await _rule(db, org_id, rule.id)
            if rule.status != "published":
                continue
            outcomes = await evaluate_rule(db, org_id, rule)
        except AutomationError as exc:
            # Invalid legacy definitions must not abort unrelated tenant rules.
            db.add(
                AutomationRun(
                    org_id=org_id,
                    rule_id=rule.id,
                    version=rule.published_version or 0,
                    ref_id=rule.id,
                    status="failed",
                    detail_json=json.dumps({"reason": str(exc), "blocked_definition": True}),
                )
            )
            failed += 1
            continue
        for outcome in outcomes:
            if outcome["status"] == "ok":
                fired += 1
            elif outcome["status"] == "throttled":
                throttled += 1
            elif outcome["status"] == "failed":
                failed += 1
    return {"rules": len(rules), "fired": fired, "throttled": throttled, "failed": failed}


async def list_runs(db: AsyncSession, org_id: str, *, limit: int = 100) -> list[AutomationRun]:
    return list(
        await db.scalars(
            select(AutomationRun)
            .where(AutomationRun.org_id == org_id)
            .order_by(AutomationRun.created_at.desc())
            .limit(limit)
        )
    )


async def org_exists(db: AsyncSession, org_id: str) -> bool:  # pragma: no cover - trivial
    return await db.scalar(select(Organization.id).where(Organization.id == org_id)) is not None
