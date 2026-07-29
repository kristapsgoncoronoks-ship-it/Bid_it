"""vat_claim_lines local-currency columns (WO-54, G2.5, C10, ADR-P3)

Additive columns only, on an already-shipped table — no backfill needed
(existing rows, if any, have no underlying transactions to derive local
amounts from; new rows get them from `app.services.transport.claim_lines.
build_claim_lines`, WO-52, extended in this order). `app.services.transport.
freeze.freeze_claim_lines` (new in this order) sums `vat_local` across a
claim's own lines to freeze the claim's "VAT base" in local currency
alongside the existing EUR figure — see that module's docstring for why a
raw period `SUM` is never a substitute (C10) and why a mixed-currency claim
refuses to freeze rather than silently summing across currencies
(master-context invariant §4.14).

Revision ID: bc783e1ec7c2
Revises: 4cb7fca7e508
Create Date: 2026-07-29 23:29:29.803618
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "bc783e1ec7c2"
down_revision: Union[str, None] = "4cb7fca7e508"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("vat_claim_lines", schema=None) as batch_op:
        batch_op.add_column(sa.Column("net_local", sa.Numeric(precision=14, scale=2), nullable=True))
        batch_op.add_column(sa.Column("vat_local", sa.Numeric(precision=14, scale=2), nullable=True))
        batch_op.add_column(sa.Column("currency", sa.String(length=3), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("vat_claim_lines", schema=None) as batch_op:
        batch_op.drop_column("currency")
        batch_op.drop_column("vat_local")
        batch_op.drop_column("net_local")
