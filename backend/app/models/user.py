from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.organization import Organization


class UserRole(str, enum.Enum):
    """The stored role column. These are ALWAYS scoped to the user's own company
    (tenant) — no company role grants any cross-company or system-wide privilege.
    Platform-operator access is a separate flag (`is_platform_admin`), never a
    company role.

    The original four values (low → high privilege):

    - user_free : read-only-tier permission role (maps to `authz.Role.READ_ONLY`)
    - user      : standard permission role (maps to `authz.Role.EMPLOYEE`)
    - admin     : business administration WITHIN the company (the admin panel)
    - owner     : the company's primary user — full administration of THEIR company
                  (user management, roles, settings). Not a system administrator.

    NOTE (WO-47): usage QUOTAS (monthly invoice/upload limits) are NOT a
    function of this role — they key off the org's subscription `plan`
    (`app.services.access`/`app.services.plans`), org-wide, shared by every
    member regardless of role. `user_free`/`user` are permission tiers only;
    the name `user_free` is a historical label, not a billing signal.

    Since A1.5, the column also accepts the remaining four business roles from
    the 8-role authorization matrix (`app.core.authz.Role`) directly by value —
    `finance_manager`/`accountant`/`approver`/`auditor` — so every business role
    is now a genuinely assignable stored value, not just a permission-matrix
    definition with no way to reach it. These four values are spelled to match
    `authz.Role`'s own string values exactly, so `authz.business_role()` resolves
    them with no mapping code (see its module docstring: "forward-compatible").
    `owner`/`admin`/`user`/`user_free` keep their legacy strings and continue to
    resolve through `authz._LEGACY_ROLE` onto OWNER/ADMINISTRATOR/EMPLOYEE/
    READ_ONLY, unchanged by this expansion.

    Stored as a portable VARCHAR (native_enum=False) so the role set can evolve
    without a Postgres ENUM migration — this expansion is exactly that: a pure
    Python-side widening, no DDL, no CHECK constraint (see `app/core/roles.py`
    and `docs/security/authorization-policy-matrix.md`).
    """

    user_free = "user_free"
    user = "user"
    admin = "admin"
    owner = "owner"
    finance_manager = "finance_manager"
    accountant = "accountant"
    approver = "approver"
    auditor = "auditor"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # The ACTIVE-ORG pointer (B1.5) — which of the user's organizations this
    # session currently acts in, repointed by POST /auth/switch-org. It is a
    # denormalized projection, NOT a membership assertion: the authoritative
    # org relationship is the `memberships` table, and every request verifies a
    # LIVE membership in this org (api/deps.get_current_identity) before the
    # pointer is honoured. Nothing security-relevant reads it as membership —
    # the users-table tenant guard/RLS scope by membership, and org-member
    # resolution (roster, SCIM, payees, approvers, DSAR) reads memberships.
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
        default=UserRole.user,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Whether the email address has been confirmed (Slice 3). New accounts start
    # False and receive a verification link; existing accounts were backfilled True.
    # Login only requires it when `require_email_verification` is enabled.
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Platform operator (cross-tenant admin). Off for all normal SaaS users.
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Designated expense approver — may approve/reject/reimburse expense reports.
    # The workspace owner (first-registered user) is one by default and appoints others.
    is_expense_approver: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Employee bank details for expense reimbursement payouts (SEPA credit
    # transfer). Self-service; only used when a reimbursement batch is exported.
    iban: Mapped[str | None] = mapped_column(String(34), nullable=True)
    bic: Mapped[str | None] = mapped_column(String(11), nullable=True)
    # Brute-force lockout: consecutive failed logins, reset on success. When the
    # count crosses the configured threshold the account is locked until
    # `locked_until` (a timestamp in the future).
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="users")
