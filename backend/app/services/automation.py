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

import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.automation import (
    ACTIONS,
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
from app.services import crm, mailer

#: Per rule per sweep: more sends than this in one pass is a runaway, not a
#: workflow — the remainder is recorded as `throttled`, never silently cut.
MAX_FIRES_PER_SWEEP = 25

AUTOMATION_SWEEP = "automation.sweep"


class AutomationError(Exception):
    """Base for automation failures the route maps to HTTP."""


class NotFoundError(AutomationError):
    """Unknown (or other-tenant — indistinguishable, §4.4) id."""


# --------------------------------------------------------------------------- #
# The safe condition evaluator (JSON-Logic subset)
# --------------------------------------------------------------------------- #

_OPS = {"==", "!=", ">", ">=", "<", "<=", "in", "and", "or", "!", "var"}


def validate_condition(node: Any) -> None:
    """Refuse anything outside the closed operator set — at SAVE time, so a
    bad rule can't even be stored, let alone run."""
    if node is None or isinstance(node, (str, int, float, bool)):
        return
    if isinstance(node, list):
        for item in node:
            validate_condition(item)
        return
    if isinstance(node, dict):
        if len(node) != 1:
            raise AutomationError("a condition node must have exactly one operator")
        op, args = next(iter(node.items()))
        if op not in _OPS:
            raise AutomationError(f"unsupported condition operator '{op}'")
        validate_condition(args)
        return
    raise AutomationError("unsupported condition value")


def eval_condition(node: Any, ctx: dict[str, Any]) -> Any:
    """Evaluate the validated subset. Pure, total, terminates: the tree is
    finite and every operator is data-only."""
    if node is None or isinstance(node, (str, int, float, bool)):
        return node
    if isinstance(node, list):
        # A literal list (e.g. the right side of `in`) is data, not an op node.
        return [eval_condition(item, ctx) for item in node]
    if isinstance(node, dict):
        op, args = next(iter(node.items()))
        if op == "var":
            return ctx.get(args if isinstance(args, str) else str(args))
        if op == "!":
            inner = args[0] if isinstance(args, list) else args
            return not eval_condition(inner, ctx)
        vals = [eval_condition(a, ctx) for a in (args if isinstance(args, list) else [args])]
        if op == "and":
            return all(vals)
        if op == "or":
            return any(vals)
        if op == "in":
            return vals[0] in (vals[1] or [])
        a, b = vals[0], vals[1]
        try:
            if op == "==":
                return a == b
            if op == "!=":
                return a != b
            # Ordered comparisons coerce numerics; None never satisfies them.
            if a is None or b is None:
                return False
            a, b = float(a), float(b)
            return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
        except (TypeError, ValueError):
            return False
    raise AutomationError("unsupported condition value")  # pragma: no cover - validated


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
    validate_condition(condition)
    if not actions:
        raise AutomationError("a rule needs at least one action")
    for a in actions:
        if a.get("kind") not in ACTIONS:
            raise AutomationError(f"unknown action '{a.get('kind')}'")
        if a["kind"] in ("notify_owner_email", "notify_customer_email"):
            if not (a.get("subject") or "").strip() or not (a.get("body") or "").strip():
                raise AutomationError("an email action needs a subject and a body")
        if a["kind"] == "create_customer_note" and not (a.get("body") or "").strip():
            raise AutomationError("a note action needs a body")
    if fire_policy not in FIRE_POLICIES:
        raise AutomationError(f"unknown fire policy '{fire_policy}'")
    if fire_policy == "cooldown" and not cooldown_hours:
        raise AutomationError("a cooldown policy needs cooldown_hours")


async def _rule(db: AsyncSession, org_id: str, rule_id: str) -> AutomationRule:
    row = await db.scalar(
        select(AutomationRule).where(AutomationRule.org_id == org_id, AutomationRule.id == rule_id)
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


async def update_rule(db: AsyncSession, org_id: str, rule_id: str, **fields: Any) -> AutomationRule:
    """Edits land on the DRAFT definition; the published snapshot keeps
    running until the next publish."""
    row = await _rule(db, org_id, rule_id)
    if "name" in fields and fields["name"] is not None:
        row.name = str(fields["name"]).strip()
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


async def publish_rule(
    db: AsyncSession, org_id: str, rule_id: str, *, published_by: str | None
) -> AutomationRule:
    row = await _rule(db, org_id, rule_id)
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
            snapshot_json=json.dumps(_snapshot(row)),
            published_by=published_by,
        )
    )
    row.status = "published"
    row.published_version = version
    await db.flush()
    return row


async def revert_rule(
    db: AsyncSession, org_id: str, rule_id: str, version: int, *, published_by: str | None
) -> AutomationRule:
    """Revert = load an old snapshot into the definition and publish it as a
    NEW version. History is append-only; nothing is rewritten."""
    row = await _rule(db, org_id, rule_id)
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
    row.trigger = snap["trigger"]
    row.condition_json = json.dumps(snap["condition"]) if snap["condition"] is not None else None
    row.actions_json = json.dumps(snap["actions"])
    row.fire_policy = snap["fire_policy"]
    row.cooldown_hours = snap["cooldown_hours"]
    return await publish_rule(db, org_id, rule_id, published_by=published_by)


async def set_status(db: AsyncSession, org_id: str, rule_id: str, status: str) -> AutomationRule:
    if status not in ("published", "disabled"):
        raise AutomationError("status must be 'published' or 'disabled'")
    row = await _rule(db, org_id, rule_id)
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


async def _run_action(db, org_id: str, action: dict, ctx: dict) -> dict:
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
) -> list[dict]:
    """One rule over current state. Returns the per-record outcomes; with
    dry_run=True nothing is sent, written or logged — the would-be outcome
    is simply reported."""
    now = now or datetime.now(UTC)
    condition = json.loads(rule.condition_json) if rule.condition_json else None
    actions = json.loads(rule.actions_json)
    matches = await _MATCHERS[rule.trigger](db, org_id, now)
    outcomes: list[dict] = []
    fired = 0
    for ref_id, ctx in matches:
        if condition is not None and not eval_condition(condition, ctx):
            continue
        if not dry_run and not await _may_fire(db, org_id, rule, ref_id, now):
            continue
        if dry_run:
            outcomes.append(
                {
                    "ref_id": ref_id,
                    "status": "would_fire",
                    "context": ctx,
                    "actions": [a["kind"] for a in actions],
                }
            )
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
        results = [await _run_action(db, org_id, a, ctx) for a in actions]
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
        for outcome in await evaluate_rule(db, org_id, rule):
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
