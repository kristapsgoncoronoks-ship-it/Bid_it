"""users RLS: membership-driven visibility (B1.5)

Finishes the `users.org_id` → memberships contract step at the DATABASE layer.
Since the multi-org split, `users.org_id` is only the ACTIVE-ORG pointer
(repointed by org-switching) — it is no longer a membership assertion. The
original `tenant_isolation` policy on `users` (b2c3d4e5f6a7) still keyed
visibility off `org_id`, so a member whose active org was elsewhere was
invisible to their other orgs even at the raw-SQL layer (SCIM roster,
reimbursement payees, approver resolution, GDPR scans). This migration replaces
that policy with a membership-EXISTS predicate:

  * USING       — GUC unset (intentionally unscoped bootstrap/operator paths)
                  OR the row's user holds a membership in the current org.
                  Membership EXISTENCE, not status: a suspended member is still
                  the org's data (SCIM lists them as inactive; erasure reaches
                  them) — access control is the live-membership gate in deps.
  * WITH CHECK  — the same, PLUS an `org_id`-match disjunct that exists ONLY for
                  the create-user window: SCIM inserts the user row one flush
                  before its membership row inside one scoped transaction, and
                  that insert sets `org_id` to the current org. The disjunct is
                  never read as a membership assertion for VISIBILITY (USING has
                  no such disjunct — fail closed).

Postgres-only; a no-op on SQLite (dev/CI), where the ORM tenant guard applies
the same membership predicate (app/core/tenant.py::_scope_criteria). The
downgrade restores the b2c3d4e5f6a7-era org_id-equality policy verbatim.

No TENANT_TABLES here: `users` remains RLS-covered (declared by b2c3d4e5f6a7)
and remains in TENANT_MODELS, so the set-equality guard is unchanged.

Revision ID: e6a8c0b2d4f6
Revises: d4e6f8a0b2c4
Create Date: 2026-07-26 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'e6a8c0b2d4f6'
down_revision: Union[str, None] = 'd4e6f8a0b2c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MEMBER_EXISTS = (
    "EXISTS (SELECT 1 FROM memberships m "
    "WHERE m.user_id = users.id "
    "AND m.org_id::text = current_setting('app.current_org', true))"
)
_UNSET = "current_setting('app.current_org', true) IS NULL"
_ORG_MATCH = "org_id::text = current_setting('app.current_org', true)"

# The b2c3d4e5f6a7 predicate, restored verbatim on downgrade.
_LEGACY = f"{_UNSET} OR {_ORG_MATCH}"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # RLS is Postgres-only; no-op on SQLite (dev/CI)
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON users")
    op.execute(
        "CREATE POLICY tenant_isolation ON users "
        f"USING ({_UNSET} OR {_MEMBER_EXISTS}) "
        f"WITH CHECK ({_UNSET} OR {_ORG_MATCH} OR {_MEMBER_EXISTS})"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON users")
    op.execute(
        f"CREATE POLICY tenant_isolation ON users USING ({_LEGACY}) WITH CHECK ({_LEGACY})"
    )
