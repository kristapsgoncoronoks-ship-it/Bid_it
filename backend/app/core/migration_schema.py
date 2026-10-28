"""Exact PostgreSQL comparison metadata for DB-017/018's existing FK policy.

The portable ORM deliberately uses plain SET NULL. PostgreSQL migrations name
the nullable link column so deleting a parent never nulls the tenant key. Build
a copy for schema comparison, verify the four actual contracts, and leave the
entire remaining schema to Alembic's normal comparison. No diff is filtered and
neither application metadata nor the database is modified.
"""

from __future__ import annotations

from sqlalchemy import MetaData, inspect
from sqlalchemy.engine import Connection

# These exact names and columns are the committed DB-017/018 migration policy.
POSTGRES_COLUMN_SET_NULL = {
    ("expense_items", "fk_expense_items_project"): "project_id",
    ("invoices", "fk_invoices_project"): "project_id",
    ("issued_invoices", "fk_issued_invoices_project"): "project_id",
    ("issued_invoices", "fk_issued_invoices_issuer"): "issuer_id",
}


def postgresql_comparison_metadata(connection: Connection, metadata: MetaData) -> MetaData:
    """Return a checked comparison-only copy; refuse any actual policy drift."""
    if connection.dialect.name != "postgresql":
        raise ValueError("Column-specific SET NULL comparison requires PostgreSQL")
    inspector = inspect(connection)
    for (table, name), column in POSTGRES_COLUMN_SET_NULL.items():
        expected = f"SET NULL ({column})"
        reflected = [fk for fk in inspector.get_foreign_keys(table) if fk["name"] == name]
        if len(reflected) != 1 or reflected[0].get("options", {}).get("ondelete") != expected:
            raise RuntimeError(f"[DB-PG-PARITY-01] {table}.{name} must use {expected}")
        constraints = [
            fk for fk in metadata.tables[table].foreign_key_constraints if fk.name == name
        ]
        if (
            len(constraints) != 1
            or constraints[0].ondelete != "SET NULL"
            or any(element.ondelete != "SET NULL" for element in constraints[0].elements)
        ):
            raise RuntimeError(f"[DB-PG-PARITY-01] {table}.{name} portable ORM contract changed")
    # Validate the original objects before cloning: Table.to_metadata can
    # normalize inconsistent ForeignKey element options from their constraint.
    comparison = MetaData(naming_convention=metadata.naming_convention)
    for model_table in metadata.sorted_tables:
        model_table.to_metadata(comparison)
    for (table, name), column in POSTGRES_COLUMN_SET_NULL.items():
        copied_constraints = [
            fk for fk in comparison.tables[table].foreign_key_constraints if fk.name == name
        ]
        if len(copied_constraints) != 1:
            raise RuntimeError(f"[DB-PG-PARITY-01] {table}.{name} comparison clone changed")
        constraint = copied_constraints[0]
        expected = f"SET NULL ({column})"
        constraint.ondelete = expected
        for foreign_key in constraint.elements:
            foreign_key.ondelete = expected
    return comparison
