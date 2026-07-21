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
    groups_claim: str = "groups"
    role_mappings: dict[str, str] = Field(default_factory=dict)
    role_sync: bool = False
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
    default_role: str | None = Field(default=None, pattern=r"^(user_free|user|admin)$")
    groups_claim: str | None = Field(default=None, min_length=1, max_length=64)
    role_mappings: dict[str, str] | None = None   # {idp_group: role}
    role_sync: bool | None = None
    saml_metadata_url: str | None = None
    saml_sso_url: str | None = None
    saml_idp_entity_id: str | None = None
    saml_idp_cert: str | None = None
