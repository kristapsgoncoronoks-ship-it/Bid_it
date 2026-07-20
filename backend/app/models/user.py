from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.organization import Organization


class UserRole(str, enum.Enum):
    """Four platform user groups, low → high privilege.

    - user_free : non-paying user; limited access, usage limits from the matrix
    - user      : paying user; usage limits from the matrix
    - admin     : access to the admin panel (business administration)
    - sysadmin  : all privileges, including user-rights management + the matrix
    """
    user_free = "user_free"
    user = "user"
    admin = "admin"
    sysadmin = "sysadmin"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    # Stored as a portable VARCHAR (native_enum=False) so the role set can evolve
    # without a Postgres ENUM migration.
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role", native_enum=False, length=20),
        default=UserRole.user, nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Platform operator (cross-tenant admin). Off for all normal SaaS users.
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Designated expense approver — may approve/reject/reimburse expense reports.
    # The workspace owner (first-registered user) is one by default and appoints others.
    is_expense_approver: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    organization: Mapped["Organization"] = relationship(back_populates="users")
