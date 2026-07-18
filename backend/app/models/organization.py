from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.vendor import Vendor


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant. Everything else hangs off exactly one organization."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Data-validation options — OFF by default, turned on by the user's choice.
    # AI = automated rule-based checks (LLM-pluggable); human = a review gate.
    ai_validation_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    human_validation_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    users: Mapped[list["User"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    vendors: Mapped[list["Vendor"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
