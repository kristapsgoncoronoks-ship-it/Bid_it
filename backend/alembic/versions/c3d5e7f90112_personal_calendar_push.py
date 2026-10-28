"""Add personal Google/Outlook export intent and browser-bound OAuth state.

IMPACT: three new empty tenant tables; no historical assignment is exported.
PREFLIGHT: apply assignment delivery profiles b2c4d6e8f010 first. Credentials
remain operator configuration; migration makes no provider calls.
REFUSAL: normal FK/uniqueness checks; no credential migration or plaintext data.
POSTCONDITION: sealed credentials, durable mapping identity and expiring one-use
OAuth evidence can be committed before external I/O. PostgreSQL RLS is enabled.
ROLLBACK/REPAIR: downgrade removes integration metadata only; disconnect first.
External provider calendars are not deleted by schema rollback.

Revision ID: c3d5e7f90112
Revises: b2c4d6e8f010
"""

import sqlalchemy as sa

from alembic import op
from app.models.base import GUID

revision = "c3d5e7f90112"
down_revision = "b2c4d6e8f010"
branch_labels = None
depends_on = None
TENANT_TABLES = ("calendar_connections", "calendar_oauth_attempts", "calendar_event_mappings")


def _base():
    return [
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "org_id", GUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade():
    op.create_table(
        "calendar_connections",
        *_base(),
        sa.Column("user_id", GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(12), nullable=False),
        sa.Column("account_hash", sa.String(64), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("sealed_tokens", sa.Text(), nullable=True),
        sa.Column("calendar_id", sa.String(1024), nullable=True),
        sa.Column("calendar_name", sa.String(120), nullable=False),
        sa.Column("calendar_marker", sa.String(100), nullable=False),
        sa.Column("setup_started", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(120), nullable=True),
        sa.UniqueConstraint("org_id", "user_id", "provider", name="uq_calendar_connection_owner"),
        sa.UniqueConstraint("org_id", "id", name="uq_calendar_connections_org_id_id"),
    )
    op.create_index(
        "ix_calendar_connections_org_user", "calendar_connections", ["org_id", "user_id"]
    )
    op.create_table(
        "calendar_oauth_attempts",
        *_base(),
        sa.Column("user_id", GUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "session_id", GUID(), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.String(12), nullable=False),
        sa.Column("connection_id", GUID(), nullable=False),
        sa.Column("connection_generation", sa.Integer(), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("browser_hash", sa.String(64), nullable=False),
        sa.Column("sealed_verifier", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id", "connection_id"],
            ["calendar_connections.org_id", "calendar_connections.id"],
            ondelete="CASCADE",
            name="fk_calendar_oauth_connection",
        ),
    )
    op.create_index(
        "ix_calendar_oauth_attempts_org_expiry", "calendar_oauth_attempts", ["org_id", "expires_at"]
    )
    op.create_table(
        "calendar_event_mappings",
        *_base(),
        sa.Column("connection_id", GUID(), nullable=False),
        sa.Column("assignment_id", GUID(), nullable=False),
        sa.Column("stable_id", sa.String(36), nullable=False),
        sa.Column("event_id", sa.String(1024), nullable=True),
        sa.Column("desired_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("synced_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error", sa.String(120), nullable=True),
        sa.Column("create_started", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lease_owner", sa.String(36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "org_id", "connection_id", "assignment_id", name="uq_calendar_event_source"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "connection_id"],
            ["calendar_connections.org_id", "calendar_connections.id"],
            ondelete="CASCADE",
            name="fk_calendar_events_connection",
        ),
    )
    op.create_index(
        "ix_calendar_events_org_connection", "calendar_event_mappings", ["org_id", "connection_id"]
    )
    if op.get_bind().dialect.name == "postgresql":
        for table in TENANT_TABLES:
            op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
            op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
            predicate = "(current_setting('app.current_org', true) IS NULL OR current_setting('app.current_org', true) = '' OR org_id::text = current_setting('app.current_org', true))"
            op.execute(
                sa.text(
                    f'CREATE POLICY tenant_isolation ON "{table}" USING ({predicate}) WITH CHECK ({predicate})'
                )
            )


def downgrade():
    op.drop_table("calendar_event_mappings")
    op.drop_table("calendar_oauth_attempts")
    op.drop_table("calendar_connections")
