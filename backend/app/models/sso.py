from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class SsoConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant's single-sign-on connection to its own identity provider (ADR-0021).

    One per tenant for now, keyed by a public `slug` used in the login URL
    (`/auth/sso/<slug>/authorize`). `protocol` selects OIDC (implemented) or SAML
    (scaffolded — see ADR-0021). JIT provisioning creates a user in THIS org on
    first successful login, with `default_role`.

    Security note: `client_secret` is **sealed at rest** (AES-256-GCM via
    `keyvault.py`, ADR-0016) — written encrypted by `sso_config`, read back with
    `keyvault.read_secret`. The production KEK provider (env/BYOK vs cloud KMS) is
    a deployment decision (see docs/DECISIONS-NEEDED.md §5).
    """

    __tablename__ = "sso_connections"

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    protocol: Mapped[str] = mapped_column(String(10), default="oidc", nullable=False)  # oidc | saml
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # OIDC.
    issuer: Mapped[str | None] = mapped_column(String(400), nullable=True)  # discovery base URL
    client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_secret: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )  # TODO: secret store (ADR-0016)

    # Provisioning policy.
    allowed_domain: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )  # restrict emails to this domain
    jit_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)

    # IdP group → role mapping (ADR-0021): JSON {"<idp_group>": "<role>"} read from
    # the ID token's `groups_claim`. When `role_sync` is on, an existing user's
    # role is re-synced from their groups on each login (IdP authoritative); owner
    # is never granted or demoted via SSO.
    groups_claim: Mapped[str] = mapped_column(String(64), default="groups", nullable=False)
    role_mappings: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON object
    role_sync: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # SCIM 2.0 provisioning (ADR-0021): the tenant's IdP authenticates with a
    # bearer token (only its sha256 is stored; the plaintext is shown once).
    scim_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    scim_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # SAML (SP request side + config; assertion CONSUMPTION is the finish item —
    # needs a vetted XML-DSig library + real IdP metadata, see ADR-0021).
    saml_metadata_url: Mapped[str | None] = mapped_column(String(400), nullable=True)
    saml_sso_url: Mapped[str | None] = mapped_column(String(400), nullable=True)  # IdP SSO endpoint
    saml_idp_entity_id: Mapped[str | None] = mapped_column(String(400), nullable=True)
    saml_idp_cert: Mapped[str | None] = mapped_column(
        String(4000), nullable=True
    )  # IdP signing cert (PEM/base64)
