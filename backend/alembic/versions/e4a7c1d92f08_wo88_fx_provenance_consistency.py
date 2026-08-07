"""WO-88 — FX provenance consistency: a stored EUR figure may never contradict
its own provenance.

Three CHECK constraints, no column, no table, no data change.

WHY
-----
`app/models/fx.py` defines `fx_source='unknown'` as *"no rate available → the
EUR figure is NULL, never a guessed number"* (ADR-0010, master-context §4.15).
`fuel_transactions.net_eur` and `vat_off_invoice_rebates.amount_eur` are both
NOT NULL, so on those tables the honest outcome of "no rate" is NO ROW — which
is exactly the branch `statement_ingest._resolve_line` and `rebate._resolve_eur`
already take. Nothing enforced that: the pre-WO-88 constraints
(`ck_fuel_transactions_fx_source`) closed only the VALUE DOMAIN, never the
combination, so a row could assert "no rate was available" and "€1,400.00" at
once. `vat_off_invoice_rebates` had no `fx_source` constraint of any kind — not
even the value-domain one every other FX-bearing table has carried since WO-8.

  1. ck_fuel_transactions_fx_provenance
  2. ck_vat_off_invoice_rebates_fx_source          (the WO-8 value domain)
  3. ck_vat_off_invoice_rebates_fx_provenance

Plain portable SQL: no `IS DISTINCT FROM` (SQLite gained it only in 3.39), and
`upper()`, which is immutable — and therefore legal inside a CHECK — on both
SQLite and PostgreSQL. The `<eur column> IS NULL` disjunct is kept deliberately
even though both columns are NOT NULL today: it states the invariant the way
ADR-0010 states it, so a future order that makes an EUR column nullable
inherits a constraint that still means the right thing.

PRE-FLIGHT, AND WHY IT REFUSES INSTEAD OF CORRECTING
------------------------------------------------------
The WO-8 precedent (`b1c3e5a7f9d1`) runs the data pass BEFORE the constraints
"so no legacy value can violate them", printing a reconciliation report. This
migration does the same inspection and prints the same kind of report — but it
CANNOT correct a violating row, and does not pretend to: recomputing the euro
needs a rate that by construction does not exist, NULLing it is impossible on a
NOT NULL column, and deleting validated transaction history (or a recorded
rebate document) is a business decision, not a migration's. So it prints every
offending row and RAISES, leaving the schema untouched. Fail CLOSED: a refusal
an operator can act on beats a guess nobody can audit.

At the time of writing no such row exists anywhere in the tree (verified: zero
rows in the dev database, no migration inserts one, `app/seed.py` writes none,
and the only production writer — `fuel_ingest.ingest_transaction` via
`statement_ingest` — always supplies `eur`/`stated`/`ecb`).

Revision ID: e4a7c1d92f08
Revises: b3d8f1c04e97
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e4a7c1d92f08"
down_revision = "b3d8f1c04e97"
branch_labels = None
depends_on = None

# The value domain, verbatim from `app/models/fx.py::FX_SOURCE_CHECK` (restated
# rather than imported — a migration must keep working when the model moves on).
FX_SOURCE_CHECK = "fx_source IS NULL OR fx_source IN ('eur', 'stated', 'ecb', 'unknown')"

FX_SOURCES = ("eur", "stated", "ecb", "unknown")


def _provenance_check(eur_column: str) -> str:
    return (
        f"(fx_source IS NULL OR fx_source <> 'unknown' OR {eur_column} IS NULL)"
        " AND (upper(currency) = 'EUR' OR fx_source IS NOT NULL)"
    )


# (table, eur column, the columns worth printing when a row violates)
_TABLES = (
    ("fuel_transactions", "net_eur", ("id", "supplier", "period", "currency", "fx_source")),
    (
        "vat_off_invoice_rebates",
        "amount_eur",
        ("id", "supplier", "period", "currency", "fx_source"),
    ),
)


def _report_violations(bind: sa.engine.Connection) -> list[str]:
    """Print every row that the new constraints would reject, and return the
    lines so the caller can raise with them. Two classes of violation: the
    provenance CONTRADICTION (both tables) and, on the rebate table only, an
    out-of-domain `fx_source` value the WO-8 CHECK will now close."""
    problems: list[str] = []
    for table, eur_column, columns in _TABLES:
        selected = ", ".join(columns)
        rows = bind.execute(
            sa.text(
                f"SELECT {selected}, {eur_column} FROM {table} "
                "WHERE fx_source = 'unknown' "
                "   OR (upper(currency) <> 'EUR' AND fx_source IS NULL)"
            )
        ).fetchall()
        for row in rows:
            problems.append(
                f"[WO-88]   {table}: {dict(zip([*columns, eur_column], row, strict=True))}"
            )

        if table == "vat_off_invoice_rebates":
            domain = ", ".join(f"'{v}'" for v in FX_SOURCES)
            bad_domain = bind.execute(
                sa.text(
                    f"SELECT {selected} FROM {table} "
                    f"WHERE fx_source IS NOT NULL AND fx_source NOT IN ({domain})"
                )
            ).fetchall()
            for row in bad_domain:
                problems.append(
                    f"[WO-88]   {table}: fx_source outside the enum — "
                    f"{dict(zip(columns, row, strict=True))}"
                )
    return problems


def upgrade() -> None:
    bind = op.get_bind()
    problems = _report_violations(bind)
    print(f"[WO-88] {len(problems)} violating rows")
    for line in problems:
        print(line)
    if problems:
        raise RuntimeError(
            "[WO-88] refusing to migrate: the rows above assert a EUR figure that no "
            "exchange rate produced. This migration will not invent a rate, cannot NULL a "
            "NOT NULL euro column, and will not delete transaction history on its own "
            "authority. Re-ingest the affected statement through statement_ingest (which "
            "refuses an unconvertible line properly), or remove the rows deliberately, "
            "then re-run."
        )

    with op.batch_alter_table("fuel_transactions", schema=None) as batch_op:
        batch_op.create_check_constraint(
            "ck_fuel_transactions_fx_provenance", _provenance_check("net_eur")
        )
    with op.batch_alter_table("vat_off_invoice_rebates", schema=None) as batch_op:
        batch_op.create_check_constraint("ck_vat_off_invoice_rebates_fx_source", FX_SOURCE_CHECK)
        batch_op.create_check_constraint(
            "ck_vat_off_invoice_rebates_fx_provenance", _provenance_check("amount_eur")
        )


def downgrade() -> None:
    # Drops the three constraints and nothing else — no column changed, no value
    # was written, and the pre-flight above refuses rather than correcting, so
    # there is nothing to restore.
    with op.batch_alter_table("vat_off_invoice_rebates", schema=None) as batch_op:
        batch_op.drop_constraint("ck_vat_off_invoice_rebates_fx_provenance", type_="check")
        batch_op.drop_constraint("ck_vat_off_invoice_rebates_fx_source", type_="check")
    with op.batch_alter_table("fuel_transactions", schema=None) as batch_op:
        batch_op.drop_constraint("ck_fuel_transactions_fx_provenance", type_="check")
