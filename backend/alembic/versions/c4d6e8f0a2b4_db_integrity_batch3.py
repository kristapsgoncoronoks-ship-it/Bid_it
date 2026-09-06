"""Audit 2026-09-05, P2 batch 3 — the database keeps four promises the code
had been keeping alone.

DB-007/008/009 — cascades that erased records become RESTRICT
------------------------------------------------------------
`audit_events.org_id` and `archived_invoices.org_id` cascaded from
`organizations`; `invoices.vendor_id` cascaded from `vendors`;
`expense_reports.employee_id` and `expense_transactions.employee_id` cascaded
from `users`. Each of those parents is a master row and each child is a
record — the audit trail, the retention archive, supplier invoices with
payment history, expense claims. No code path hard-deletes these parents
today (vendors and users are soft-deleted or pseudonymised), so the cascades
were latent — which is exactly when to change them: RESTRICT costs nothing
now and refuses the deletion that would one day be written without knowing
what hangs off it. On SQLite the same constraints were created unnamed, so
batch mode names them by convention to drop and recreate them (the drift
check compares the delete rule, so the migrated SQLite schema must say
RESTRICT too).

DB-017 + DB-018 — composite tenant-safe links that detach the right column
--------------------------------------------------------------------------
`issued_invoices.issuer_id` referenced `issuer_profiles(id)` alone, although
the target table carries `uq_issuer_profiles_org_id_id` for a composite,
tenant-safe reference. It is now `(org_id, issuer_id) → (org_id, id)`.

Measured while doing so (DB-018, CONFIRMED on Postgres 16): every composite
`(org_id, project_id) → projects(org_id, id) ON DELETE SET NULL` — on
`invoices`, `issued_invoices` and `expense_items` — nulls BOTH referencing
columns on a project delete, so the delete fails on `org_id NOT NULL`
instead of detaching the documents the comment beside each FK promises to
keep. Postgres ≥ 15 can name the columns to null: `ON DELETE SET NULL
(project_id)`. The three project links and the new issuer link are
recreated that way on Postgres; SQLite has no column-list form and no
project-delete path in the suite, so it keeps the plain composite FK.

DB-011 — CHECKs on the six financial state columns, with pre-flight
-------------------------------------------------------------------
`payment_runs.status`, `reimbursement_batches.status`,
`expense_reports.status`, `vat_refund_claims.status`,
`issued_invoices.lifecycle` and `billing_payments.state` were unconstrained
strings on the tables whose state decides what gets paid, exported or
claimed. Each gains a CHECK naming the closed set the code writes. PRE-FLIGHT,
FAIL CLOSED: a value outside its set already in the table means the
constraint cannot be created and something wrote it — the migration refuses
with the table, the values and their counts named. One value is normalised
instead of refused, because it is exact: `issued_invoices.lifecycle = 'paid'`
(written by the demo seed) — "paid" is DERIVED from `amount_paid`, and the
status resolver treated such rows as `issued`, which is what they become.

Revision ID: c4d6e8f0a2b4
Revises: b3c5d7e9f1a3
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4d6e8f0a2b4"
down_revision: str | None = "b3c5d7e9f1a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# --- DB-007/008/009: (table, constraint, column, parent, previous action) ---
RESTRICTED: tuple[tuple[str, str, str, str], ...] = (
    ("audit_events", "audit_events_org_id_fkey", "org_id", "organizations"),
    ("archived_invoices", "fk_archived_invoices_org", "org_id", "organizations"),
    ("invoices", "invoices_vendor_id_fkey", "vendor_id", "vendors"),
    ("expense_reports", "expense_reports_employee_id_fkey", "employee_id", "users"),
    ("expense_transactions", "expense_transactions_employee_id_fkey", "employee_id", "users"),
)

# --- DB-018: composite project links whose SET NULL must name project_id only ---
PROJECT_LINKS: tuple[tuple[str, str], ...] = (
    ("invoices", "fk_invoices_project"),
    ("issued_invoices", "fk_issued_invoices_project"),
    ("expense_items", "fk_expense_items_project"),
)

# --- DB-011: (table, column, constraint, allowed values) ---
STATE_SETS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("payment_runs", "status", "ck_payment_runs_status", ("open", "approved", "paid", "cancelled")),
    (
        "reimbursement_batches",
        "status",
        "ck_reimbursement_batches_status",
        ("open", "paid", "cancelled"),
    ),
    (
        "expense_reports",
        "status",
        "ck_expense_reports_status",
        (
            "draft",
            "submitted",
            "partially_approved",
            "approved",
            "rejected",
            "returned",
            "marked_for_reimbursement",
            "reimbursed",
        ),
    ),
    (
        "vat_refund_claims",
        "status",
        "ck_vat_refund_claims_status",
        ("draft", "submitted", "approved", "paid", "withdrawn", "rejected"),
    ),
    (
        "issued_invoices",
        "lifecycle",
        "ck_issued_invoices_lifecycle",
        ("draft", "approved", "issued", "disputed", "written_off", "cancelled"),
    ),
    (
        "billing_payments",
        "state",
        "ck_billing_payments_state",
        ("initial", "pending", "settled", "failed", "voided", "abandoned"),
    ),
)


# SQLite: the initial migration created these FKs without names; batch mode
# names an unnamed constraint by this convention so it can be dropped.
NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _in_list(values: Sequence[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _sqlite_fk_name(bind, table: str, column: str, parent: str) -> str:
    """The name batch mode will see for `table.column → parent`: the declared
    one, or the NAMING convention's for an unnamed constraint."""
    for fk in sa.inspect(bind).get_foreign_keys(table):
        if fk["constrained_columns"] == [column] and fk["referred_table"] == parent:
            return fk["name"] or f"fk_{table}_{column}_{parent}"
    raise RuntimeError(f"no foreign key {table}.{column} → {parent} to alter")


def _sqlite_set_delete_rule(bind, action: str) -> None:
    for table, _pg_name, column, parent in RESTRICTED:
        name = _sqlite_fk_name(bind, table, column, parent)
        with op.batch_alter_table(table, schema=None, naming_convention=NAMING) as batch_op:
            batch_op.drop_constraint(name, type_="foreignkey")
            batch_op.create_foreign_key(name, parent, [column], ["id"], ondelete=action)


def offending_values(
    bind, table: str, column: str, allowed: Sequence[str]
) -> list[tuple[str, int]]:
    """Every distinct value of `table.column` outside `allowed`, with its row
    count. Empty means the CHECK can be created. Exposed so the gate can be
    tested on a bare table."""
    rows = bind.execute(
        sa.text(
            f"SELECT {column}, COUNT(*) AS n FROM {table} "  # noqa: S608 — identifiers are ours
            f"WHERE {column} NOT IN ({_in_list(allowed)}) OR {column} IS NULL "
            f"GROUP BY {column} ORDER BY {column}"
        )
    ).fetchall()
    return [(str(v), int(n)) for v, n in rows]


def _preflight(bind) -> None:
    # The one exact normalisation, before the check that would refuse it.
    normalised = bind.execute(
        sa.text("UPDATE issued_invoices SET lifecycle = 'issued' WHERE lifecycle = 'paid'")
    ).rowcount
    if normalised:
        print(  # noqa: T201
            f"[DB-011] issued_invoices.lifecycle: {normalised} row(s) 'paid' → 'issued' "
            "(a derived status stored by the demo seed; the resolver already read it as issued)"
        )
    problems: list[str] = []
    for table, column, _name, allowed in STATE_SETS:
        found = offending_values(bind, table, column, allowed)
        if found:
            listing = ", ".join(f"{v!r} ×{n}" for v, n in found)
            problems.append(f"{table}.{column}: {listing}")
    if problems:
        raise RuntimeError(
            "[DB-011] refusing to add the state CHECKs: values outside the closed set exist — "
            + "; ".join(problems)
            + ". Something wrote them; decide what each row IS, correct it, then re-run."
        )


def upgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"

    # ---- DB-011 (both dialects) --------------------------------------------
    _preflight(bind)
    for table, column, name, allowed in STATE_SETS:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.create_check_constraint(name, f"{column} IN ({_in_list(allowed)})")
    print(f"[DB-011] {len(STATE_SETS)} state CHECKs created; offending rows found: 0")  # noqa: T201

    # ---- DB-017 (both dialects; Postgres adds the column list) --------------
    # A legacy issuer_id pointing at another org's issuer would break the
    # composite key. By construction there are none; say so if there are.
    cross = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM issued_invoices i JOIN issuer_profiles p ON p.id = i.issuer_id "
            "WHERE p.org_id <> i.org_id"
        )
    ).scalar()
    if cross:
        raise RuntimeError(
            f"[DB-017] {cross} issued invoice(s) reference an issuer of ANOTHER workspace; "
            "that is a tenancy breach to investigate, not a row to migrate."
        )
    if pg:
        op.execute(
            "ALTER TABLE issued_invoices DROP CONSTRAINT fk_issued_invoices_issuer, "
            "ADD CONSTRAINT fk_issued_invoices_issuer FOREIGN KEY (org_id, issuer_id) "
            "REFERENCES issuer_profiles (org_id, id) ON DELETE SET NULL (issuer_id)"
        )
    else:
        with op.batch_alter_table("issued_invoices", schema=None) as batch_op:
            batch_op.drop_constraint("fk_issued_invoices_issuer", type_="foreignkey")
            batch_op.create_foreign_key(
                "fk_issued_invoices_issuer",
                "issuer_profiles",
                ["org_id", "issuer_id"],
                ["org_id", "id"],
                ondelete="SET NULL",
            )

    if not pg:
        _sqlite_set_delete_rule(bind, "RESTRICT")
        print(  # noqa: T201
            f"[DB-007/008/009] {len(RESTRICTED)} cascades are now RESTRICT; "
            "[DB-018] SQLite has no column-list SET NULL — project links unchanged"
        )
        return

    # ---- DB-018 (Postgres) --------------------------------------------------
    for table, name in PROJECT_LINKS:
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT {name}, "
            f"ADD CONSTRAINT {name} FOREIGN KEY (org_id, project_id) "
            "REFERENCES projects (org_id, id) ON DELETE SET NULL (project_id)"
        )
    print(f"[DB-018] {len(PROJECT_LINKS)} project links now SET NULL (project_id) only")  # noqa: T201

    # ---- DB-007/008/009 (Postgres) -----------------------------------------
    for table, name, column, parent in RESTRICTED:
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT {name}, "
            f"ADD CONSTRAINT {name} FOREIGN KEY ({column}) REFERENCES {parent} (id) "
            "ON DELETE RESTRICT"
        )
    print(f"[DB-007/008/009] {len(RESTRICTED)} cascades are now RESTRICT")  # noqa: T201


def downgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"
    if pg:
        for table, name, column, parent in RESTRICTED:
            op.execute(
                f"ALTER TABLE {table} DROP CONSTRAINT {name}, "
                f"ADD CONSTRAINT {name} FOREIGN KEY ({column}) REFERENCES {parent} (id) "
                "ON DELETE CASCADE"
            )
        for table, name in PROJECT_LINKS:
            op.execute(
                f"ALTER TABLE {table} DROP CONSTRAINT {name}, "
                f"ADD CONSTRAINT {name} FOREIGN KEY (org_id, project_id) "
                "REFERENCES projects (org_id, id) ON DELETE SET NULL"
            )
        op.execute(
            "ALTER TABLE issued_invoices DROP CONSTRAINT fk_issued_invoices_issuer, "
            "ADD CONSTRAINT fk_issued_invoices_issuer FOREIGN KEY (issuer_id) "
            "REFERENCES issuer_profiles (id) ON DELETE SET NULL"
        )
    else:
        _sqlite_set_delete_rule(bind, "CASCADE")
        with op.batch_alter_table("issued_invoices", schema=None) as batch_op:
            batch_op.drop_constraint("fk_issued_invoices_issuer", type_="foreignkey")
            batch_op.create_foreign_key(
                "fk_issued_invoices_issuer",
                "issuer_profiles",
                ["issuer_id"],
                ["id"],
                ondelete="SET NULL",
            )
    for table, _column, name, _allowed in STATE_SETS:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(name, type_="check")
