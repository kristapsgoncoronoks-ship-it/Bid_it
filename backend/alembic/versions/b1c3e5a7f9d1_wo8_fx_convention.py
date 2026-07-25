"""WO-8 — one FX convention: constrain fx_source, correct mis-multiplied expense amounts.

Two things happen here, in order:

1. DATA migration (runs BEFORE the constraints so no legacy value can violate them):
   a. `expense_items.fx_source` free text is normalised through an EXPLICIT mapping
      table; anything unmappable becomes 'unknown' — never a guess. Counts per
      bucket are printed. `invoices.fx_source` is normalised defensively the same
      way (its values were always produced by fx.eur_total and should already be
      canonical).
   b. Expense items whose stored `amount` matches the old MULTIPLY bug
      (amount == round(original × rate, 2) while the one true convention divides:
      amount = round(original / rate, 2)) are corrected — but ONLY on reports no
      human has decided yet (draft / submitted / returned). Items on decided
      reports (approved / rejected / marked_for_reimbursement / reimbursed /
      partially_approved) are FLAGGED in the printed report and left untouched:
      restating a figure a human approved is a business decision, recorded in
      docs/DECISIONS-NEEDED.md, not something a migration decides unilaterally.
   c. Corrected reports get their totals recomputed from the live items. A
      corrected non-EUR report's stamped `total_eur` is set to NULL (the original
      conversion date is unknowable here — NULL surfaces it for re-submission
      rather than guessing); an EUR report's `total_eur` follows its total.
   Every old→new value is printed (row id, old, new) — keep that output as the
   reconciliation artifact. No audit rows are written here: the audit chain is
   hash-chained per tenant by the service layer and a migration writing chain
   rows out-of-band would be worse than the printed report it replaces.

2. SCHEMA: CHECK constraints closing `fx_source` to the enum
   {eur, stated, ecb, unknown} (or NULL) on `expense_items` and `invoices`.

Downgrade drops the CHECK constraints ONLY and restores nothing else: the column
type never changed (String), no value was destroyed, and corrected amounts REMAIN
corrected — re-multiplying them would compound the original error (WO-8 rollback
strategy: after the data migration ran, roll back code only).

Revision ID: b1c3e5a7f9d1
Revises: c2d4f6a8b0e3
Create Date: 2026-07-25
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import sqlalchemy as sa
from alembic import op

revision = "b1c3e5a7f9d1"
down_revision = "c2d4f6a8b0e3"
branch_labels = None
depends_on = None

_CENTS = Decimal("0.01")
FX_SOURCE_CHECK = "fx_source IS NULL OR fx_source IN ('eur', 'stated', 'ecb', 'unknown')"

# Explicit free-text → enum mapping (matched on lower-cased, stripped value).
# Anything absent from this table becomes 'unknown' — never a guess.
FX_SOURCE_MAP = {
    "eur": "eur",
    "stated": "stated",
    "ecb": "ecb",
    "unknown": "unknown",
    # Historical free text observed/plausible in the wild: a human or a bank
    # statement stated the conversion.
    "manual": "stated",
    "card": "stated",
    "bank": "stated",
    "card_statement": "stated",
    "bank_statement": "stated",
    "statement": "stated",
    "receipt": "stated",
}

_UNDECIDED = ("draft", "submitted", "returned")


def _q2(v: Decimal) -> Decimal:
    return v.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _dec(v) -> Decimal:
    return Decimal(str(v))


def _normalise_fx_source(bind, table: str) -> None:
    rows = bind.execute(
        sa.text(
            f"SELECT DISTINCT fx_source FROM {table} WHERE fx_source IS NOT NULL"  # noqa: S608
        )
    ).fetchall()
    buckets: dict[str, int] = {}
    for (old,) in rows:
        new = FX_SOURCE_MAP.get(str(old).strip().lower(), "unknown")
        if new == old:
            continue
        res = bind.execute(
            sa.text(f"UPDATE {table} SET fx_source = :new WHERE fx_source = :old"),  # noqa: S608
            {"new": new, "old": old},
        )
        buckets[f"{old!r} -> {new!r}"] = res.rowcount
    print(f"[WO-8] {table}.fx_source normalisation: " + (str(buckets) if buckets else "nothing to map"))


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1a. fx_source normalisation (before the CHECK constraints land) ------
    _normalise_fx_source(bind, "expense_items")
    _normalise_fx_source(bind, "invoices")

    # --- 1b. correct amounts produced by the multiply bug ---------------------
    rows = bind.execute(
        sa.text(
            """
            SELECT i.id, i.report_id, i.amount, i.original_amount, i.fx_rate,
                   i.currency, r.status, r.currency AS report_currency
              FROM expense_items i
              JOIN expense_reports r ON r.id = i.report_id
             WHERE i.original_amount IS NOT NULL
               AND i.fx_rate IS NOT NULL
               AND i.currency IS NOT NULL
            """
        )
    ).fetchall()
    corrected: list[tuple] = []
    flagged: list[tuple] = []
    touched_reports: set[str] = set()
    for item_id, report_id, amount, original, rate, ccy, status, report_ccy in rows:
        rate_d = _dec(rate)
        if rate_d <= 0 or str(ccy).upper() == str(report_ccy or "EUR").upper():
            continue
        stored = _q2(_dec(amount))
        multiplied = _q2(_dec(original) * rate_d)
        divided = _q2(_dec(original) / rate_d)
        if stored != multiplied or stored == divided:
            continue  # not a product of the bug (or rate ≈ 1: nothing to fix)
        if status in _UNDECIDED:
            bind.execute(
                sa.text("UPDATE expense_items SET amount = :new WHERE id = :id"),
                {"new": str(divided), "id": item_id},
            )
            corrected.append((item_id, report_id, str(stored), str(divided)))
            touched_reports.add(report_id)
        else:
            flagged.append((item_id, report_id, status, str(stored), str(divided)))

    # --- 1c. recompute totals of corrected reports ----------------------------
    for report_id in sorted(touched_reports):
        total = bind.execute(
            sa.text("SELECT COALESCE(SUM(amount), 0) FROM expense_items WHERE report_id = :r"),
            {"r": report_id},
        ).scalar_one()
        rep = bind.execute(
            sa.text("SELECT currency, total, total_eur FROM expense_reports WHERE id = :r"),
            {"r": report_id},
        ).fetchone()
        new_total = str(_q2(_dec(total)))
        if str(rep.currency or "EUR").upper() == "EUR":
            new_total_eur = new_total if rep.total_eur is not None else None
        else:
            # Never guess: the submit-time conversion date is unknowable here.
            new_total_eur = None
        bind.execute(
            sa.text("UPDATE expense_reports SET total = :t, total_eur = :te WHERE id = :r"),
            {"t": new_total, "te": new_total_eur, "r": report_id},
        )
        print(
            f"[WO-8] report {report_id}: total {rep.total} -> {new_total}, "
            f"total_eur {rep.total_eur} -> {new_total_eur}"
        )

    print(f"[WO-8] expense_items corrected (divide convention): {len(corrected)}")
    for item_id, report_id, old, new in corrected:
        print(f"[WO-8]   corrected item {item_id} (report {report_id}): {old} -> {new}")
    print(f"[WO-8] expense_items FLAGGED on decided reports (NOT changed): {len(flagged)}")
    for item_id, report_id, status, old, would_be in flagged:
        print(
            f"[WO-8]   FLAGGED item {item_id} (report {report_id}, {status}): "
            f"stored {old}, correct value would be {would_be} — see docs/DECISIONS-NEEDED.md"
        )

    # --- 2. close the fx_source enum ------------------------------------------
    with op.batch_alter_table("expense_items", schema=None) as batch_op:
        batch_op.create_check_constraint("ck_expense_items_fx_source", FX_SOURCE_CHECK)
    with op.batch_alter_table("invoices", schema=None) as batch_op:
        batch_op.create_check_constraint("ck_invoices_fx_source", FX_SOURCE_CHECK)


def downgrade() -> None:
    # Drop the CHECK constraints only. The column type never changed and no
    # value is destroyed; corrected amounts stay corrected — re-multiplying
    # would compound the original error (see the module docstring).
    with op.batch_alter_table("invoices", schema=None) as batch_op:
        batch_op.drop_constraint("ck_invoices_fx_source", type_="check")
    with op.batch_alter_table("expense_items", schema=None) as batch_op:
        batch_op.drop_constraint("ck_expense_items_fx_source", type_="check")
