"""Response schemas for the refund-estimate funnel (WO-AC, G4.8 / R43).

WHY EVERY FIGURE IS A STRING ON THE WIRE
------------------------------------------
Money and litres are typed `Decimal` (the WO-76 `transport_claim` convention) —
pydantic v2 serializes them as JSON strings, so no float ever crosses the wire
into or out of the `q2` quantization (master-context §4.9). This surface
recomputes nothing; every number is handed over exactly as
`estimate.EstimateResult` produced it.

WHY `below_minimum` IS OPTIONAL AND NOT DEFAULTED
----------------------------------------------------
`null` is a THIRD state, not a missing value: it means the Art. 17 comparison
could not be made in the country's own currency (Sweden and Denmark compare a
local-currency amount, and a country whose lines arrive in mixed currencies has
no single one). Defaulting it to `false` would tell a client the claim clears a
threshold nobody checked. The screen renders the three states differently for
the same reason.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class CountryEstimateOut(BaseModel):
    country: str
    lines: int
    litres: Decimal
    # Invoiced VAT in EUR — the recoverable figure under §2.3's own assumption.
    # EXCLUDES any line with no exchange rate; see `unconverted_lines`.
    vat_eur: Decimal
    # Present only when every line for this country shares one currency.
    vat_local: Decimal | None = None
    currency: str | None = None
    # true = below the threshold, false = clears it, null = not comparable.
    below_minimum: bool | None = None
    threshold: Decimal
    threshold_currency: str
    unconverted_lines: int


class EstimateOut(BaseModel):
    network: str
    period: str
    lines: int
    countries: list[CountryEstimateOut]
    recoverable_eur: Decimal
    unconverted_lines: int
    warnings: list[str]
    # R53's framing for this analysis, carried on every response so a client
    # cannot render the number without it.
    caveat: str
