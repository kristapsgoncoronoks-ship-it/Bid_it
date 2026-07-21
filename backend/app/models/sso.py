from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, UUIDPrimaryKeyMixin


class SsoConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant's single-sign-on connection to its own identity provider (ADR-0021).

    One per tenant for now, keyed by a public `slug` used in the login URL
    (`/auth/sso/<slug>/authorize`). `protocol` selects OIDC (implemented) or SAML
    (scaffolded — see ADR-0021). JIT provisioning creates a user in THIS org on
    first successful login, with `default_role`.

    Security note: `client_secret` is stored here for the multi-tenant config.
    Before GA it MUST move to the envelope-encrypted secret store (ADR-0016);
    tracked as a finish-line item in ADR-0021.
    """

    __tablename__ = "sso_connections"

    org_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    protocol: Mapped[str] = mapped_column(String(10), default="oidc", nullable=False)  # oidc | saml
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # OIDC.
    issuer: Mapped[str | None] = mapped_column(String(400), nullable=True)      # discovery base URL
    client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_secret: Mapped[str | None] = mapped_column(String(512), nullable=True)  # TODO: secret store (ADR-0016)

    # Provisioning policy.
    allowed_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)  # restrict emails to this domain
    jit_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)

    # SCIM 2.0 provisioning (ADR-0021): the tenant's IdP authenticates with a
    # bearer token (only its sha256 is stored; the plaintext is shown once).
    scim_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    scim_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # SAML (SP request side + config; assertion CONSUMPTION is the finish item —
    # needs a vetted XML-DSig library + real IdP metadata, see ADR-0021).
    saml_metadata_url: Mapped[str | None] = mapped_column(String(400), nullable=True)
    saml_sso_url: Mapped[str | None] = mapped_column(String(400), nullable=True)       # IdP SSO endpoint
    saml_idp_entity_id: Mapped[str | None] = mapped_column(String(400), nullable=True)
    saml_idp_cert: Mapped[str | None] = mapped_column(String(4000), nullable=True)     # IdP signing cert (PEM/base64)
