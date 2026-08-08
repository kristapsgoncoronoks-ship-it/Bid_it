"""WO-93 — the client claim-status portal, the SERVICE half (G4.4, R39).

Every stage is proven with a claim GENUINELY CONSTRUCTED in that state through
the real services — `claim_lines.build_claim_lines`, `extraction.link_to_invoice`,
`lock.submit_claim`, `status.set_status_code` — the `test_g2_7_status.py` /
`test_wo81_recovery.py` seeding pattern, reused unchanged. A stage must never
pass because the fixture wrote the code the mapper reads.

The mapping itself is asserted over `status.AUTO_CODES + status.MANUAL_CODES`
IMPORTED from `status.py`, so a code added there fails here rather than falling
through this surface silently.

Every euro asserted in this file is hand-computed in `Decimal`. There is no
float in it, and none in the module it proves.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import AppError
from app.models.invoice import Invoice
from app.models.transport.vat_claim import VatRefundClaim
from app.models.vendor import Vendor
from app.services import extraction
from app.services.transport import claim as claim_svc
from app.services.transport import (
    claim_lines,
    client_status,
    fuel_ingest,
    lock,
    status,
)
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import activate_entity, enable_transport, make_entity, make_org

# 2026-Q2 ends 30 Jun 2026.
PERIOD_ENDED = date(2026, 7, 1)
PERIOD_NOT_ENDED = date(2026, 6, 15)


# --------------------------------------------------------------------------- #
# Domain seeding (the test_wo81_recovery.py shapes)
# --------------------------------------------------------------------------- #


async def _make_claim(db_session, org_id: str, entity_id: str, **overrides) -> VatRefundClaim:
    kwargs = dict(refund_country="LV", ref_period="2026-Q2")
    kwargs.update(overrides)
    return await claim_svc.get_or_create_claim(db_session, org_id, entity_id=entity_id, **kwargs)


async def _make_vendor(db_session, org_id: str, *, name: str = "Q8") -> Vendor:
    vendor = Vendor(org_id=org_id, name=name, country="LV")
    db_session.add(vendor)
    await db_session.flush()
    return vendor


async def _make_invoice(db_session, org_id: str, vendor, *, number: str) -> Invoice:
    inv = Invoice(
        org_id=org_id,
        vendor_id=vendor.id,
        invoice_number=number,
        issue_date=date(2026, 5, 1),
        currency="EUR",
        subtotal=Decimal("2000.00"),
        tax_amount=Decimal("420.00"),
        total=Decimal("2420.00"),
    )
    db_session.add(inv)
    await db_session.flush()
    return inv


async def _make_txn(
    db_session,
    org_id: str,
    entity_id: str,
    *,
    supplier: str = "Q8",
    invoice_ref: str | None = "INV-0001",
    net_eur: Decimal = Decimal("2000.00"),
    vat_eur: Decimal = Decimal("420.00"),
    currency: str = "EUR",
    line_seq: int = 1,
    month: str = "2026-05",
    txn_date: date = date(2026, 5, 15),
):
    return await fuel_ingest.ingest_transaction(
        db_session,
        org_id,
        entity_id=entity_id,
        supplier=supplier,
        period=month,
        line_seq=line_seq,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=txn_date,
        station="Demo Station Riga",
        product="DIESEL",
        qty=Decimal("100.000"),
        currency=currency,
        net_local=net_eur,
        vat_local=vat_eur,
        gross_local=net_eur + vat_eur,
        net_eur=net_eur,
        vat_eur=vat_eur,
        invoice_ref=invoice_ref,
        fx_source=None if str(currency).upper() == "EUR" else "ecb",
    )


async def _attach_document(db_session, org_id: str, invoice, *, sha: str = "a" * 64) -> None:
    run = await extraction.record(
        db_session,
        org_id,
        filename="q8-inv-0001.pdf",
        sha256=sha,
        method="pdf-text",
        status="parsed",
    )
    await extraction.link_to_invoice(db_session, org_id, run.id, invoice.id)
    await db_session.commit()


async def _clean_claim(
    db_session,
    org_id: str,
    entity_id: str,
    *,
    vat_eur: Decimal = Decimal("420.00"),
    supplier: str = "Q8",
    number: str = "INV-0001",
    sha: str = "a" * 64,
    month: str = "2026-05",
    txn_date: date = date(2026, 5, 15),
    **claim_kwargs,
):
    """A draft claim with ONE documented, resolved line. `derive_stage` reads
    `1E` for it once the period has ended, so it reads client stage `ready`.
    Returns `(claim, transaction)` — the transaction id is what
    `lock.submit_claim` keys its locks on."""
    vendor = await _make_vendor(db_session, org_id, name=supplier)
    inv = await _make_invoice(db_session, org_id, vendor, number=number)
    claim = await _make_claim(db_session, org_id, entity_id, **claim_kwargs)
    await db_session.commit()
    txn = await _make_txn(
        db_session,
        org_id,
        entity_id,
        supplier=supplier,
        invoice_ref=number,
        vat_eur=vat_eur,
        month=month,
        txn_date=txn_date,
    )
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org_id, claim.id)
    await db_session.commit()
    await _attach_document(db_session, org_id, inv, sha=sha)
    return claim, txn


async def _org_with_entity(db_session, *, activate: bool = False):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    if activate:
        await activate_entity(db_session, org.id, entity.id, "LV")
    await db_session.commit()
    return org.id, entity.id


def _stage_of(view: client_status.ClientClaimStatus) -> str:
    """The single populated stage of a one-claim portfolio."""
    filled = [s.stage for s in view.stages if s.claims]
    assert len(filled) == 1, f"expected exactly one populated stage, got {filled}"
    return filled[0]


def _stage(view: client_status.ClientClaimStatus, name: str) -> client_status.ClientStage:
    found = [s for s in view.stages if s.stage == name]
    assert len(found) == 1, f"{name} is not a stage: {[s.stage for s in view.stages]}"
    return found[0]


async def _submitted_claim(db_session, org_id: str, entity_id: str, **kw) -> VatRefundClaim:
    """A claim submitted through the REAL gate — `lock.submit_claim` freezes the
    lines, takes the locks and stamps `status_code = "2"`. Nothing here writes
    a status by hand."""
    claim, txn = await _clean_claim(db_session, org_id, entity_id, **kw)
    supplier = kw.get("supplier", "Q8")
    number = kw.get("number", "INV-0001")
    result = await lock.submit_claim(
        db_session,
        org_id,
        claim_id=claim.id,
        invoices=[(supplier, number, txn.id)],
        today=PERIOD_ENDED,
    )
    await db_session.commit()
    return result


# --------------------------------------------------------------------------- #
# The stage vocabulary and the code mapping
# --------------------------------------------------------------------------- #


def test_wo93_the_six_client_stages_are_the_spec_vocabulary_in_order():
    """`BA_fleet_fuel.md` §3.D / §4.5 / R39: *"prep -> ready -> filed ->
    awaiting -> refunded (plus 'needs attention')"*. Six values, in that order,
    with no seventh and no friendlier synonym (§9/§10)."""
    assert client_status.CLIENT_STAGES == (
        "prep",
        "ready",
        "filed",
        "awaiting",
        "refunded",
        "needs_attention",
    )


def test_wo93_every_internal_code_maps_to_exactly_one_client_stage():
    """The mapping is TOTAL over the vocabulary `status.py` actually defines —
    the tuples are imported, so a code added there fails HERE rather than
    silently falling through to the engine fallback on a client's screen.

    `status.MANUAL_CODES` deliberately EXCLUDES `"2"` (Submit): that transition
    only ever happens through `lock.submit_claim`'s gated procedure, which
    stamps `status_code = "2"` itself, and `set_status_code` refuses to accept
    it at all. So the code a claim can actually CARRY is the union of the two
    tuples plus `"2"` — fourteen settable codes and the one the submit gate
    writes, fifteen in total, which is exactly §3.D's table."""
    every_code = status.AUTO_CODES + status.MANUAL_CODES
    assert "2" not in every_code, "MANUAL_CODES excludes Submit — see lock.submit_claim"
    carryable = (*every_code, "2")
    assert len(carryable) == 15, carryable
    for code in carryable:
        stage = client_status.stage_for_code(code)
        assert stage in client_status.CLIENT_STAGES, f"{code} -> {stage!r}"

    # And the map carries no entry for a code the lifecycle does not have.
    assert set(client_status.STAGE_BY_CODE) == set(carryable)


def test_wo93_the_recorded_interpretation_is_what_the_map_says():
    """The module docstring's table, asserted rather than described. A future
    edit that quietly re-read "rejection" as `awaiting`, or folded 1A into
    `prep`, has to change this list deliberately."""
    assert client_status.STAGE_BY_CODE == {
        "1A": "needs_attention",
        "1B": "prep",
        "1C": "prep",
        "1E": "ready",
        "2": "filed",
        "2A": "filed",
        "2B": "needs_attention",
        "3": "awaiting",
        "3A": "refunded",
        "3B": "needs_attention",
        "3C": "needs_attention",
        "3D": "needs_attention",
        "4": "refunded",
        "4A": "refunded",
        "5": "refunded",
    }


def test_wo93_an_absent_or_unknown_code_maps_to_nothing_rather_than_guessing():
    assert client_status.stage_for_code(None) is None
    assert client_status.stage_for_code("9Z") is None


def test_wo93_every_stage_has_a_server_owned_label_and_sentence():
    """The client reads the LABEL, not the slug, and the label is the server's
    so the SPA cannot re-word it (see the module docstring)."""
    for stage in client_status.CLIENT_STAGES:
        assert client_status.STAGE_LABELS[stage].strip(), stage
        assert client_status.STAGE_DESCRIPTIONS[stage].strip(), stage
    assert set(client_status.STAGE_LABELS) == set(client_status.CLIENT_STAGES)
    assert set(client_status.STAGE_DESCRIPTIONS) == set(client_status.CLIENT_STAGES)


# --------------------------------------------------------------------------- #
# The AUTO stages — a draft claim genuinely constructed in each state
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo93_a_draft_missing_documents_reads_needs_attention(db_session):
    """Stage 1A. No vendor is registered at all, so the line resolves UNMATCHED
    and the checklist fails — the claim is genuinely blocked, not marked so."""
    org_id, entity_id = await _org_with_entity(db_session)
    claim = await _make_claim(db_session, org_id, entity_id)
    await db_session.commit()
    await _make_txn(db_session, org_id, entity_id, invoice_ref="INV-9999")
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org_id, claim.id)
    await db_session.commit()

    assert await status.derive_stage(db_session, org_id, claim.id, today=PERIOD_ENDED) == "1A"
    view = await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert _stage_of(view) == "needs_attention"
    assert _stage(view, "needs_attention").vat_eur == Decimal("420.00")


@pytest.mark.asyncio
async def test_wo93_a_draft_whose_period_has_not_ended_reads_prep(db_session):
    """Stage 1B — nothing to fix; it becomes fileable when its own period
    closes. "In preparation" is the true statement about it."""
    org_id, entity_id = await _org_with_entity(db_session)
    claim, _txn = await _clean_claim(db_session, org_id, entity_id)

    assert await status.derive_stage(db_session, org_id, claim.id, today=PERIOD_NOT_ENDED) == "1B"
    view = await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_NOT_ENDED)
    assert _stage_of(view) == "prep"


@pytest.mark.asyncio
async def test_wo93_a_below_minimum_draft_reads_prep(db_session):
    """Stage 1C (Art. 17: EUR 400 quarterly; EUR 2.10 of VAT is under it). The
    client is deliberately not told WHICH caveat — that is operator
    information, and `/recovery-dashboard` is where an operator sees it."""
    org_id, entity_id = await _org_with_entity(db_session)
    claim, _txn = await _clean_claim(db_session, org_id, entity_id, vat_eur=Decimal("2.10"))

    assert await status.derive_stage(db_session, org_id, claim.id, today=PERIOD_ENDED) == "1C"
    view = await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert _stage_of(view) == "prep"
    assert _stage(view, "prep").vat_eur == Decimal("2.10")


@pytest.mark.asyncio
async def test_wo93_a_clean_fileable_draft_reads_ready(db_session):
    """Stage 1E — the one AUTO code the spec's own ladder has a name for."""
    org_id, entity_id = await _org_with_entity(db_session)
    claim, _txn = await _clean_claim(db_session, org_id, entity_id)

    assert await status.derive_stage(db_session, org_id, claim.id, today=PERIOD_ENDED) == "1E"
    view = await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert _stage_of(view) == "ready"
    assert _stage(view, "ready").vat_eur == Decimal("420.00")


# --------------------------------------------------------------------------- #
# The manual codes — a claim submitted through the real gate, then moved
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo93_a_submitted_claim_reads_filed(db_session):
    """Constructed through `lock.submit_claim`, which is what actually stamps
    `status_code = "2"` — never by writing the code by hand."""
    org_id, entity_id = await _org_with_entity(db_session, activate=True)
    claim = await _submitted_claim(db_session, org_id, entity_id)
    assert claim.status == "submitted" and claim.status_code == "2"

    view = await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert _stage_of(view) == "filed"
    assert view.claims[0].vat_eur == Decimal("420.00"), "the FROZEN figure (R13/C10)"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("2A", "filed"),
        ("2B", "needs_attention"),
        ("3", "awaiting"),
        ("3A", "refunded"),
        ("3B", "needs_attention"),
        ("3C", "needs_attention"),
        ("3D", "needs_attention"),
        ("4", "refunded"),
        ("4A", "refunded"),
        ("5", "refunded"),
    ],
)
async def test_wo93_each_manual_code_reads_its_stage(db_session, code, expected):
    """Every manual code of §3.D's table, stamped by the REAL writer
    (`status.set_status_code`) on a claim that reached a locking state through
    the real gate."""
    org_id, entity_id = await _org_with_entity(db_session, activate=True)
    claim = await _submitted_claim(db_session, org_id, entity_id)
    await status.set_status_code(db_session, org_id, claim.id, code)
    await db_session.commit()

    view = await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert _stage_of(view) == expected


@pytest.mark.asyncio
async def test_wo93_a_filed_claim_with_no_status_code_falls_back_to_the_engine_state(db_session):
    """`lock.submit_claim` stamps a code, but a claim moved by any other path
    may carry none. The coarse engine state is always true even then, so the
    portal reports something honest rather than raising or hiding the claim."""
    org_id, entity_id = await _org_with_entity(db_session)
    for period, engine in (
        ("2026-Q1", "submitted"),
        ("2026-Q2", "approved"),
        ("2026-Q3", "paid"),
    ):
        c = await _make_claim(db_session, org_id, entity_id, ref_period=period)
        c.status = engine
        c.status_code = None
        c.vat_eur = Decimal("100.00")
    await db_session.commit()

    view = await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert _stage(view, "filed").claims == 1
    assert _stage(view, "awaiting").claims == 1
    assert _stage(view, "refunded").claims == 1
    assert view.not_shown_claims == 0


@pytest.mark.asyncio
async def test_wo93_a_filed_claim_with_an_unrecognised_code_falls_back_not_forward(db_session):
    """A code from a future vocabulary must not reach the client and must not
    make the claim vanish: the engine state answers instead."""
    org_id, entity_id = await _org_with_entity(db_session)
    c = await _make_claim(db_session, org_id, entity_id)
    c.status = "paid"
    c.status_code = "7X"
    c.vat_eur = Decimal("55.00")
    await db_session.commit()

    view = await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert _stage_of(view) == "refunded"
    assert view.claims[0].stage == "refunded"


@pytest.mark.asyncio
async def test_wo93_a_withdrawn_claim_is_not_shown_and_its_stale_code_is_never_read(db_session):
    """`lock.withdraw_claim` sets `status = "withdrawn"` but LEAVES
    `status_code` populated, while §3.D D7 says withdrawal also NULLs it — a
    G2.7 gap this order does not fix (§4.20 additive). Dispatching on the
    engine status FIRST makes this surface immune to it: the withdrawn claim
    is not shown, and its stale `"2"` never reaches the code map. This test
    pins the immunity so a future fix cannot silently change the portal."""
    org_id, entity_id = await _org_with_entity(db_session, activate=True)
    claim = await _submitted_claim(db_session, org_id, entity_id)
    withdrawn = await lock.withdraw_claim(db_session, org_id, claim.id)
    await db_session.commit()
    assert withdrawn.status == "withdrawn"
    assert withdrawn.status_code == "2", "the stale code this surface must not read"

    view = await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert all(s.claims == 0 for s in view.stages)
    assert view.claims == ()
    assert view.not_shown_claims == 1
    assert view.total_claims == 1


@pytest.mark.asyncio
async def test_wo93_an_engine_status_outside_the_ladder_is_counted_not_shown(db_session):
    """`rejected`, and a status this module has not met, match no stage. Neither
    is given an invented seventh stage (§10) and neither is silently dropped —
    the count is what keeps the arithmetic honest."""
    org_id, entity_id = await _org_with_entity(db_session)
    rejected = await _make_claim(db_session, org_id, entity_id, ref_period="2026-Q1")
    rejected.status = "rejected"
    rejected.vat_eur = Decimal("900.00")
    future = await _make_claim(db_session, org_id, entity_id, ref_period="2026-Q2")
    future.status = "escheated"  # not in CLAIM_STATUSES — a future/foreign value
    future.vat_eur = Decimal("800.00")
    await db_session.commit()

    view = await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert view.not_shown_claims == 2
    assert view.total_claims == 2
    assert all(s.claims == 0 and s.vat_eur == Decimal("0.00") for s in view.stages)
    assert view.claims == ()


@pytest.mark.asyncio
async def test_wo93_every_claim_is_accounted_for_exactly_once(db_session):
    """`Σ stages.claims + not_shown_claims == total_claims`, over a mixed
    portfolio — and the claim ROWS are exactly the shown ones."""
    org_id, entity_id = await _org_with_entity(db_session)
    await _clean_claim(db_session, org_id, entity_id)  # ready
    for period, engine in (
        ("2026-Q1", "submitted"),
        ("2026-Q3", "paid"),
        ("2026-Q4", "withdrawn"),
        ("2026-YEAR", "rejected"),
    ):
        c = await _make_claim(db_session, org_id, entity_id, ref_period=period)
        c.status = engine
        c.vat_eur = Decimal("100.00")
    await db_session.commit()

    view = await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert view.total_claims == 5
    shown = sum(s.claims for s in view.stages)
    assert shown == 3 and view.not_shown_claims == 2
    assert shown + view.not_shown_claims == view.total_claims
    assert len(view.claims) == shown


# --------------------------------------------------------------------------- #
# The claim rows, the money, and the empty year
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo93_a_claim_row_carries_the_entity_name_country_and_period(db_session):
    """The client reads their OWN legal entity's name, resolved through the
    issuer service — never a UUID, and never a claim id (there is nothing to
    open: the R1 grain already identifies the row)."""
    org_id, entity_id = await _org_with_entity(db_session)
    await _clean_claim(db_session, org_id, entity_id)

    view = await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_ENDED)
    (row,) = view.claims
    assert row.refund_country == "LV"
    assert row.period == "2026-Q2"
    assert row.stage == "ready"
    assert row.entity_name and row.entity_name != entity_id
    assert not hasattr(row, "id")


@pytest.mark.asyncio
async def test_wo93_stage_totals_match_hand_computed_decimals(db_session):
    org_id, entity_id = await _org_with_entity(db_session)
    await _clean_claim(db_session, org_id, entity_id)  # ready, EUR 420.00
    submitted = await _make_claim(db_session, org_id, entity_id, ref_period="2026-Q1")
    submitted.status = "submitted"
    submitted.status_code = "2"
    submitted.vat_eur = Decimal("1234.56")
    paid = await _make_claim(db_session, org_id, entity_id, ref_period="2026-Q3")
    paid.status = "paid"
    paid.status_code = "3A"
    paid.vat_eur = Decimal("7890.12")
    await db_session.commit()

    view = await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert view.currency == "EUR"
    assert _stage(view, "ready").vat_eur == Decimal("420.00")
    assert _stage(view, "filed").vat_eur == Decimal("1234.56")
    assert _stage(view, "refunded").vat_eur == Decimal("7890.12")
    assert sum((s.vat_eur for s in view.stages), Decimal("0")) == Decimal("9544.68")


@pytest.mark.asyncio
async def test_wo93_a_cross_currency_draft_reports_a_null_figure_not_zero(db_session):
    """§4.14 — a draft whose lines span currencies has no single-currency VAT
    base (`preview_vat_base` refuses), so there is no figure to state. `None`
    says exactly that; `0.00` would be a wrong number in front of a client, and
    a foreign amount labelled EUR would be the §4.14 violation itself. The
    claim is still SHOWN, in the stage that gets a human to look at it."""
    org_id, entity_id = await _org_with_entity(db_session)
    for supplier, number, sha in (("Q8", "INV-A", "d" * 64), ("DKV", "INV-B", "e" * 64)):
        vendor = await _make_vendor(db_session, org_id, name=supplier)
        inv = await _make_invoice(db_session, org_id, vendor, number=number)
        await _attach_document(db_session, org_id, inv, sha=sha)
    claim = await _make_claim(db_session, org_id, entity_id)
    await db_session.commit()
    await _make_txn(db_session, org_id, entity_id, supplier="Q8", invoice_ref="INV-A")
    await _make_txn(
        db_session,
        org_id,
        entity_id,
        supplier="DKV",
        invoice_ref="INV-B",
        currency="SEK",
        line_seq=2,
        net_eur=Decimal("1000.00"),
        vat_eur=Decimal("250.00"),
    )
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org_id, claim.id)
    await db_session.commit()

    # A healthy claim beside it: one bad claim never blanks the whole year.
    await _clean_claim(
        db_session,
        org_id,
        entity_id,
        supplier="BP",
        number="INV-OK",
        sha="c" * 64,
        ref_period="2026-Q1",
        month="2026-02",
        txn_date=date(2026, 2, 10),
    )

    view = await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert view.total_claims == 2
    mismatched = [c for c in view.claims if c.period == "2026-Q2"]
    assert len(mismatched) == 1
    assert mismatched[0].vat_eur is None, "no figure is stated, and none is invented"
    assert mismatched[0].stage == "needs_attention"
    assert _stage(view, "needs_attention").vat_eur == Decimal("0.00")
    assert _stage(view, "ready").vat_eur == Decimal("420.00")


@pytest.mark.asyncio
async def test_wo93_a_filed_claim_with_no_frozen_figure_reports_null(db_session):
    org_id, entity_id = await _org_with_entity(db_session)
    c = await _make_claim(db_session, org_id, entity_id)
    c.status = "submitted"
    c.status_code = "2"
    c.vat_eur = None
    await db_session.commit()

    view = await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert view.claims[0].vat_eur is None
    assert _stage(view, "filed").claims == 1
    assert _stage(view, "filed").vat_eur == Decimal("0.00")


@pytest.mark.asyncio
async def test_wo93_a_year_with_no_claims_renders_the_empty_state_not_an_error(db_session):
    org_id, entity_id = await _org_with_entity(db_session)
    await _clean_claim(db_session, org_id, entity_id)  # a 2026 claim

    view = await client_status.client_claim_status(db_session, org_id, 2019, today=PERIOD_ENDED)
    assert view.total_claims == 0
    assert view.claims == ()
    assert view.not_shown_claims == 0
    assert [s.stage for s in view.stages] == list(client_status.CLIENT_STAGES)
    assert all(s.claims == 0 and s.vat_eur == Decimal("0.00") for s in view.stages)


@pytest.mark.asyncio
async def test_wo93_the_year_selects_on_the_claims_own_ref_period(db_session):
    org_id, entity_id = await _org_with_entity(db_session)
    for period in ("2025-Q4", "2026-Q2", "2027-YEAR"):
        c = await _make_claim(db_session, org_id, entity_id, ref_period=period)
        c.status = "submitted"
        c.status_code = "2"
        c.vat_eur = Decimal("11.00")
    await db_session.commit()

    for year in (2025, 2026, 2027):
        view = await client_status.client_claim_status(db_session, org_id, year, today=PERIOD_ENDED)
        assert view.total_claims == 1, year
        assert _stage(view, "filed").vat_eur == Decimal("11.00"), year


# --------------------------------------------------------------------------- #
# The gates, and the read-only posture
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo93_an_un_entitled_org_is_refused_module_not_enabled(db_session):
    org = await make_org(db_session)
    await db_session.commit()
    with pytest.raises(AppError) as exc:
        await client_status.client_claim_status(db_session, org.id, 2026)
    assert exc.value.code == "module_not_enabled"
    assert exc.value.status == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("year", [0, 999, 10000, -2026])
async def test_wo93_an_invalid_year_is_refused(db_session, year):
    """Fails CLOSED, through `recovery.validate_year` — an empty year for a typo
    would read to a client as "you have no claims"."""
    org_id, _entity_id = await _org_with_entity(db_session)
    with pytest.raises(AppError) as exc:
        await client_status.client_claim_status(db_session, org_id, year)
    assert exc.value.code == "invalid_year"
    assert exc.value.status == 422


@pytest.mark.asyncio
async def test_wo93_reading_the_portal_mutates_nothing(db_session):
    """§4.19 — opening the portal changes nothing about a claim. Reading it
    twice leaves every column byte-identical."""
    org_id, entity_id = await _org_with_entity(db_session)
    claim, _txn = await _clean_claim(db_session, org_id, entity_id)

    def snapshot(c: VatRefundClaim):
        return (c.status, c.status_code, c.vat_eur, c.updated_at.replace(tzinfo=None))

    before = snapshot(claim)
    await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_ENDED)
    await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_NOT_ENDED)
    await db_session.refresh(claim)
    assert snapshot(claim) == before


def test_wo93_the_service_forks_no_claims_query():
    """R38/R51's binding constraint, applied here: the claim set comes from
    `claim.list_claims` — THE claims-listing query — and the stages from
    `status.derive_stage`. This module holds no `select()` and names no claim
    model, so it cannot drift from the gate that enforces the same rules."""
    tree = ast.parse(inspect.getsource(client_status))
    called = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "select" not in called, "a forked row-selection would bypass list_claims"
    assert "VatRefundClaim" not in called
    assert "VatRefundClaimLine" not in called

    imported = {
        node.module.split(".")[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "list_claims" in imported
    assert "derive_stage" in imported
    assert "preview_vat_base" in imported
