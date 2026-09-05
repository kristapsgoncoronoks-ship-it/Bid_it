from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.roles import idp_assignable_role_values


def _not_assignable(value: str) -> str:
    # The refusal names the vocabulary. The regex this replaces refused with
    # a pattern string, which is how the settings screen came to offer a
    # value the API refused for a month without anyone learning why.
    return (
        f"{value!r} is not a role an identity provider may assign; "
        f"must be one of: {', '.join(idp_assignable_role_values())}"
    )


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
    # WO-AE: the roles an IdP may assign, FROM THE SERVER, so the settings
    # screen's selects cannot drift from the vocabulary the login path enforces
    # (the screen used to offer "processor", which is not a role at all).
    assignable_roles: list[str] = Field(default_factory=list)
    saml_metadata_url: str | None = None
    has_client_secret: bool = False
    scim_enabled: bool = False
    login_url: str | None = None
    scim_base_url: str | None = None


class ScimTokenOut(BaseModel):
    token: str  # shown ONCE


class SsoConnectionUpdate(BaseModel):
    slug: str | None = Field(
        default=None, min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$"
    )
    protocol: str | None = Field(default=None, pattern=r"^(oidc|saml)$")
    enabled: bool | None = None
    issuer: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    allowed_domain: str | None = None
    jit_enabled: bool | None = None
    # WO-AE: validated against the ONE role vocabulary (`roles.IDP_ASSIGNABLE_
    # ROLES`) by the validators below, not by a regex kept in step by hand —
    # the regex here used to admit three roles while the role module declared
    # eight.
    default_role: str | None = None
    groups_claim: str | None = Field(default=None, min_length=1, max_length=64)
    role_mappings: dict[str, str] | None = None  # {idp_group: role}
    role_sync: bool | None = None
    saml_metadata_url: str | None = None
    saml_sso_url: str | None = None
    saml_idp_entity_id: str | None = None
    saml_idp_cert: str | None = None

    @field_validator("default_role")
    @classmethod
    def _default_role_is_assignable(cls, value: str | None) -> str | None:
        if value is not None and value not in idp_assignable_role_values():
            raise ValueError(_not_assignable(value))
        return value

    @field_validator("role_mappings")
    @classmethod
    def _mapped_roles_are_assignable(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        # A mapping to a role the login path would drop is refused at SAVE time.
        # Before WO-AE it was accepted and then ignored at login — the worst of
        # the two failure modes, because the admin was told it worked.
        for group, role in (value or {}).items():
            if role not in idp_assignable_role_values():
                raise ValueError(f"group {group!r}: {_not_assignable(role)}")
        return value
