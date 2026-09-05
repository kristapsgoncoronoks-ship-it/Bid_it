"""DB-004 — the duplicate-import guard on bank statements becomes a database invariant.

`reconciliation.import_statement` refused a duplicate upload with a SELECT
followed by an INSERT. Two uploads of the same file landing together (a
double-click, a client retry, two operators) both passed the SELECT, and the
organisation held two `bank_statements` rows and two full sets of `bank_lines`
for one set of bank data — each duplicate credit independently matchable, so
one customer payment could settle two invoices. `documents (org_id, sha256,
kind)` and `fuel_extraction_baselines (org_id, statement_sha256, currency)`
already hold this invariant in the database; this migration applies the same
pattern to `bank_statements`.

PRE-FLIGHT, FAIL CLOSED
------------------------
If duplicates already exist the constraint cannot be created, and silently
deleting one of two statements would delete the bank lines an operator may have
reconciled against. The migration refuses with the offending (org_id, sha256)
pairs named; resolving them is a deliberate operator action, not a migration
side effect.

Revision ID: d4f6a8b0c2e4
Revises: c3e5a7b9d1f2
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4f6a8b0c2e4"
down_revision: str | None = "c3e5a7b9d1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "uq_bank_statements_org_sha"


def upgrade() -> None:
    bind = op.get_bind()
    dupes = bind.execute(
        sa.text(
            "SELECT org_id, sha256, COUNT(*) AS n FROM bank_statements "
            "GROUP BY org_id, sha256 HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if dupes:
        listing = "; ".join(f"org {o} sha {s[:12]}… ×{n}" for o, s, n in dupes)
        raise RuntimeError(
            f"[DB-004] refusing to add {CONSTRAINT}: {len(dupes)} duplicate statement "
            f"group(s) exist ({listing}). Each duplicate carries its own bank lines, "
            "some possibly reconciled; decide which to keep, then re-run."
        )
    with op.batch_alter_table("bank_statements", schema=None) as batch_op:
        batch_op.create_unique_constraint(CONSTRAINT, ["org_id", "sha256"])
    print(f"[DB-004] {CONSTRAINT} created; duplicate groups found: 0")  # noqa: T201


def downgrade() -> None:
    with op.batch_alter_table("bank_statements", schema=None) as batch_op:
        batch_op.drop_constraint(CONSTRAINT, type_="unique")
