"""Join the preserved planning/OCR and payment migration histories.

IMPACT: graph only; no DDL, row rewrite, RLS, financial or provider changes.
PREFLIGHT: the preserved e5f7091a2345 and a9c3e7f1b5d0 histories must resolve.
Alembic applies each original feature migration before recording this marker;
quiesce application, capture, calendar and delivery writers for those upgrades.
REFUSAL: unresolved parents stop graph resolution; no normalization or repair.
POSTCONDITION: one head retains planning, region OCR and payment lookup schema.
ROLLBACK/REPAIR: explicit marker-only downgrade splits version metadata without
removing feature schema or evidence. Broad downgrade is not application rollback;
retain every earlier feature refusal guard and payment receipt APPROVE lookup
protection. Reapply the marker after resolving uncertainty. Calendar-provider
and external payment state is never inferred, reversed or deleted here.
"""

revision = "f0b2d4e6a8c1"
down_revision = ("e5f7091a2345", "a9c3e7f1b5d0")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
