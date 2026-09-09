"""RLS: restore the WO-27 empty-string leg on every policy created since the fix

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-09-09

Found by the P2 batch 9 security panel while reviewing PROD-009's own new
policy, then confirmed against a live Postgres 16 catalogue.

`6fec8c88ba7c` (WO-27, ADR-0028) established that `current_setting(
'app.current_org', true)` never returns SQL NULL again once any transaction on
that physical connection has set it — it returns `''` for the rest of the
connection's life. Every "unscoped" leg therefore has to read

    current_setting(...) IS NULL OR current_setting(...) = ''

and that migration rewrote all 55 policies that existed at the time. What it
could not do is change the TEMPLATE: every tenant table added since copied the
pre-fix two-leg predicate out of an older migration, and nothing checked. The
catalogue on 2026-09-09 held **45** such tables — including PROD-009's own
`workspace_exports` and WO-AI's `archive_exports`, whose one-time download
routes are unauthenticated and therefore run UNSCOPED, which is precisely the
case the missing leg breaks.

Consequence on those 45 tables, pre-repair: an unscoped query on a pooled
connection that had ever been scoped matched NEITHER leg and returned ZERO
rows. It hides rows, it never leaks them — but "the emailed download link
404s depending on which connection the pool hands back" is not a defect a
customer can be asked to live with, and the same shape breaks any unscoped
platform-operator or scheduler read of those tables.

This repair is derived from the CATALOGUE, not from a hand-written list: it
rewrites every `tenant_isolation` policy whose `USING` expression lacks the
`= ''` leg, so it fixes exactly what is broken on whatever database it runs
against and is a no-op on one already correct. `users` keeps its
membership-driven form (`e6a8c0b2d4f6`). `tests/test_rls_predicate_shape_pg.py`
holds the shape from now on, which is the part that stops this recurring.

Widening only: the tenant-match leg is untouched, so no policy admits a row it
did not admit before except in the unscoped case the fix is for.
"""

from __future__ import annotations

from typing import Union

from alembic import op
from sqlalchemy import text

revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None

_UNSET = (
    "current_setting('app.current_org', true) IS NULL "
    "OR current_setting('app.current_org', true) = ''"
)
_LEGACY_UNSET = "current_setting('app.current_org', true) IS NULL"
_ORG_MATCH = "org_id::text = current_setting('app.current_org', true)"
_MEMBER_EXISTS = (
    "EXISTS (SELECT 1 FROM memberships m "
    "WHERE m.user_id = users.id "
    "AND m.org_id::text = current_setting('app.current_org', true))"
)

#: Policies whose USING expression does not already carry the empty-string leg.
_FIND_LEGACY = text(
    "SELECT c.relname FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
    "WHERE p.polname = 'tenant_isolation' "
    "AND pg_get_expr(p.polqual, p.polrelid) NOT LIKE '%= ''''::text%' "
    "ORDER BY c.relname"
)
#: The reverse, for the downgrade: policies that DO carry it.
_FIND_FIXED = text(
    "SELECT c.relname FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
    "WHERE p.polname = 'tenant_isolation' "
    "AND pg_get_expr(p.polqual, p.polrelid) LIKE '%= ''''::text%' "
    "ORDER BY c.relname"
)


def _rewrite(table: str, unset: str) -> None:
    if table == "users":
        using = f"{unset} OR {_MEMBER_EXISTS}"
        check = f"{unset} OR {_ORG_MATCH} OR {_MEMBER_EXISTS}"
    else:
        using = check = f"{unset} OR {_ORG_MATCH}"
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({using}) WITH CHECK ({check})")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # RLS is Postgres-only; the SQLite dev/test path has no policies
    tables = [row[0] for row in bind.execute(_FIND_LEGACY)]
    for table in tables:
        _rewrite(table, _UNSET)
    print(f"[RLS] restored the empty-string leg on {len(tables)} policy/policies")


def downgrade() -> None:
    """Put every policy back on the pre-WO-27 two-leg predicate.

    Symmetric with the upgrade and derived the same way. It restores a KNOWN
    DEFECT on purpose — a downgrade's job is to return the schema to what the
    previous revision described, not to keep the improvement.
    """
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for row in bind.execute(_FIND_FIXED):
        _rewrite(row[0], _LEGACY_UNSET)
