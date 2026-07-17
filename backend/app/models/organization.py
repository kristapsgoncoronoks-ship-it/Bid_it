from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.vendor import Vendor


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant. Everything else hangs off exactly one organization."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)

    users: Mapped[list["User"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    vendors: Mapped[list["Vendor"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
