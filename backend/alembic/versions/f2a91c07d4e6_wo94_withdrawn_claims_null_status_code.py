"""WO-94 — a withdrawn claim carries no workflow code (D7 backfill).

Data only. No column, no table, no index, no constraint, no RLS policy — the
tenant-table set is unchanged, so `tests/test_rls.py::
test_rls_migration_covers_every_tenant_table` needs no new entry.

WHY
-----
`BA_fleet_fuel.md` §3.D **D7**: *"Only `withdraw_claim` releases, and it also
NULLs `status_code`."* Until WO-94, `app/services/transport/lock.py::
withdraw_claim` set `status = 'withdrawn'` and left the code alone, so every
claim withdrawn before this revision still carries the `"2"` `submit_claim`
stamps — or whatever `status.set_status_code` last wrote (`2B`, `3D`, …).
Such a row reads as *submitted* / *under appeal* beside a `withdrawn` engine
status, on `/vat-claims` and in any export of the claim record.

The service fix stops NEW rows taking that shape. This revision repairs the
ones already stored.

WHY IT REPAIRS RATHER THAN REFUSES
------------------------------------
The two FX provenance revisions (`e4a7c1d92f08`, `a7f2e9c41b83`) print their
offending rows and RAISE, because the value those rows should have held — an
exchange rate nobody recorded — cannot be reconstructed from the row.

This one is the opposite case, and the difference is the whole reason it is
allowed to write. D7 states the one correct value for every matching row, and
it is a constant: NULL. Nothing is guessed, nothing is derived, and no euro is
touched (a claim's money is its frozen `vat_eur` column; no amount, fee or
status anywhere is computed from `status_code`). Refusing to start the
application until an operator hand-nulls a column whose correct value the
specification prints would be ceremony, not caution.

It still COUNTS AND PRINTS what it is about to change, so the repair appears in
the migration output as a number an operator can check against their own
expectation, and it is idempotent — the predicate matches nothing on a second
run.

DOWNGRADE
-----------
A documented no-op. The pre-repair value cannot be restored (it is not recorded
anywhere else, and there is exactly one right answer for these rows anyway), and
restoring a value the rule forbids would be a service to no one. Downgrading
past this revision leaves the repaired rows repaired, which is the correct end
state under D7 either way.

Revision ID: f2a91c07d4e6
Revises: c8b4e2a71d05
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f2a91c07d4e6"
down_revision = "c8b4e2a71d05"
branch_labels = None
depends_on = None

# The inconsistent pair D7 forbids: withdrawn, and still coded. Written once
# and reused by the count and the update so the report can never describe a
# different set of rows from the one that is repaired.
_LEGACY_SHAPE = "status = 'withdrawn' AND status_code IS NOT NULL"


def upgrade() -> None:
    bind = op.get_bind()
    affected = bind.execute(
        sa.text(f"SELECT count(*) FROM vat_refund_claims WHERE {_LEGACY_SHAPE}")
    ).scalar_one()
    print(
        f"WO-94/D7: clearing status_code on {affected} withdrawn claim(s) "
        "(a withdrawn claim carries no workflow code)."
    )
    if affected:
        bind.execute(
            sa.text(f"UPDATE vat_refund_claims SET status_code = NULL WHERE {_LEGACY_SHAPE}")
        )


def downgrade() -> None:
    """Deliberately empty — see the module docstring. The cleared codes are not
    recoverable, and the rule that cleared them is the specification's."""
