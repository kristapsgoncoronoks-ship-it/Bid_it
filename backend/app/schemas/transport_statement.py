"""Response schemas for the transport statement-intake route (WO-S).

WHAT THIS SURFACE DELIBERATELY DOES NOT RETURN
------------------------------------------------
`statement_ingest.ingest_statement` returns every `FuelTransaction` it wrote.
This schema returns their COUNT and a bounded sample instead, for a reason that
is about honesty rather than payload size: a statement is a monthly file that
routinely carries thousands of lines, and a response that dumps all of them
invites a client to treat the upload reply as the fuel-transaction list. It is
not one — `GET /transport/fuel-transactions` is, it is paginated, it is
filterable, and it stays true after a second upload. The sample here exists so
an operator can see that the right file landed, not so a client can page it.

WHY THE WARNINGS ARE THE POINT
--------------------------------
Everything advisory in the ingest pipeline arrives as a warning string: the
parser's own notes, the capture review's WARN findings (R25 regime 1), the
post-capture deterministic checks (WO-71/G3.4, advisory per §4.19 even at
`error` severity), and the anti-drift comparison (WO-70). A statement that
registered successfully but produced fourteen warnings is the interesting case
this whole slice exists to make visible — until now those strings were returned
to a Python caller that no route ever invoked, which is to say: to nobody.

The REFUSALS travel as the service's own `{"detail", "code"}` — `invalid_period`,
`unrecognized_fuel_card_statement`, `capture_review_blocked`,
`module_not_enabled`, `entity_not_found`. This module maps none of them, so the
wire vocabulary cannot drift from the service layer (master-context §4.20).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel

#: How many parsed lines the upload reply echoes back. Enough to recognise the
#: file, far too few to mistake for the fuel-transaction listing.
SAMPLE_LINES = 5


class StatementLineSample(BaseModel):
    """One registered line, trimmed to what identifies it on a screen."""

    line_seq: int
    txn_date: date
    country: str
    station: str
    product: str
    qty: Decimal
    currency: str
    net_local: Decimal
    vat_local: Decimal
    net_eur: Decimal
    vat_eur: Decimal
    # §4.15: a converted amount is not auditable without its provenance, so the
    # sample carries the source even though it drops most other columns.
    fx_source: str | None


class StatementEntityOut(BaseModel):
    """A per-country seller entity the statement TAUGHT this workspace (G3.1 /
    WO-61). Learned, never typed — which is why the upload reply names them: an
    operator should see what a file added to the registry."""

    country: str
    vat_number: str
    entity_name: str | None


class StatementIngestOut(BaseModel):
    #: Detected by the parser registry from the file's own marker line, NEVER
    #: asserted by the uploader — see the route docstring.
    network: str
    period: str
    filename: str
    #: SHA-256 of the uploaded bytes. The same digest `extraction_baseline`
    #: keys on, surfaced so an operator can tell "I uploaded this exact file
    #: before" apart from "I uploaded this month again".
    statement_sha256: str
    lines_registered: int
    entities_learned: list[StatementEntityOut]
    warnings: list[str]
    sample: list[StatementLineSample]


class FuelCardNetworkOut(BaseModel):
    network: str


class FuelCardNetworkListOut(BaseModel):
    """What the intake screen can name as supported — read from the live parser
    registry, so a network cannot appear here without a parser that actually
    handles it, and a newly registered parser needs no second edit to show up."""

    networks: list[FuelCardNetworkOut]
