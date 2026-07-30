"""The adjustable submission checklist, as DATA (G2.10 slice 1;
`BA_fleet_fuel.md` §3.E, R45).

WHY RULES ARE ROWS, NOT CODE
-----------------------------
Fleet Fuel's own `checklist_rules` table is the harvested design R45 asks
for verbatim: a rule is `key`/`label`/`scope`/`check_type`/`reference`/
`active`/`sort` — an admin can deactivate one ("we don't need a signed
contract check for this client type") without a code change or a deploy.
`app.services.transport.checklist.set_active` is the ONLY writer of
`active`; `seed_default_rules` is the only writer of a NEW row.

WHY ONLY TWO ROWS ARE SEEDED IN THIS SLICE
---------------------------------------------
The harvested `DEFAULT_CHECKLIST` names six customer/country-scope rules
(`contract`, `customer_data`, `bank_account`, `nace`, `trade_register`,
`power_of_attorney`). Only `customer_data` and `bank_account` have a real,
evaluable `check_type="data"` verifier in this codebase today (reusing
`IssuerProfile` fields already on the claimant entity — no new customer
concept, ADR-P3 rule 2 in spirit). The other four are `check_type=
"document"` (needing a document-requirements-with-EXPIRY concept this
codebase does not yet have) or need a new `nace_code` column — both
deliberately deferred to a follow-up slice rather than seeded half-built;
see `app.services.transport.checklist`'s own module docstring.

Tenant-scoped: RLS-policy'd in the SAME migration that creates this table.
No cross-table FK — a rule is keyed by `(org_id, key)`, evaluated against
whichever claim's entity is being checked at read time (never a stored
reference to one claim), the same "rule, not fact" shape a config table
should have.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class VatChecklistRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One adjustable submission-checklist rule. `app.services.transport.
    checklist.seed_default_rules` is the only inserter; `set_active` is the
    only function that flips `active`."""

    __tablename__ = "vat_checklist_rules"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_vat_checklist_rules_key"),)

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    # "customer" (checked once per claimant entity) | "country" (per refund
    # country) — see module docstring; only "customer" is evaluable today.
    scope: Mapped[str] = mapped_column(String(12), nullable=False)
    # "document" (a valid document of that kind exists) | "data" (a built-in
    # verifier in DATA_VERIFIERS passes) — only "data" is evaluable today.
    check_type: Mapped[str] = mapped_column(String(12), nullable=False)
    # The DATA_VERIFIERS key (for check_type="data") or the document kind
    # (for check_type="document", not yet evaluable).
    reference: Mapped[str | None] = mapped_column(String(60), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", nullable=False)
    sort: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
