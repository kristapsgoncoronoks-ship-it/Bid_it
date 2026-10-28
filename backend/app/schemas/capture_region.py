"""The bounded public selected-region recognition and explicit-application wire."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.invoice import InvoiceCreate

HEADER_FIELDS = frozenset(
    (
        "vendor_name",
        "invoice_number",
        "issue_date",
        "due_date",
        "currency",
        "po_reference",
        "supplier_registration_number",
        "supplier_vat_id",
        "supplier_iban",
    )
)
LINE_FIELDS = frozenset(
    (
        "description",
        "category",
        "quantity",
        "unit_price",
        "amount",
        "tax_rate",
        "captured_tax_amount",
        "captured_gross_amount",
    )
)


class RegionReadIn(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    command_key: UUID
    expected_attempt_seq: int = Field(ge=0)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page: int = Field(ge=1, le=6)
    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    rotation: Literal[0, 90, 180, 270] = 0
    language: Literal["eng"] = "eng"
    target: Literal["field", "table"]
    field: str | None = Field(default=None, max_length=40)
    line_index: int | None = Field(default=None, ge=0, le=199)

    @model_validator(mode="after")
    def bounded_target(self):
        if self.x + self.width > 1.00000001 or self.y + self.height > 1.00000001:
            raise ValueError("Selection must remain inside the page")
        if self.target == "table":
            if self.field is not None or self.line_index is not None:
                raise ValueError("A table selection does not target an existing field")
        elif self.field in HEADER_FIELDS:
            if self.line_index is not None:
                raise ValueError("Header fields have no line index")
        elif self.field in LINE_FIELDS:
            if self.line_index is None:
                raise ValueError("Choose the destination line")
        else:
            raise ValueError("Choose a supported field")
        return self


class RegionValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(max_length=40)
    line_index: int | None = Field(default=None, ge=0, le=199)
    value: str = Field(max_length=500)


class RegionRowMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_index: int = Field(ge=0, le=99)
    target_line_index: int | None = Field(default=None, ge=0, le=199)
    fields: list[str] = Field(min_length=1, max_length=8)


class RegionApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_key: UUID
    expected_review_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    fields: list[RegionValue] = Field(default_factory=list, max_length=80)
    rows: list[RegionRowMapping] = Field(default_factory=list, max_length=100)
    overwrite_reviewed: bool = False

    @model_validator(mode="after")
    def selected(self):
        if not self.fields and not self.rows:
            raise ValueError("Choose at least one change")
        return self


class RegionResult(BaseModel):
    text: str
    engine: str
    engine_version: str | None = None
    segmentation_mode: int | None = None
    language: str
    installed_languages: list[str] = Field(default_factory=list)
    page: int
    rotation: int
    source_width: float
    source_height: float
    source_unit: str | None = None
    rotated_width: float
    rotated_height: float
    crop_width: int
    crop_height: int
    render_dpi: int | None = None
    fields: list[RegionValue] = Field(default_factory=list)
    rows: list[dict[str, str | None]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReviewedRegionField(BaseModel):
    field: str
    line_index: int | None = None


class RegionAppliedResult(BaseModel):
    draft: InvoiceCreate
    review_fingerprint: str
    changed_fields: int
    appended_rows: int


class RegionReadOut(BaseModel):
    id: str
    command_key: str
    extraction_run_id: str
    status: str
    request: RegionReadIn
    result: RegionResult | None = None
    error_code: str | None = None
    error_detail: str | None = None
    review_fingerprint: str
    current_draft: dict | None = None
    reviewed_fields: list[ReviewedRegionField] = Field(default_factory=list)
    applied: bool = False
    apply_command_key: str | None = None
    applied_result: RegionAppliedResult | None = None


class RegionReadList(BaseModel):
    items: list[RegionReadOut]
    source_sha256: str | None
    attempt_seq: int
    review_fingerprint: str
    current_draft: dict | None = None
    reviewed_fields: list[ReviewedRegionField]
    eligible: bool
    reason: str | None = None
