from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SsoConnectionOut(BaseModel):
    """Admin view of the connection. The client secret is NEVER returned — only
    whether one is set."""
    model_config = ConfigDict(from_attributes=True)
    id: str
    slug: str
    protocol: str
    enabled: bool
    issuer: str | None = None
    client_id: str | None = None
    allowed_domain: str | None = None
    jit_enabled: bool = True
    default_role: str = "user"
    saml_metadata_url: str | None = None
    has_client_secret: bool = False
    scim_enabled: bool = False
    login_url: str | None = None
    scim_base_url: str | None = None


class ScimTokenOut(BaseModel):
    token: str   # shown ONCE


class SsoConnectionUpdate(BaseModel):
    slug: str | None = Field(default=None, min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    protocol: str | None = Field(default=None, pattern=r"^(oidc|saml)$")
    enabled: bool | None = None
    issuer: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    allowed_domain: str | None = None
    jit_enabled: bool | None = None
    default_role: str | None = Field(default=None, pattern=r"^(user|processor|admin)$")
    saml_metadata_url: str | None = None
