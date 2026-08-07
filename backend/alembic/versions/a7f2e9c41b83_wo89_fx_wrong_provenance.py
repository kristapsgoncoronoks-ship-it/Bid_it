"""WO-89 — FX provenance honesty: a non-EUR row may not claim the EUR identity.

Two CHECK constraints REPLACED (dropped and recreated with a third conjunct),
no new constraint name, no column, no table, no data change.

WHY
-----
WO-88 (`e4a7c1d92f08`) closed the two combinations in which a stored euro denies
that a rate was USED — `fx_source='unknown'` beside a EUR figure, and a non-EUR
currency with no provenance at all. It deliberately left open the one in which
the euro denies a rate was NEEDED: a non-EUR document currency carrying
`fx_source='eur'`, which `app/models/fx.py` defines as *"the amount was already
EUR (identity, rate 1)"*. WO-88's expression passes such a row — the provenance
is neither `unknown` nor NULL — and so, before WO-89, did the one service that
writes `fuel_transactions`: a PLN line asserting `net_eur = 1400.00` with the
provenance "no conversion was required" was stored on request and flowed into
the VAT claim lines, the WO-83 demand letter, the tie-out, the close and the
recovery dashboard as an honest euro.

  1. ck_fuel_transactions_fx_provenance          (replaced, same name)
  2. ck_vat_off_invoice_rebates_fx_provenance    (replaced, same name)

The names are DELIBERATELY reused. Each constraint means "this row's euro does
not contradict its own provenance", which is what it always meant; WO-88 could
only state two thirds of it. A second, differently-named constraint beside it
would fragment one invariant across two objects — the storage-layer form of the
rival-predicate mistake. Neither dialect can ALTER a CHECK expression in place,
so the cost of one name is a drop-and-recreate inside `batch_alter_table`.
`ck_vat_off_invoice_rebates_fx_source` (WO-88's value-domain CHECK) is untouched.

Plain portable SQL, WO-88's discipline unchanged: no `IS DISTINCT FROM` (SQLite
gained it only in 3.39), and `upper()`, which is immutable — and therefore legal
inside a CHECK — on both SQLite and PostgreSQL. The `<eur column> IS NULL`
disjunct of clause 1 is likewise kept, for the reason WO-88 recorded.

PRE-FLIGHT, AND WHY IT REFUSES INSTEAD OF CORRECTING
------------------------------------------------------
Identical in kind to WO-88's, and deliberately WIDER: it scans for all THREE
combinations, not only the new one. A migration that DROPS a constraint before
recreating it must verify everything the recreated constraint asserts — a
database restored from before WO-88, or one that has been through this
revision's own downgrade, can hold either older shape.

It cannot correct a violating row and does not pretend to: the rate the row
should have used cannot be reconstructed from the row, the euro column is NOT
NULL so it cannot be nulled, and deleting validated transaction history (or a
recorded rebate document) is a business decision, not a migration's. So it
prints every offending row and RAISES, leaving the schema untouched. Fail
CLOSED: a refusal an operator can act on beats a guess nobody can audit.

At the time of writing no such row exists anywhere in the tree (verified: zero
rows in all four FX-bearing tables in the dev database, no migration inserts
one, `app/seed.py` writes none, and the wrong-provenance shape appears in no
fixture — the single instance WO-88 found was corrected in WO-88 itself).

Revision ID: a7f2e9c41b83
Revises: e4a7c1d92f08
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7f2e9c41b83"
down_revision = "e4a7c1d92f08"
branch_labels = None
depends_on = None


def _provenance_check(eur_column: str) -> str:
    """The WO-89 expression — WO-88's two clauses plus the identity clause."""
    return (
        f"(fx_source IS NULL OR fx_source <> 'unknown' OR {eur_column} IS NULL)"
        " AND (upper(currency) = 'EUR' OR fx_source IS NOT NULL)"
        " AND (upper(currency) = 'EUR' OR fx_source <> 'eur')"
    )


def _wo88_provenance_check(eur_column: str) -> str:
    """WO-88's expression verbatim — what `downgrade()` restores, so a rollback
    lands on the WO-88 invariant rather than on no invariant at all."""
    return (
        f"(fx_source IS NULL OR fx_source <> 'unknown' OR {eur_column} IS NULL)"
        " AND (upper(currency) = 'EUR' OR fx_source IS NOT NULL)"
    )


# (table, eur column, constraint name, the columns worth printing on a violation)
_TABLES = (
    (
        "fuel_transactions",
        "net_eur",
        "ck_fuel_transactions_fx_provenance",
        ("id", "supplier", "period", "currency", "fx_source"),
    ),
    (
        "vat_off_invoice_rebates",
        "amount_eur",
        "ck_vat_off_invoice_rebates_fx_provenance",
        ("id", "supplier", "period", "currency", "fx_source"),
    ),
)

# Every row the RECREATED constraint would reject — all three combinations, not
# only the one this revision adds (see the module docstring).
_VIOLATION_PREDICATE = (
    "fx_source = 'unknown'"
    " OR (upper(currency) <> 'EUR' AND fx_source IS NULL)"
    " OR (upper(currency) <> 'EUR' AND fx_source = 'eur')"
)


def _report_violations(bind: sa.engine.Connection) -> list[str]:
    """Print every row the new constraints would reject, and return the lines so
    the caller can raise with them."""
    problems: list[str] = []
    for table, eur_column, _name, columns in _TABLES:
        selected = ", ".join(columns)
        rows = bind.execute(
            sa.text(f"SELECT {selected}, {eur_column} FROM {table} WHERE {_VIOLATION_PREDICATE}")
        ).fetchall()
        for row in rows:
            problems.append(
                f"[WO-89]   {table}: {dict(zip([*columns, eur_column], row, strict=True))}"
            )
    return problems


def upgrade() -> None:
    bind = op.get_bind()
    problems = _report_violations(bind)
    print(f"[WO-89] {len(problems)} violating rows")
    for line in problems:
        print(line)
    if problems:
        raise RuntimeError(
            "[WO-89] refusing to migrate: the rows above assert a EUR figure their own FX "
            "provenance contradicts — a foreign amount claiming it was already euro, or a "
            "euro no rate produced. This migration will not invent a rate, cannot NULL a "
            "NOT NULL euro column, and will not delete transaction history on its own "
            "authority. Re-ingest the affected statement through statement_ingest (which "
            "resolves or refuses the conversion properly), or remove the rows deliberately, "
            "then re-run."
        )

    for table, eur_column, name, _columns in _TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(name, type_="check")
            batch_op.create_check_constraint(name, _provenance_check(eur_column))


def downgrade() -> None:
    # Restores WO-88's exact expressions: a rollback relaxes the floor back to
    # the WO-88 invariant, it does not remove it. Nothing is lost — no value was
    # written, no column changed, and every row legal under WO-88 stays legal.
    for table, eur_column, name, _columns in reversed(_TABLES):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(name, type_="check")
            batch_op.create_check_constraint(name, _wo88_provenance_check(eur_column))
