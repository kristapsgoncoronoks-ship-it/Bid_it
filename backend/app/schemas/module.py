from __future__ import annotations

from pydantic import BaseModel


class ModuleOut(BaseModel):
    key: str
    name: str
    description: str
    core: bool
    enabled: bool
    requires_issuer: bool
    ready: bool  # for add-ons with prerequisites (e.g. issuing needs a complete issuer)


class ModuleToggle(BaseModel):
    enabled: bool
