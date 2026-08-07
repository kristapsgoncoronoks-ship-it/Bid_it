"""WO-81 — the cash-recovery analytics service + its read route (G4.3, R38).

Two halves, deliberately.

The ANALYTICS half calls `app.services.transport.recovery.recovery_dashboard`
directly, because the bucket mapping is the point of this order and most of it
is only observable against a controlled clock: the `today` seam is NOT on the
wire (a client that could post its own clock could make a claim 20 days from the
fatal 30-Sep time-bar report as comfortable), so the period-end and
deadline-window scenarios have no HTTP form. Each bucket is proven with a claim
genuinely constructed in that state through the real services
(`claim_lines.build_claim_lines`, `waiver.set_waiver`, `extraction.link_to_invoice`)
— the `test_g2_7_status.py` seeding pattern, reused unchanged so a bucket can
never pass because the fixture faked the stage it maps from.

The ROUTE half goes through HTTP for everything a route owns: Decimal-as-string
on the wire, the structural permission pair, the module entitlement, org scoping
over deliberately OVERLAPPING data, and the `invalid_year` refusal — the
`test_wo79_fuel_routes.py` fixture strategy, unchanged.

Every euro asserted here is hand-computed in `Decimal`. There is no float in this
file, and none in the module it proves.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.errors import AppError
from app.models.invoice import Invoice
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine
from app.models.vendor import Vendor
from app.services import extraction, modules
from app.services.transport import claim as claim_svc
from app.services.transport import claim_lines, fuel_ingest, recovery, waiver
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org

V = "/api/v1"
PATH = f"{V}/transport/recovery-dashboard"

# 2026-Q2 ends 30 Jun 2026; its Art. 15 filing deadline is 30 Sep 2027.
PERIOD_ENDED = date(2026, 7, 1)
PERIOD_NOT_ENDED = date(2026, 6, 15)
# Exactly `DEADLINE_RISK_DAYS` (60) before 30 Sep 2027 — the inclusive edge.
AT_RISK_EDGE = date(2027, 8, 1)
# One day earlier: 61 days out, outside the window.
OUTSIDE_EDGE = date(2027, 7, 31)


# --------------------------------------------------------------------------- #
# Domain seeding (the test_g2_7_status.py shapes)
# --------------------------------------------------------------------------- #


async def _make_claim(db_session, org_id: str, entity_id: str, **overrides) -> VatRefundClaim:
    kwargs = dict(refund_country="LV", ref_period="2026-Q2")
    kwargs.update(overrides)
    return await claim_svc.get_or_create_claim(db_session, org_id, entity_id=entity_id, **kwargs)


async def _make_vendor(db_session, org_id: str, *, name: str = "Q8") -> Vendor:
    """The vendor NAME is the fuel-card supplier code: `invoice_match.
    registered_invoices` looks the vendor up by exactly that name, so a claim
    line only resolves when the two agree."""
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
        # WO-88 (§4.15): a non-EUR line carries the FX provenance a real
        # ingestion records; EUR is the identity and needs none. Fixture
        # privilege raised — the cross-currency scenario below is unchanged
        # (the claim still spans two currencies and `preview_vat_base` still
        # refuses to sum them), and not one assertion moves.
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
) -> VatRefundClaim:
    """A draft claim with ONE documented, resolved line — the baseline every
    scenario below tweaks exactly one thing on. `derive_stage` reads `1E` for it
    once the period has ended, so it buckets `ready`.

    `month`/`txn_date` must fall INSIDE the claim's own `ref_period`:
    `build_claim_lines` scopes a claim's transactions to its period's months, so
    a mismatched month silently produces a claim with no lines at all."""
    vendor = await _make_vendor(db_session, org_id, name=supplier)
    inv = await _make_invoice(db_session, org_id, vendor, number=number)
    claim = await _make_claim(db_session, org_id, entity_id, **claim_kwargs)
    await db_session.commit()
    await _make_txn(
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
    return claim


async def _org_with_entity(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    return org.id, entity.id


def _bucket(dash: recovery.RecoveryDashboard, state: str) -> recovery.Bucket:
    found = [b for b in dash.buckets if b.state == state]
    assert len(found) == 1, f"{state} is not a bucket: {[b.state for b in dash.buckets]}"
    return found[0]


def _state_of(dash: recovery.RecoveryDashboard) -> str:
    """The single non-empty bucket of a one-claim portfolio."""
    filled = [b.state for b in dash.buckets if b.claims]
    assert len(filled) == 1, f"expected exactly one populated bucket, got {filled}"
    return filled[0]


# --------------------------------------------------------------------------- #
# The six readiness states — one claim genuinely constructed in each
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo81_the_six_buckets_are_always_present_in_the_harvested_order(db_session):
    """`BA_fleet_fuel.md` §2.4's own order, and all six even when empty — a
    bucket that vanished at zero would make "nothing is at deadline risk" and
    "the dashboard forgot to tell you" look identical."""
    org_id, _entity_id = await _org_with_entity(db_session)
    dash = await recovery.recovery_dashboard(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert [b.state for b in dash.buckets] == [
        "ready",
        "deadline",
        "missing",
        "below",
        "submitted",
        "paid",
    ]
    assert recovery.READINESS_STATES == tuple(b.state for b in dash.buckets)


@pytest.mark.asyncio
async def test_wo81_a_draft_with_an_unresolved_line_buckets_missing(db_session):
    """Stage 1A ("Missing documents", §3.D) — no vendor registered at all, so
    the line resolves UNMATCHED and the checklist fails."""
    org_id, entity_id = await _org_with_entity(db_session)
    claim = await _make_claim(db_session, org_id, entity_id)
    await db_session.commit()
    await _make_txn(db_session, org_id, entity_id, invoice_ref="INV-9999")
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org_id, claim.id)
    await db_session.commit()

    dash = await recovery.recovery_dashboard(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert _state_of(dash) == "missing"
    assert _bucket(dash, "missing").vat_eur == Decimal("420.00")


@pytest.mark.asyncio
async def test_wo81_a_below_minimum_draft_buckets_below(db_session):
    """Art. 17: €400 quarterly. €2.10 of VAT is a 1C caveat whose CAUSE is the
    threshold — `minimum.below_minimum` is re-asked, never assumed from 1C."""
    org_id, entity_id = await _org_with_entity(db_session)
    await _clean_claim(db_session, org_id, entity_id, vat_eur=Decimal("2.10"))

    dash = await recovery.recovery_dashboard(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert _state_of(dash) == "below"
    assert _bucket(dash, "below").vat_eur == Decimal("2.10")


@pytest.mark.asyncio
async def test_wo81_a_waiver_only_1c_buckets_ready_not_below(db_session):
    """Interpretation 2 (the module docstring): `derive_stage` emits 1C for
    EITHER a below-minimum figure or an active receipt-control waiver. Only the
    first is a THRESHOLD problem — an above-minimum claim carrying a waiver has
    nothing an operator must fix, so it is `ready`."""
    org_id, entity_id = await _org_with_entity(db_session)
    claim = await _clean_claim(db_session, org_id, entity_id)
    await _make_txn(
        db_session,
        org_id,
        entity_id,
        supplier="CASHCO",
        invoice_ref="INPUT-CASH",
        net_eur=Decimal("50.00"),
        vat_eur=Decimal("10.50"),
        line_seq=2,
    )
    await db_session.commit()
    await waiver.set_waiver(db_session, org_id, claim_id=claim.id, supplier="CASHCO")
    await db_session.commit()
    await claim_lines.build_claim_lines(db_session, org_id, claim.id)
    await db_session.commit()

    dash = await recovery.recovery_dashboard(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert _state_of(dash) == "ready"


@pytest.mark.asyncio
async def test_wo81_a_period_not_ended_draft_buckets_ready_not_missing(db_session):
    """Interpretation 1: stage 1B has nothing to fix — it becomes fileable when
    its own period closes. Reporting it as `missing` would invent a data gap,
    and the spec gives six states, not seven (§10)."""
    org_id, entity_id = await _org_with_entity(db_session)
    await _clean_claim(db_session, org_id, entity_id)

    dash = await recovery.recovery_dashboard(db_session, org_id, 2026, today=PERIOD_NOT_ENDED)
    assert _state_of(dash) == "ready"
    assert dash.deadline_risk_claims == 0


@pytest.mark.asyncio
async def test_wo81_a_fileable_draft_inside_the_risk_window_buckets_deadline(db_session):
    org_id, entity_id = await _org_with_entity(db_session)
    await _clean_claim(db_session, org_id, entity_id)

    dash = await recovery.recovery_dashboard(db_session, org_id, 2026, today=AT_RISK_EDGE)
    assert _state_of(dash) == "deadline"
    assert dash.deadline_risk_claims == 1
    assert _bucket(dash, "deadline").vat_eur == Decimal("420.00")


@pytest.mark.asyncio
async def test_wo81_deadline_risk_boundary_is_inclusive_at_exactly_60_days(db_session):
    """R9/A4: `DEADLINE_RISK_DAYS = 60` of the 30-Sep-year+1 cutoff. Both sides
    of the edge, one day apart — 60 days out is at risk, 61 days out is not."""
    assert recovery.DEADLINE_RISK_DAYS == 60
    assert (date(2027, 9, 30) - AT_RISK_EDGE).days == 60
    assert (date(2027, 9, 30) - OUTSIDE_EDGE).days == 61

    org_id, entity_id = await _org_with_entity(db_session)
    await _clean_claim(db_session, org_id, entity_id)

    inside = await recovery.recovery_dashboard(db_session, org_id, 2026, today=AT_RISK_EDGE)
    assert inside.deadline_risk_claims == 1
    assert _state_of(inside) == "deadline"

    outside = await recovery.recovery_dashboard(db_session, org_id, 2026, today=OUTSIDE_EDGE)
    assert outside.deadline_risk_claims == 0
    assert _state_of(outside) == "ready"


@pytest.mark.asyncio
async def test_wo81_a_blocked_claim_at_risk_keeps_its_blocking_bucket_but_is_counted(db_session):
    """Interpretation 3: the bucket says WHAT TO DO, the count says HOW URGENT.
    A below-minimum claim inside the window stays `below` (the threshold is what
    is stopping it) and is still counted in `deadline_risk_claims` — which is
    exactly why R38 lists that count separately from the six states."""
    org_id, entity_id = await _org_with_entity(db_session)
    await _clean_claim(db_session, org_id, entity_id, vat_eur=Decimal("2.10"))

    dash = await recovery.recovery_dashboard(db_session, org_id, 2026, today=AT_RISK_EDGE)
    assert _state_of(dash) == "below"
    assert _bucket(dash, "deadline").claims == 0
    assert dash.deadline_risk_claims == 1


@pytest.mark.asyncio
async def test_wo81_a_submitted_claim_buckets_submitted_and_a_paid_one_buckets_paid(db_session):
    """The two FILED states read the claim's own FROZEN `vat_eur` (R13/C10) —
    never a re-derivation, which is the whole point of freezing it."""
    org_id, entity_id = await _org_with_entity(db_session)
    submitted = await _make_claim(db_session, org_id, entity_id, ref_period="2026-Q1")
    submitted.status = "submitted"
    submitted.vat_eur = Decimal("1000.00")
    paid = await _make_claim(db_session, org_id, entity_id, ref_period="2026-Q2")
    paid.status = "paid"
    paid.vat_eur = Decimal("2500.00")
    approved = await _make_claim(db_session, org_id, entity_id, ref_period="2026-Q3")
    approved.status = "approved"
    approved.vat_eur = Decimal("300.00")
    await db_session.commit()

    dash = await recovery.recovery_dashboard(db_session, org_id, 2026, today=PERIOD_ENDED)
    # `approved` is filed-and-awaiting-money, so it shares the `submitted` state.
    assert _bucket(dash, "submitted").claims == 2
    assert _bucket(dash, "submitted").vat_eur == Decimal("1300.00")
    assert _bucket(dash, "paid").claims == 1
    assert _bucket(dash, "paid").vat_eur == Decimal("2500.00")
    # A filed claim is past the time-bar — it can never be a deadline risk.
    assert dash.deadline_risk_claims == 0


@pytest.mark.asyncio
async def test_wo81_withdrawn_and_rejected_claims_are_excluded_with_a_reason(db_session):
    """Interpretation 4: they match none of the six states. Reported by name
    rather than dropped, because a silent drop would make the row count lie —
    and a seventh readiness state would be invented vocabulary (§10)."""
    org_id, entity_id = await _org_with_entity(db_session)
    withdrawn = await _make_claim(db_session, org_id, entity_id, ref_period="2026-Q1")
    withdrawn.status = "withdrawn"
    rejected = await _make_claim(db_session, org_id, entity_id, ref_period="2026-Q2")
    rejected.status = "rejected"
    rejected.vat_eur = Decimal("900.00")
    await db_session.commit()

    dash = await recovery.recovery_dashboard(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert [(e.reason, e.claims) for e in dash.excluded] == [("rejected", 1), ("withdrawn", 1)]
    assert all(b.claims == 0 for b in dash.buckets)
    # And their euros are nowhere in the north-star figures.
    assert dash.recovered_eur == Decimal("0.00")
    assert dash.awaiting_eur == Decimal("0.00")
    assert dash.claimable_eur == Decimal("0.00")


@pytest.mark.asyncio
async def test_wo81_an_unrecognised_status_is_excluded_by_name_not_counted_as_awaiting(db_session):
    """The dispatch is on the statuses this surface KNOWS; anything else is
    excluded under its own name. A status this module has not met must never
    fall through into a euro total — silently reporting an unknown claim as
    "awaiting €900" is exactly the wrong-money-figure class of error this
    surface exists to prevent."""
    org_id, entity_id = await _org_with_entity(db_session)
    c = await _make_claim(db_session, org_id, entity_id)
    c.status = "escheated"  # not in CLAIM_STATUSES — a future/foreign value
    c.vat_eur = Decimal("900.00")
    await db_session.commit()

    dash = await recovery.recovery_dashboard(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert [(e.reason, e.claims) for e in dash.excluded] == [("escheated", 1)]
    assert all(b.claims == 0 for b in dash.buckets)
    assert dash.awaiting_eur == Decimal("0.00")
    assert dash.recovered_eur == Decimal("0.00")
    assert dash.claimable_eur == Decimal("0.00")
    assert dash.total_claims == 1


@pytest.mark.asyncio
async def test_wo81_every_claim_is_accounted_for_exactly_once(db_session):
    """`Σ buckets + Σ excluded == total_claims`, over a mixed portfolio."""
    org_id, entity_id = await _org_with_entity(db_session)
    await _clean_claim(db_session, org_id, entity_id)  # ready
    for period, status_value in (
        ("2026-Q1", "submitted"),
        ("2026-Q3", "paid"),
        ("2026-Q4", "withdrawn"),
        ("2026-YEAR", "rejected"),
    ):
        c = await _make_claim(db_session, org_id, entity_id, ref_period=period)
        c.status = status_value
        c.vat_eur = Decimal("100.00")
    await db_session.commit()

    dash = await recovery.recovery_dashboard(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert dash.total_claims == 5
    bucketed = sum(b.claims for b in dash.buckets)
    excluded = sum(e.claims for e in dash.excluded)
    assert bucketed == 3 and excluded == 2
    assert bucketed + excluded == dash.total_claims


# --------------------------------------------------------------------------- #
# The north-star euros
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo81_totals_match_hand_computed_decimals_and_reconcile(db_session):
    """R38's acceptance line: "the dashboard totals reconcile exactly with the
    underlying claim reports." `recovered + awaiting + claimable` is the whole
    bucketed VAT — asserted against Decimals computed by hand, not by the code
    under test."""
    org_id, entity_id = await _org_with_entity(db_session)
    # A draft: €420.00 claimable, previewed (its own `vat_eur` COLUMN is NULL).
    draft = await _clean_claim(db_session, org_id, entity_id)
    assert draft.vat_eur is None, "a draft freezes nothing — the preview is the only source"

    submitted = await _make_claim(db_session, org_id, entity_id, ref_period="2026-Q1")
    submitted.status = "submitted"
    submitted.vat_eur = Decimal("1234.56")
    paid = await _make_claim(db_session, org_id, entity_id, ref_period="2026-Q3")
    paid.status = "paid"
    paid.vat_eur = Decimal("7890.12")
    await db_session.commit()

    dash = await recovery.recovery_dashboard(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert dash.currency == "EUR"
    assert dash.recovered_eur == Decimal("7890.12")
    assert dash.awaiting_eur == Decimal("1234.56")
    assert dash.claimable_eur == Decimal("420.00")

    total = dash.recovered_eur + dash.awaiting_eur + dash.claimable_eur
    assert total == Decimal("9544.68")
    assert total == sum((b.vat_eur for b in dash.buckets), Decimal("0"))


@pytest.mark.asyncio
async def test_wo81_claimable_covers_every_draft_bucket_including_the_blocked_ones(db_session):
    """`claimable_eur` is "not yet filed", so it spans all four draft buckets;
    the per-bucket euros are what show how much of it is BLOCKED, and by what."""
    org_id, entity_id = await _org_with_entity(db_session)
    await _clean_claim(db_session, org_id, entity_id, number="INV-0001", sha="a" * 64)
    # A second, below-minimum claim in a different period (its own supplier, so
    # its own vendor: the resolution key is (vendor name, invoice number)).
    await _clean_claim(
        db_session,
        org_id,
        entity_id,
        vat_eur=Decimal("2.10"),
        supplier="BP",
        number="INV-0002",
        sha="b" * 64,
        ref_period="2026-Q1",
        month="2026-02",
        txn_date=date(2026, 2, 10),
    )
    await db_session.commit()

    dash = await recovery.recovery_dashboard(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert _bucket(dash, "ready").vat_eur == Decimal("420.00")
    assert _bucket(dash, "below").vat_eur == Decimal("2.10")
    assert dash.claimable_eur == Decimal("422.10")


@pytest.mark.asyncio
async def test_wo81_median_days_to_refund_over_an_even_sample(db_session):
    """Exact-Decimal median, ROUND_HALF_UP at one decimal — never
    `statistics.median`, which would return a float into a report (§4.9)."""
    org_id, entity_id = await _org_with_entity(db_session)
    for period, submitted_on, paid_on in (
        ("2026-Q1", date(2027, 1, 10), date(2027, 3, 11)),  # 60 days
        ("2026-Q2", date(2027, 1, 10), date(2027, 4, 15)),  # 95 days
        ("2026-Q3", date(2027, 1, 10), date(2027, 5, 11)),  # 121 days
        ("2026-Q4", date(2027, 1, 10), date(2027, 6, 10)),  # 151 days
    ):
        c = await _make_claim(db_session, org_id, entity_id, ref_period=period)
        c.status = "paid"
        c.vat_eur = Decimal("100.00")
        c.submitted_date = submitted_on
        c.paid_date = paid_on
    await db_session.commit()

    dash = await recovery.recovery_dashboard(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert dash.days_to_refund_sample == 4
    # (95 + 121) / 2 = 108
    assert dash.median_days_to_refund == Decimal("108.0")
    assert isinstance(dash.median_days_to_refund, Decimal)


@pytest.mark.asyncio
async def test_wo81_median_is_null_not_zero_when_no_paid_claim_carries_both_dates(db_session):
    """`null` means "no claim has both dates yet"; 0 would claim refunds arrive
    the same day they are filed. `days_to_refund_sample` makes the null legible
    — nothing writes those dates today (G2.9), which is the recorded deviation."""
    org_id, entity_id = await _org_with_entity(db_session)
    c = await _make_claim(db_session, org_id, entity_id)
    c.status = "paid"
    c.vat_eur = Decimal("500.00")
    await db_session.commit()

    dash = await recovery.recovery_dashboard(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert dash.median_days_to_refund is None
    assert dash.days_to_refund_sample == 0
    assert dash.recovered_eur == Decimal("500.00")


@pytest.mark.asyncio
async def test_wo81_an_empty_year_returns_zeroes_not_an_error(db_session):
    org_id, entity_id = await _org_with_entity(db_session)
    await _clean_claim(db_session, org_id, entity_id)  # a 2026 claim

    dash = await recovery.recovery_dashboard(db_session, org_id, 2019, today=PERIOD_ENDED)
    assert dash.total_claims == 0
    assert dash.excluded == ()
    assert all(b.claims == 0 and b.vat_eur == Decimal("0.00") for b in dash.buckets)
    assert dash.recovered_eur == dash.awaiting_eur == dash.claimable_eur == Decimal("0.00")
    assert dash.deadline_risk_claims == 0
    assert dash.currency_mismatch_claims == 0
    assert dash.median_days_to_refund is None


@pytest.mark.asyncio
async def test_wo81_the_year_selects_on_the_claims_own_ref_period(db_session):
    org_id, entity_id = await _org_with_entity(db_session)
    for period in ("2025-Q4", "2026-Q2", "2027-YEAR"):
        c = await _make_claim(db_session, org_id, entity_id, ref_period=period)
        c.status = "submitted"
        c.vat_eur = Decimal("11.00")
    await db_session.commit()

    for year in (2025, 2026, 2027):
        dash = await recovery.recovery_dashboard(db_session, org_id, year, today=PERIOD_ENDED)
        assert dash.total_claims == 1, year
        assert dash.awaiting_eur == Decimal("11.00"), year


# --------------------------------------------------------------------------- #
# §4.14 — no aggregate sums across currencies
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo81_a_cross_currency_draft_is_excluded_from_the_euros_and_counted(db_session):
    """These are MULTI-COUNTRY claims. A draft whose lines carry two different
    local currencies has no single-currency VAT base — `preview_vat_base`
    refuses it (`claim_currency_mismatch`). The dashboard must neither blow up
    nor silently coerce: the claim buckets `missing`, contributes ZERO to every
    euro, and increments `currency_mismatch_claims` so the shortfall is VISIBLE.
    """
    org_id, entity_id = await _org_with_entity(db_session)
    # TWO resolved lines, each internally consistent, disagreeing with each
    # other on currency — the shape `claim_lines` permits (it refuses only a
    # mixed-currency line) and `preview_vat_base` then refuses to sum.
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
    currencies = {
        ln.currency
        for ln in await db_session.scalars(
            select(VatRefundClaimLine).where(VatRefundClaimLine.claim_id == claim.id)
        )
    }
    assert len(currencies) == 2, f"the fixture must really span currencies, got {currencies}"

    # A healthy claim beside it, so the "one bad claim never blanks the year"
    # guarantee is what is actually asserted.
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

    dash = await recovery.recovery_dashboard(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert dash.total_claims == 2
    assert dash.currency_mismatch_claims == 1
    assert _bucket(dash, "missing").claims == 1
    assert _bucket(dash, "missing").vat_eur == Decimal("0.00"), "no foreign amount is summed"
    assert _bucket(dash, "ready").vat_eur == Decimal("420.00")
    # The healthy claim's euros are intact; the mismatched one contributed none.
    assert dash.claimable_eur == Decimal("420.00")


@pytest.mark.asyncio
async def test_wo81_the_service_never_sums_a_local_or_paid_amount(db_session):
    """The absence assertion (§8 rule 8): `vat_local` and `paid_amount` are
    per-refund-state / currency-ambiguous, so no total here may read them. A
    paid claim carrying a wildly different `paid_amount` and `vat_local` must
    move `recovered_eur` by exactly zero."""
    org_id, entity_id = await _org_with_entity(db_session)
    c = await _make_claim(db_session, org_id, entity_id)
    c.status = "paid"
    c.vat_eur = Decimal("100.00")
    c.vat_local = Decimal("999999.00")
    c.currency = "SEK"
    c.paid_amount = Decimal("888888.00")
    await db_session.commit()

    dash = await recovery.recovery_dashboard(db_session, org_id, 2026, today=PERIOD_ENDED)
    assert dash.recovered_eur == Decimal("100.00")
    assert dash.currency == "EUR"

    # And structurally, over the module's CODE (its docstring names these
    # columns precisely to explain why they are never read): no `statistics`
    # import, no `float()` call, and no attribute access to `vat_local` or
    # `paid_amount` anywhere in the module.
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(recovery))
    imported = {
        node.names[0].name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "statistics" not in imported, "a float median must never enter this module (§4.9)"
    called = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "float" not in called, "no float in a money path (§4.9)"
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not attributes & {"vat_local", "paid_amount"}, (
        "a currency-ambiguous column must never be read here (§4.14)"
    )


# --------------------------------------------------------------------------- #
# The gates
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo81_an_un_entitled_org_is_refused_module_not_enabled(db_session):
    org = await make_org(db_session)
    await db_session.commit()
    with pytest.raises(AppError) as exc:
        await recovery.recovery_dashboard(db_session, org.id, 2026)
    assert exc.value.code == "module_not_enabled"
    assert exc.value.status == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("year", [0, 999, 10000, -2026])
async def test_wo81_a_year_no_ref_period_could_carry_is_refused(db_session, year):
    """Fails CLOSED: an empty result for a typo'd year is indistinguishable
    from "you have nothing to recover"."""
    org_id, _entity_id = await _org_with_entity(db_session)
    with pytest.raises(AppError) as exc:
        await recovery.recovery_dashboard(db_session, org_id, year)
    assert exc.value.code == "invalid_year"
    assert exc.value.status == 422


@pytest.mark.asyncio
async def test_wo81_reading_the_dashboard_mutates_nothing(db_session):
    """§4.19 — advisory, and this one does not even hold a writable intent.
    Reading it twice leaves every claim column byte-identical."""
    org_id, entity_id = await _org_with_entity(db_session)
    claim = await _clean_claim(db_session, org_id, entity_id)

    def snapshot(c: VatRefundClaim):
        # `updated_at` is compared tz-naive: SQLite hands it back without a
        # tzinfo after a refresh, and the point of the assertion is that the
        # INSTANT did not move, not how the driver spells it.
        return (c.status, c.status_code, c.vat_eur, c.updated_at.replace(tzinfo=None))

    before = snapshot(claim)

    await recovery.recovery_dashboard(db_session, org_id, 2026, today=PERIOD_ENDED)
    await recovery.recovery_dashboard(db_session, org_id, 2026, today=AT_RISK_EDGE)
    await db_session.refresh(claim)

    assert snapshot(claim) == before


# --------------------------------------------------------------------------- #
# The route (the WO-79 HTTP fixture strategy)
# --------------------------------------------------------------------------- #


async def _register_org(client: AsyncClient):
    suffix = uuid.uuid4().hex[:8]
    r = await client.post(
        f"{V}/auth/register",
        json={
            "organization_name": f"WO81 Org {suffix}",
            "name": "Owner",
            "email": f"owner-{suffix}@wo81.example.io",
            "password": "supersecret",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['token']['access_token']}"}, body["organization"]["id"]


async def _member_with_role(client: AsyncClient, owner_headers, db_session, stored_role: str):
    from sqlalchemy import update

    from app.models.user import User, UserRole

    email = f"{stored_role}-{uuid.uuid4().hex[:8]}@wo81.example.io"
    inv = await client.post(
        f"{V}/team/invites", headers=owner_headers, json={"email": email, "role": "admin"}
    )
    assert inv.status_code == 201, inv.text
    acc = await client.post(
        f"{V}/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "Member", "password": "supersecret"},
    )
    assert acc.status_code == 201, acc.text
    body = acc.json()
    await db_session.execute(
        update(User)
        .where(User.id == body["user"]["id"])
        .values(role=UserRole(stored_role), is_expense_approver=False)
    )
    await db_session.commit()
    return {"Authorization": f"Bearer {body['token']['access_token']}"}


async def _http_org_with_a_filed_claim(client, db_session, *, vat_eur: Decimal):
    headers, org_id = await _register_org(client)
    await modules.set_enabled(db_session, org_id, "transport", True)
    entity = await make_entity(db_session, org_id)
    claim = await _make_claim(db_session, org_id, entity.id)
    claim.status = "paid"
    claim.vat_eur = vat_eur
    await db_session.commit()
    return headers, org_id, claim.id


@pytest.mark.asyncio
async def test_wo81_route_returns_the_dashboard(client, db_session):
    headers, _org_id, _claim_id = await _http_org_with_a_filed_claim(
        client, db_session, vat_eur=Decimal("1500.00")
    )
    r = await client.get(PATH, headers=headers, params={"year": 2026})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["year"] == 2026
    assert body["currency"] == "EUR"
    assert body["total_claims"] == 1
    assert [b["state"] for b in body["buckets"]] == list(recovery.READINESS_STATES)
    assert body["excluded"] == []
    assert body["days_to_refund_sample"] == 0
    assert body["median_days_to_refund"] is None


@pytest.mark.asyncio
async def test_wo81_amounts_cross_the_wire_as_exact_decimal_strings(client, db_session):
    """§4.9 — pydantic v2 serializes `Decimal` as a JSON string. A float path
    anywhere here would arrive as a bare number."""
    headers, _org_id, _claim_id = await _http_org_with_a_filed_claim(
        client, db_session, vat_eur=Decimal("1234.56")
    )
    r = await client.get(PATH, headers=headers, params={"year": 2026})
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("recovered_eur", "awaiting_eur", "claimable_eur"):
        assert isinstance(body[key], str), f"{key} is not a string: {body[key]!r}"
    assert body["recovered_eur"] == "1234.56"
    assert body["awaiting_eur"] == "0.00"
    assert body["claimable_eur"] == "0.00"
    for bucket in body["buckets"]:
        assert isinstance(bucket["vat_eur"], str), bucket
    assert [b["vat_eur"] for b in body["buckets"] if b["state"] == "paid"] == ["1234.56"]


@pytest.mark.asyncio
async def test_wo81_an_empty_year_is_200_with_zeroes_over_http(client, db_session):
    headers, _org_id, _claim_id = await _http_org_with_a_filed_claim(
        client, db_session, vat_eur=Decimal("10.00")
    )
    r = await client.get(PATH, headers=headers, params={"year": 2019})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_claims"] == 0
    assert all(b["claims"] == 0 and b["vat_eur"] == "0.00" for b in body["buckets"])


@pytest.mark.asyncio
async def test_wo81_an_invalid_year_is_422_invalid_year(client, db_session):
    headers, _org_id, _claim_id = await _http_org_with_a_filed_claim(
        client, db_session, vat_eur=Decimal("10.00")
    )
    r = await client.get(PATH, headers=headers, params={"year": 99999})
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "invalid_year"


@pytest.mark.asyncio
async def test_wo81_module_disabled_refuses_403_module_not_enabled(client, db_session):
    """ADR-P3 rule 3 — the entitlement is enforced INSIDE the service, so an
    un-entitled org is refused even holding TRANSPORT_READ."""
    headers, _org_id = await _register_org(client)
    r = await client.get(PATH, headers=headers, params={"year": 2026})
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "module_not_enabled"


@pytest.mark.asyncio
async def test_wo81_employee_is_denied_and_accountant_is_granted(client, db_session, role_client):
    """Deny-by-default on the wire (§4.6/§4.7). EMPLOYEE holds no
    TRANSPORT_READ and is refused by the ROUTER dependency, before any service
    work; ACCOUNTANT holds it and reads the surface."""
    headers, _org_id, _claim_id = await _http_org_with_a_filed_claim(
        client, db_session, vat_eur=Decimal("42.00")
    )

    employee = await role_client("user")
    r = await employee.get(PATH, params={"year": 2026})
    assert r.status_code == 403, r.text

    acct = await _member_with_role(client, headers, db_session, "accountant")
    r = await client.get(PATH, headers=acct, params={"year": 2026})
    assert r.status_code == 200, r.text
    assert r.json()["recovered_eur"] == "42.00"


@pytest.mark.asyncio
async def test_wo81_the_route_is_gated_on_transport_read(client, db_session):
    """The recorded permission decision (WO-81): this IS the derived analytics
    slice WO-79's `fuel.py` reserved `TRANSPORT_READ` for. Pinned structurally
    so a future edit has to change it deliberately."""
    from app.api.routes.transport import recovery as recovery_route
    from app.core import authz

    declared = set()
    for dep in recovery_route.router.dependencies:
        declared.update(getattr(dep.dependency, authz.PERMISSIONS_ATTR, ()))
    assert declared == {authz.Permission.TRANSPORT_READ}, declared


@pytest.mark.asyncio
async def test_wo81_the_dashboard_is_org_scoped_with_overlapping_data(client, db_session):
    """The parity overlap discipline: both orgs hold an IDENTICAL-looking claim
    — same entity shape, refund country, period and amount — so only tenancy
    discriminates and a broken filter cannot pass by luck."""
    orgs = {}
    for key, amount in (("a", Decimal("777.00")), ("b", Decimal("777.00"))):
        headers, org_id, claim_id = await _http_org_with_a_filed_claim(
            client, db_session, vat_eur=amount
        )
        orgs[key] = (headers, org_id, claim_id)

    for mine in ("a", "b"):
        headers, _org_id, _claim_id = orgs[mine]
        r = await client.get(PATH, headers=headers, params={"year": 2026})
        assert r.status_code == 200, r.text
        body = r.json()
        # One claim each — never two. A leak would double both figures.
        assert body["total_claims"] == 1, "TENANT LEAK — the other org's claim is counted"
        assert body["recovered_eur"] == "777.00"


@pytest.mark.asyncio
async def test_wo81_the_route_exposes_no_object_id_to_guess(client, db_session):
    """§4.4 is satisfied by construction here: the response carries counts and
    sums only — no claim id, no entity id — so there is no by-id surface on
    which a cross-tenant probe could return anything but an aggregate of the
    caller's OWN data. Asserted as an absence so a future field addition has to
    revisit it."""
    headers, _org_id, claim_id = await _http_org_with_a_filed_claim(
        client, db_session, vat_eur=Decimal("5.00")
    )
    r = await client.get(PATH, headers=headers, params={"year": 2026})
    assert r.status_code == 200, r.text
    assert claim_id not in r.text
