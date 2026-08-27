"""WO-V — the FX triple guard reaches the platform's own invoice table.

One CHECK constraint ADDED. No column, no table, no data change.

WHY THIS TABLE, AND WHY IT WAS MISSED
---------------------------------------
WO-88 and WO-89 built the triple guard — a stored euro may not contradict its
own provenance — and applied it to `fuel_transactions` and
`vat_off_invoice_rebates`. WO-89's own notes recorded that `invoices` and
`expense_items` were left carrying only the WO-8 value-domain check, and named
it a platform finding it was not fixing. This revision fixes the half that CAN
be fixed.

`invoices` is not an incidental table to have missed. It is the AP document the
whole product is built around, and the transport vertical's claim lines resolve
THROUGH it (`claim_lines.build_claim_lines` matches a fuel line's `invoice_ref`
to an invoice). A fuel transaction could not lie about its euro after WO-89; the
invoice it points at still could.

`expense_items` and `expense_reports` are NOT given this constraint, and that is
a decision rather than an omission — see `docs/design/fx-provenance-coverage.md`.
In short: `expense_items` has no EUR column at all (its converted figure is
`amount`, in the REPORT's currency, and is NOT NULL, so the "unknown means the
euro is NULL" clause is unrepresentable), and `expense_reports` carries
`total_eur` with no `fx_source` column to contradict. A constraint copied onto
either would be theatre.

  ck_invoices_fx_provenance   (new)

The existing `ck_invoices_fx_source` (the WO-8 value-domain CHECK) is untouched:
the two say different things and both are wanted. Plain portable SQL, WO-88's
discipline unchanged — no `IS DISTINCT FROM`, and `upper()`, which is immutable
and therefore legal inside a CHECK on both SQLite and PostgreSQL.

PRE-FLIGHT, AND WHY IT REFUSES INSTEAD OF CORRECTING
------------------------------------------------------
WO-89's, in kind and in reasoning. It scans for all three combinations the new
constraint asserts, prints every offending row, and RAISES rather than
correcting one — the rate a violating row should have used cannot be
reconstructed from the row, and rewriting a booked invoice's euro is a business
decision, not a migration's.

Note the one difference from WO-89's pre-flight: `invoices.total_eur` IS
nullable, so an operator has a remedy WO-89's tables could not offer — NULL the
euro and let it be re-derived by `fx.convert`, which is exactly what
`fx_source='unknown'` is for. The refusal message says so.

Revision ID: b1d3f5a7c9e2
Revises: c4e6a8b0d2f5
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b1d3f5a7c9e2"
down_revision = "c4e6a8b0d2f5"
branch_labels = None
depends_on = None

CONSTRAINT = "ck_invoices_fx_provenance"

# Kept as a literal rather than imported from `app.models.fx`: a migration
# records the schema as it was at this revision, and must not change meaning
# later because application code was refactored (the standing rule every
# migration in this tree follows).
# NOTE the `total_eur IS NULL` disjunct in clause 2, which WO-88/89's two
# tables do not carry. Their euro columns are NOT NULL, so every row has a
# converted amount and the clause reads correctly without it. `invoices.
# total_eur` IS nullable: a foreign invoice that has not been converted yet has
# no euro and no provenance, and that is honest rather than a lie. Without this
# disjunct the constraint refuses a legitimate row — a guard that also refuses
# valid data is a worse defect than the one it fixes.
_CHECK = (
    "(fx_source IS NULL OR fx_source <> 'unknown' OR total_eur IS NULL)"
    " AND (total_eur IS NULL OR upper(currency) = 'EUR' OR fx_source IS NOT NULL)"
    " AND (upper(currency) = 'EUR' OR fx_source <> 'eur')"
)

# Every row the new constraint would reject.
_VIOLATION_PREDICATE = (
    "(fx_source = 'unknown' AND total_eur IS NOT NULL)"
    " OR (total_eur IS NOT NULL AND upper(currency) <> 'EUR' AND fx_source IS NULL)"
    " OR (upper(currency) <> 'EUR' AND fx_source = 'eur')"
)


def _report_violations(bind: sa.engine.Connection) -> list[str]:
    rows = bind.execute(
        sa.text(
            "SELECT id, invoice_number, currency, fx_source, total_eur "
            f"FROM invoices WHERE {_VIOLATION_PREDICATE}"
        )
    ).fetchall()
    return [
        "[WO-V]   invoices: "
        + str(
            dict(
                zip(
                    ("id", "invoice_number", "currency", "fx_source", "total_eur"),
                    row,
                    strict=True,
                )
            )
        )
        for row in rows
    ]


def upgrade() -> None:
    bind = op.get_bind()
    problems = _report_violations(bind)
    print(f"[WO-V] {len(problems)} violating rows")  # noqa: T201
    for line in problems:
        print(line)  # noqa: T201
    if problems:
        raise RuntimeError(
            "[WO-V] refusing to migrate: the rows above hold a EUR total their own FX "
            "provenance contradicts — a foreign-currency invoice claiming it was already "
            "euro, one with no provenance at all, or one asserting a conversion was "
            "impossible while carrying its result. Correct them first. Unlike the WO-89 "
            "tables, `invoices.total_eur` is NULLABLE, so the remedy is usually to NULL "
            "the euro and let `fx.convert` re-derive it — that is what `unknown` means."
        )

    with op.batch_alter_table("invoices") as batch:
        batch.create_check_constraint(CONSTRAINT, _CHECK)


def downgrade() -> None:
    with op.batch_alter_table("invoices") as batch:
        batch.drop_constraint(CONSTRAINT, type_="check")
