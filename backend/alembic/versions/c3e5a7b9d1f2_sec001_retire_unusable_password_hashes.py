"""SEC-001 — retire the hashed "unusable" passwords of SSO/SCIM users.

WHY
-----
`oidc._match_or_provision` and `scim.create_user` stored
`hash_password("!sso-no-password")` / `hash_password("!scim-no-password")` as
the password of every user they provisioned. That is a real bcrypt hash of a
literal in the public source tree: the literal was a working password for
every IdP-provisioned account on `POST /auth/login`, bypassing the IdP and its
MFA. The code now stores `security.UNUSABLE_PASSWORD_HASH` ("!"), which is not
a hash and verifies against nothing. This migration finds every EXISTING row
that still carries one of the two old hashes and replaces it with the sentinel.

HOW A ROW IS RECOGNISED
-------------------------
bcrypt hashes are salted, so the rows cannot be found by equality. Each
candidate hash is CHECKED against the two retired literals — the same
operation the login route would have performed for the attacker. A user who
was provisioned by SSO and later set a real password through the reset flow
does not match and is left alone. Cost: two bcrypt verifications per user
row, once.

NOT REVERSIBLE, DELIBERATELY
------------------------------
The downgrade does nothing. Restoring the old hashes would restore the
backdoor, and the sentinel is a strict subset of the old behaviour for every
legitimate caller (both refuse every password).

Revision ID: c3e5a7b9d1f2
Revises: a9c1e3f5b7d2
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3e5a7b9d1f2"
down_revision: str | None = "a9c1e3f5b7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from app.core.security import UNUSABLE_PASSWORD_HASH, is_legacy_unusable_hash

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, hashed_password FROM users")).fetchall()
    retired = 0
    for user_id, hashed in rows:
        if is_legacy_unusable_hash(hashed):
            bind.execute(
                sa.text("UPDATE users SET hashed_password = :h WHERE id = :id"),
                {"h": UNUSABLE_PASSWORD_HASH, "id": user_id},
            )
            retired += 1
    print(  # noqa: T201 - the migration reports its own reconciliation, like WO-8
        f"[SEC-001] users scanned: {len(rows)}; legacy unusable hashes retired: {retired}"
    )


def downgrade() -> None:
    # Deliberately a no-op: see the module docstring.
    pass
