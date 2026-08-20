"""Dynamic document templates — lifecycle phase 5's machinery (owner direction,
2026-08-16): the SERVER OWNER maintains master templates; each client adjusts
them for their needs and keeps as many saved versions as they like, choosing
per document which to use.

Two tables because the two scopes have opposite trust models:

- `platform_templates` — org-LESS master documents, written only by a platform
  operator, readable by every tenant. Ships with DEMO texts (marked as such —
  they are examples, not legal advice); the owner's lawyer's standardized
  texts replace the demo bodies when they land, through the same operator
  surface, changing nothing else.
- `org_templates` — a TENANT table (org_id + guard + RLS + probe): the
  client's own adjusted versions. `source_platform_id` records lineage but a
  platform edit NEVER reaches into a client's saved copy — an adjusted
  template is the client's document, frozen the moment they saved it.

Bodies are plain text with `{{placeholder}}` tokens (company.*, customer.*,
project.*, offer.*, plan.*, date). Unknown tokens render VISIBLY unreplaced:
a gap a person can see beats a silently wrong document.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

TEMPLATE_KINDS = ("contract", "acceptance", "offer", "other")
_KIND_CHECK = "kind IN ('contract', 'acceptance', 'offer', 'other')"


class PlatformTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A master document, maintained by the platform operator. Org-less like
    `ecb_rates`: global reference material, not tenant data."""

    __tablename__ = "platform_templates"
    __table_args__ = (UniqueConstraint("key", name="uq_platform_templates_key"),)

    key: Mapped[str] = mapped_column(String(60), nullable=False)  # stable slug
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class OrgTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One client's saved, adjusted version of a document template."""

    __tablename__ = "org_templates"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_org_templates_org_id"),
        Index("ix_org_templates_org_kind", "org_id", "kind"),
    )

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Lineage only — a platform edit never reaches into this saved copy.
    source_platform_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
