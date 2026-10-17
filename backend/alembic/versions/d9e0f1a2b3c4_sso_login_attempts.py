"""Add ephemeral browser-bound OIDC attempts; no existing records change.

IMPACT: creates sso_login_attempts and its tenant policy/index; no money rows.
PREFLIGHT/REFUSAL: additive empty table; no source rows require conversion.
POSTCONDITION: attempts have unique state hashes, a tenant and finite expiry.
ROLLBACK: drops only pending/consumed login attempts; users restart SSO login.
"""

import sqlalchemy as sa

from alembic import op
from app.models.base import GUID

revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None
TENANT_TABLES = ("sso_login_attempts",)


def upgrade():
    op.create_table(
        "sso_login_attempts",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "org_id", GUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("browser_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_sso_login_attempts_org_expiry", "sso_login_attempts", ["org_id", "expires_at"]
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE sso_login_attempts ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE sso_login_attempts FORCE ROW LEVEL SECURITY")
        predicate = "(current_setting('app.current_org', true) IS NULL OR current_setting('app.current_org', true) = '' OR org_id::text = current_setting('app.current_org', true))"
        op.execute(
            f"CREATE POLICY tenant_isolation ON sso_login_attempts USING ({predicate}) WITH CHECK ({predicate})"
        )


def downgrade():
    op.drop_table("sso_login_attempts")
