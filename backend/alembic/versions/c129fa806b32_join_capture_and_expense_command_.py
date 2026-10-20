"""Join capture and expense command histories without changing their schemas.

IMPACT: migration graph only; no tables, columns, constraints or rows change.
PREFLIGHT: Alembic applies both independent command-schema branches before this
marker. Existing capture and expense receipts retain their original migrations.
REFUSAL: missing predecessors fail Alembic graph resolution; no normalization.
POSTCONDITION: one head includes capture admission and expense replay evidence.
ROLLBACK/REPAIR: downgrading this marker only splits the graph; it does not drop
either schema. Each earlier branch retains its existing downgrade/refusal policy.

Revision ID: c129fa806b32
Revises: 238cd0be715f, b018e9f4c021
Create Date: 2026-10-20 04:57:37.340143
"""
revision: str = "c129fa806b32"
down_revision: tuple[str, str] = ("238cd0be715f", "b018e9f4c021")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
