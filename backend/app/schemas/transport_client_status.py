"""Response schemas for the client claim-status portal (WO-93, G4.4/R39).

Field-for-field from `app/services/transport/client_status.py`'s dataclasses —
this module shapes the wire and computes nothing (engineering-rules §3). Every
amount is typed `Decimal`, so pydantic v2 serializes it as a JSON STRING and no
float ever appears in a money path (master-context §4.9); the service already
quantized each one through `app.core.money.q2`.

THE FIELD NAMES ARE THE DELIVERABLE
-------------------------------------
R39's acceptance line is an absence — *"a client-role session cannot see a status
code or a fee anywhere"* — and a wire shape is where that gets undone first. A
`status_code` field, or a `fee_eur` field, is read as such the moment a client
renders it, whatever the prose beside it says. So the names here carry no
internal code vocabulary, no fee vocabulary and no action verb, and
`tests/transport/test_wo93_client_surface.py` scans them (with a
seeded-violation self-test, so the scan cannot quietly stop working).

`label` and `description` are the SERVER's plain-language wording, carried on
the wire rather than held in the SPA — the same posture
`transport_excise.ExciseReportOut` takes with `eligibility`/`rate_caveat`. A
string the SPA owns is a string the SPA can re-word, and this surface's
vocabulary is the spec's, not ours.

`vat_eur` is `Decimal | None` on a claim row. `null` means "no single-currency
figure can be stated for this claim yet" — a draft whose lines span currencies
(§4.14) or a filed claim whose frozen column is NULL. It is never `0.00` as a
stand-in, because a client reading €0.00 learns something false.

`currency` is carried explicitly, always `"EUR"`. These are multi-country claims
and every figure is a `vat_eur` amount, so the response states its basis rather
than leaving a consumer to assume it (§4.14). No `vat_local` and no
`paid_amount` crosses this wire — see the service docstring for why reading
either would be a cross-currency total in disguise.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class ClientClaimOut(BaseModel):
    """One claim, as its owner sees it: which of their legal entities, which
    refunding country, which period, which plain-language stage, and how much
    reclaimable VAT. No id — there is nothing to open — and no internal code."""

    entity_name: str
    refund_country: str
    period: str
    stage: str
    vat_eur: Decimal | None = None


class ClientStageOut(BaseModel):
    """One of the six harvested stages (`BA_fleet_fuel.md` §3.D: `prep · ready ·
    filed · awaiting · refunded` + needs attention). All six are always present,
    including the empty ones — a stage that vanished at zero would make "you
    have nothing in preparation" and "the page forgot" identical."""

    stage: str
    label: str
    description: str
    claims: int
    vat_eur: Decimal


class ClientClaimStatusOut(BaseModel):
    year: int
    # Always "EUR" — the basis of every figure below, stated not assumed.
    currency: str
    total_claims: int
    stages: list[ClientStageOut]
    claims: list[ClientClaimOut]
    # Claims in an engine state outside the six-stage ladder. A count only: the
    # arithmetic stays honest (`Σ stages + not_shown == total`) while the reason
    # stays internal, which is the whole point of this surface.
    not_shown_claims: int
