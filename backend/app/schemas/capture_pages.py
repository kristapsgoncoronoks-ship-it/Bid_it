from typing import Literal

from pydantic import BaseModel, Field


class CapturePage(BaseModel):
    number: int = Field(ge=1, le=6)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    width: int = Field(ge=1, le=960)
    height: int = Field(ge=1, le=960)
    size: int = Field(ge=1, le=524288)


class CapturePages(BaseModel):
    status: Literal["ready", "unsupported", "unavailable"]
    pages: list[CapturePage] = Field(default_factory=list, max_length=6)
    total_pages: int | None = None
    truncated: bool = False
    message: str | None = None
