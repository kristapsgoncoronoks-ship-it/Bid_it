"""Join planning and region OCR without rewriting either feature's history.

IMPACT: graph only; no schema, tenant policy, assignment or OCR evidence changes.
PREFLIGHT: both c3d5e7f90112 and d4e6f8a01223 must be present. Alembic applies
both branches before this marker. Quiesce API, capture, delivery and calendar
writers before upgrading either feature, including optional/external workers.
REFUSAL: unresolved predecessors fail graph resolution; no data normalization.
POSTCONDITION: one head includes both feature schemas and their original RLS.
ROLLBACK/REPAIR: downgrade this marker to its two parents only splits version
metadata. Retain additive feature schemas for application rollback. Earlier
downgrade guards still apply; calendar metadata removal requires disconnect
and worker shutdown first and does not delete remote provider events.
"""

revision = "e5f7091a2345"
down_revision = ("c3d5e7f90112", "d4e6f8a01223")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
