"""Wire shapes for the supplier-reliability board (WO-Q).

Every field name here was chosen against `test_wo87_r53_framing.CLAIM_WORDS`:
nothing on this surface may read as a demand, a debt or an amount owed, because
this analysis makes no such assertion (see `services/transport/reliability.py`).
The band values travel as the service's own constants, and `rule` travels
BESIDE `band` in the same object so a renderer cannot show a label without the
threshold that produced it.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class ThresholdsOut(BaseModel):
    overcharge_cases: int
    overcharge_eur_per_1000: Decimal
    fx_markup_bps: int
    ungoverned_share_pct: Decimal
    #: True when the org has typed no thresholds and these are the platform's
    #: documented defaults — rendered, because "our default" and "your choice"
    #: mean different things next to a band.
    is_default: bool


class ThresholdsIn(BaseModel):
    overcharge_cases: int = Field(ge=1, le=1000)
    overcharge_eur_per_1000: Decimal = Field(gt=0, le=Decimal("100000"))
    fx_markup_bps: int = Field(gt=0, le=10000)
    ungoverned_share_pct: Decimal = Field(gt=0, le=Decimal("100"))


class CriterionOut(BaseModel):
    key: str
    band: str
    #: The rule that produced `band`, in words, for rendering next to it.
    rule: str
    #: Counts and euros behind the band — criterion-specific by design; the
    #: screen renders what it is given rather than assuming a fixed set.
    figures: dict


class SupplierReliabilityOut(BaseModel):
    supplier: str
    overall: str
    active_months: int
    net_spend_eur: Decimal
    criteria: list[CriterionOut]


class ReliabilityOut(BaseModel):
    window_from: str
    window_to: str
    #: Rendered VERBATIM by every client. The service owns these words.
    framing: str
    thresholds: ThresholdsOut
    suppliers: list[SupplierReliabilityOut]
