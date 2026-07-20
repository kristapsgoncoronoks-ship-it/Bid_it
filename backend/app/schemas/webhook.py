from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WebhookCreate(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    events: str = Field(default="*", max_length=500)   # "*" or comma-separated types
    description: str | None = Field(default=None, max_length=200)

    @field_validator("url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return v


class WebhookUpdate(BaseModel):
    url: str | None = Field(default=None, max_length=500)
    events: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=200)
    active: bool | None = None


class WebhookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    url: str
    events: str
    description: str | None
    active: bool
    created_at: datetime


class WebhookCreated(WebhookOut):
    # The signing secret is returned ONCE, at creation — store it now.
    secret: str


class WebhookDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    event_type: str
    status: str
    attempts: int
    response_code: int | None
    last_error: str | None
    created_at: datetime
    delivered_at: datetime | None
