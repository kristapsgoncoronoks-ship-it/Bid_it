"""Admin automation rules (WO-J, docs/design/workflow-builder-research.md).

The bounded trigger-condition-action engine the research settled on — and
nothing more. Three tables:

- `automation_rules` — the live definition: one entity type, ONE trigger
  from a closed set, a declarative condition (JSON-Logic subset — never
  code), an ordered list of actions from a fixed catalog, and the fire
  policy. Draft → published → disabled; only published rules run.
- `automation_rule_versions` — an immutable snapshot per publish (the
  durable-execution industry pattern: runs record which version acted, and
  revert is re-publishing an old snapshot, never editing history).
- `automation_runs` — one row per rule × record evaluation that DID
  something (fired, was throttled, or failed): the visible run log, and the
  once-per-record ledger the fire policy reads.

Deliberately absent, per the research's shipped-precedent checklist: loops,
branching, multi-trigger graphs, embedded scripting (RestrictedPython is
not a sandbox), free-URL webhook actions (SSRF surface — phase 2 reuses
the registered-webhook machinery instead).
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

#: trigger → the entity it fires over. Closed set; the sweep derives matches
#: from queries (like the Next-actions generators), no event bus involved.
TRIGGERS: dict[str, str] = {
    "offer.sent_stale": "offer",  # sent, quiet for >= condition-supplied days
    "issued.overdue": "issued_invoice",  # past due, not settled
    "project.accepted": "project",  # acceptance recorded
    "assignment.done_all": "project",  # every assignment done, none open
    "customer.dormant": "customer",  # no issued invoice for N days
}

RULE_STATUSES = ("draft", "published", "disabled")
FIRE_POLICIES = ("once_per_record", "every_time", "cooldown")

#: The fixed action catalog. Each key maps to an existing service — the
#: engine composes, it never invents capability.
#:
#: WO-W added `emit_webhook`, and it is the first action that reaches OUTSIDE
#: this workspace. It composes the same way as the rest: the HMAC signing, the
#: SSRF guard, the durable queue and the retry/backoff already existed in
#: `services/webhooks.py` with no automation caller. A rule can now tell an
#: external system that something happened, without this engine learning how to
#: make an HTTP request.
ACTIONS = (
    "notify_owner_email",
    "notify_customer_email",
    "create_customer_note",
    "emit_webhook",
)

#: The event type an `emit_webhook` action publishes. ONE type, not a
#: rule-author-chosen string: `webhooks.EVENT_TYPES` is a documented catalog a
#: receiver subscribes against, and letting a rule invent event names would let
#: a workspace publish events no consumer could have subscribed to and no
#: document describes. Which rule fired is carried in the PAYLOAD.
AUTOMATION_EVENT = "automation.fired"


class AutomationRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "automation_rules"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_automation_rules_org_id"),
        UniqueConstraint("org_id", "name", name="uq_automation_rules_org_name"),
        CheckConstraint(
            "status IN ('draft', 'published', 'disabled')", name="ck_automation_rules_status"
        ),
        CheckConstraint(
            "fire_policy IN ('once_per_record', 'every_time', 'cooldown')",
            name="ck_automation_rules_fire_policy",
        ),
        Index("ix_automation_rules_org_status", "org_id", "status"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    condition_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # None = always
    actions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(12), default="draft", nullable=False)
    fire_policy: Mapped[str] = mapped_column(String(16), default="once_per_record", nullable=False)
    cooldown_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)


class AutomationRuleVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "automation_rule_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "rule_id"],
            ["automation_rules.org_id", "automation_rules.id"],
            name="fk_automation_rule_versions_rule",
            ondelete="CASCADE",
        ),
        UniqueConstraint("org_id", "id", name="uq_automation_rule_versions_org_id"),
        UniqueConstraint(
            "org_id", "rule_id", "version", name="uq_automation_rule_versions_rule_version"
        ),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    published_by: Mapped[str | None] = mapped_column(String(320), nullable=True)


class AutomationRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "automation_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "rule_id"],
            ["automation_rules.org_id", "automation_rules.id"],
            name="fk_automation_runs_rule",
            ondelete="CASCADE",
        ),
        UniqueConstraint("org_id", "id", name="uq_automation_runs_org_id"),
        CheckConstraint(
            "status IN ('ok', 'throttled', 'failed')", name="ck_automation_runs_status"
        ),
        Index("ix_automation_runs_org_rule_ref", "org_id", "rule_id", "ref_id"),
        Index("ix_automation_runs_org_created", "org_id", "created_at"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[str] = mapped_column(GUID(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    ref_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
