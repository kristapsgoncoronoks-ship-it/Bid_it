"""DB-014 / DB-020 (audit 2026-09-05, P2 batch 8) — vendor bank identity:
canonical, and tenant-safe to reference.

DB-014 — IBANs stored before validation existed may carry spaces or lower
case. Every write path normalises today (`core.bank_id.assert_iban`) and the
SEPA builder re-validates, but a legacy row compares unequal to its own
canonical form, so the collision surface this batch adds (which vendors of a
workspace share one account) would miss it. This migration rewrites
`vendors.iban`, `users.iban` and `issuer_profiles.iban` in place — whitespace
stripped, upper-cased — ONLY where the canonical form passes the ISO 13616 /
MOD-97 check. A value that does not is REPORTED (masked: last four + length)
and left untouched: it was never valid, the file builder already refuses it,
and a migration must not guess which account was meant. `ix_vendors_org_iban`
serves the lookup.

DB-020 — `invoices.vendor_id` referenced `vendors(id)` alone, unlike the
cost-centre / department / project links on the same table, so an invoice
could structurally carry another workspace's supplier. It is now
`(org_id, vendor_id) → vendors(org_id, id)` — the target `uq_vendors_org_id`
already existed for `vendor_change_requests` — RESTRICT as before (DB-008).
PRE-FLIGHT, FAIL CLOSED: an invoice whose vendor belongs to another workspace
is a tenancy breach to investigate, not a row to migrate; the migration
refuses with the count.

Revision ID: a8b9c0d1e2f3
Revises: e7f9a1c3d5b8

IMPACT: idempotent in-place rewrite of IBAN strings that differ from their
canonical form; one index; one constraint swap on `invoices` (metadata plus a
validation scan of the table under its lock — seconds at production's size).
ROLLBACK: `downgrade` restores the plain FK and drops the index; the
canonical IBANs stay (same accounts, canonical spelling).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.bank_id import is_valid_iban, mask_iban, normalize_iban

revision: str = "a8b9c0d1e2f3"
down_revision: str | None = "e7f9a1c3d5b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IBAN_TABLES: tuple[str, ...] = ("vendors", "users", "issuer_profiles")

# SQLite: batch mode names an unnamed constraint by this convention (the one
# batch 3 used) so it can be dropped.
NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def normalise_ibans(bind, table: str) -> tuple[int, list[str]]:
    """Rewrite every IBAN of `table` that differs from its canonical form and
    is valid in that form. Returns (rows rewritten, masked values left alone
    because they fail validation even once canonical). Exposed so the gate can
    be tested on a bare table."""
    rows = bind.execute(
        sa.text(f"SELECT DISTINCT iban FROM {table} WHERE iban IS NOT NULL")  # noqa: S608 — identifier is ours
    ).fetchall()
    rewritten = 0
    invalid: list[str] = []
    for (raw,) in rows:
        canonical = normalize_iban(raw)
        if not is_valid_iban(canonical):
            invalid.append(mask_iban(canonical) or "(empty)")
            continue
        if canonical != raw:
            rewritten += bind.execute(
                sa.text(f"UPDATE {table} SET iban = :canonical WHERE iban = :raw"),  # noqa: S608
                {"canonical": canonical, "raw": raw},
            ).rowcount
    return rewritten, invalid


def cross_workspace_vendor_links(bind) -> int:
    """Invoices whose vendor belongs to ANOTHER workspace — zero by construction
    (the service resolves vendors inside the caller's org); anything else is a
    breach, not a migration input."""
    return int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM invoices i JOIN vendors v ON v.id = i.vendor_id "
                "WHERE v.org_id <> i.org_id"
            )
        ).scalar()
        or 0
    )


def preflight(bind) -> None:
    cross = cross_workspace_vendor_links(bind)
    if cross:
        raise RuntimeError(
            f"[DB-020] {cross} invoice(s) reference a vendor of ANOTHER workspace; "
            "that is a tenancy breach to investigate, not a row to migrate."
        )


def _sqlite_fk_name(bind, table: str, column: str, parent: str) -> str:
    for fk in sa.inspect(bind).get_foreign_keys(table):
        if fk["constrained_columns"] == [column] and fk["referred_table"] == parent:
            return fk["name"] or f"fk_{table}_{column}_{parent}"
    raise RuntimeError(f"no foreign key {table}.{column} → {parent} to alter")


def upgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"

    # PRE-FLIGHT FIRST, before any DDL or DML. Postgres would roll the whole
    # revision back on a refusal, but SQLite's DDL is not transactional: with
    # the index created first, a refused run left it behind and every retry
    # died with "index ix_vendors_org_iban already exists" — the real reason
    # replaced by a meaningless one. Refusing before anything is written also
    # keeps the log honest: a refused run reports no rewrites it then rolled back.
    preflight(bind)

    # ---- DB-014 -----------------------------------------------------------
    for table in IBAN_TABLES:
        rewritten, invalid = normalise_ibans(bind, table)
        print(  # noqa: T201
            f"[DB-014] {table}.iban: {rewritten} row(s) rewritten in canonical form; "
            f"{len(invalid)} value(s) fail validation and were left untouched"
            + (f": {', '.join(invalid)}" if invalid else "")
        )
    op.create_index("ix_vendors_org_iban", "vendors", ["org_id", "iban"])

    # ---- DB-020 -----------------------------------------------------------
    if pg:
        op.execute(
            "ALTER TABLE invoices DROP CONSTRAINT invoices_vendor_id_fkey, "
            "ADD CONSTRAINT fk_invoices_vendor FOREIGN KEY (org_id, vendor_id) "
            "REFERENCES vendors (org_id, id) ON DELETE RESTRICT"
        )
    else:
        name = _sqlite_fk_name(bind, "invoices", "vendor_id", "vendors")
        with op.batch_alter_table("invoices", schema=None, naming_convention=NAMING) as batch_op:
            batch_op.drop_constraint(name, type_="foreignkey")
            batch_op.create_foreign_key(
                "fk_invoices_vendor",
                "vendors",
                ["org_id", "vendor_id"],
                ["org_id", "id"],
                ondelete="RESTRICT",
            )
    print("[DB-020] invoices.vendor_id is now the composite (org_id, vendor_id) link")  # noqa: T201


def downgrade() -> None:
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"
    if pg:
        op.execute(
            "ALTER TABLE invoices DROP CONSTRAINT fk_invoices_vendor, "
            "ADD CONSTRAINT invoices_vendor_id_fkey FOREIGN KEY (vendor_id) "
            "REFERENCES vendors (id) ON DELETE RESTRICT"
        )
    else:
        with op.batch_alter_table("invoices", schema=None, naming_convention=NAMING) as batch_op:
            batch_op.drop_constraint("fk_invoices_vendor", type_="foreignkey")
            batch_op.create_foreign_key(
                "fk_invoices_vendor_id_vendors",
                "vendors",
                ["vendor_id"],
                ["id"],
                ondelete="RESTRICT",
            )
    op.drop_index("ix_vendors_org_iban", table_name="vendors")
