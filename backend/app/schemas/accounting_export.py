from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.invoice import InvoiceStatus


class AccountingScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: UUID
    date_from: date
    date_to: date
    currency: str = Field(min_length=3, max_length=3, pattern="^[A-Z]{3}$")
    status: InvoiceStatus | None = None
    q: str | None = Field(default=None, max_length=120)
    workflow_state: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def valid_period(self):
        if self.date_to < self.date_from:
            raise ValueError("The end date must be on or after the start date.")
        return self


class AccountingEntity(BaseModel):
    id: str
    name: str | None
    legal_name: str | None


class AccountingPreviewRow(BaseModel):
    invoice_id: str
    number: str
    supplier: str
    currency: str
    net: str
    tax: str
    gross: str


class AccountingPreview(BaseModel):
    snapshot_id: str
    contract: str
    scope: AccountingScope
    entity_name: str
    included: int
    excluded_unapproved: int
    excluded_filters: int
    excluded_other_currencies: int
    unassigned_in_period: int
    missing_fx: int
    net: str
    tax: str
    gross: str
    gross_eur: str | None
    sha256: str | None
    expires_at: int
    token: str | None
    rows: list[AccountingPreviewRow]


class AccountingDownload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=3_000_000)


class AccountingFormat(BaseModel):
    key: str
    action_id: str
    label: str
    enabled: bool
    contract: str | None
    reason: str | None
    setup_steps: list[str]
