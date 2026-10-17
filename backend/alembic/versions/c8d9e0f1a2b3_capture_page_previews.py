"""N4 bounded capture page preview metadata; existing runs stay NULL.

Downgrade removes metadata, not original source documents. Derived preview
objects remain registered until normal workspace storage retention/purge.
"""
import sqlalchemy as sa
from alembic import op

revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("extraction_runs", sa.Column("preview_json", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("extraction_runs") as batch:
        batch.drop_column("preview_json")
