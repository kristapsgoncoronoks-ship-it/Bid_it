"""WO-L claim hygiene: the §12 `ignored` state + §13 line-level rejection.

Revision ID: c6d8e0f2a4b6
Revises: b5c7d9e1f3a5
Create Date: 2026-08-26

Two changes, both owner-decided 2026-08-08 (docs/DECISIONS-NEEDED.md):

- `ck_vat_overcharge_claims_status` widens from the harvested six states to
  include `ignored` — the explicit, audited "we are not pursuing this"
  outcome §12 called for. Replaced drop-before-create in a batch (SQLite
  cannot alter a CHECK in place); no row can violate the WIDER constraint,
  so no pre-flight scan is needed (every value legal under six states is
  legal under seven).
- `vat_claim_lines.rejected_at` — the §13 partial-rejection stamp: when a
  member state rejects SOME invoices of a filed claim, the affected lines
  carry the decision moment and the claim's frozen figures are recomputed
  on the reduced base at the FROZEN fee rate (fee.py's documented seam).
- `vat_claim_lines.unmatched_suppliers` — §11's option (b): an UNMATCHED
  line keeps the R2 grain and CARRIES the distinct suppliers behind it
  (JSON list, set at build time when the suppliers are in hand). NULL on
  every resolved line; a work-item hint, never a filable attribute (R3
  still refuses every synthetic line at submit).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "c6d8e0f2a4b6"
down_revision: Union[str, None] = "b5c7d9e1f3a5"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

_SIX = ("detected", "packaged", "claimed", "recovered", "rejected", "written_off")
_SEVEN = _SIX + ("ignored",)


def _check(states: tuple[str, ...]) -> str:
    return "status IN (" + ", ".join(f"'{s}'" for s in states) + ")"


def upgrade() -> None:
    with op.batch_alter_table("vat_overcharge_claims", schema=None) as batch_op:
        batch_op.drop_constraint("ck_vat_overcharge_claims_status", type_="check")
        batch_op.create_check_constraint("ck_vat_overcharge_claims_status", _check(_SEVEN))
    op.add_column(
        "vat_claim_lines",
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "vat_claim_lines",
        sa.Column("unmatched_suppliers", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    # Refuse to narrow the constraint while an `ignored` row exists — a silent
    # downgrade would strand data the constraint then forbids.
    bind = op.get_bind()
    n = bind.execute(
        sa.text("SELECT COUNT(*) FROM vat_overcharge_claims WHERE status = 'ignored'")
    ).scalar()
    if n:
        raise RuntimeError(
            f"[WO-L] {n} claim-back(s) are 'ignored'; move them back to 'detected' "
            "before downgrading, or the narrowed CHECK would forbid their own rows."
        )
    op.drop_column("vat_claim_lines", "unmatched_suppliers")
    op.drop_column("vat_claim_lines", "rejected_at")
    with op.batch_alter_table("vat_overcharge_claims", schema=None) as batch_op:
        batch_op.drop_constraint("ck_vat_overcharge_claims_status", type_="check")
        batch_op.create_check_constraint("ck_vat_overcharge_claims_status", _check(_SIX))
