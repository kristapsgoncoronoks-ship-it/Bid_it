"""WO-82 — the overcharge CLAIM-BACK lifecycle (G4.5, R41; `BA_fleet_fuel.md`
§4.5, §2.4).

The harvested chain, drawn literally:

    detected -> packaged -> claimed -> recovered | rejected | written_off

Every legal edge is walked, every illegal one is refused with the row proven
column-for-column unchanged, every real transition is checked for its old->new
audit row (§4.16), and `recovered_total` — §2.4's *"booked-cash north star"* —
is proven to count only what actually came back.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.transport.overcharge import VatOverchargeClaim
from app.services.transport import contract_audit, fuel_ingest, overcharge
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org

pytestmark = pytest.mark.asyncio

PERIOD = "2026-05"
SUPPLIER = "Q8"


async def _seed_breach(
    db_session,
    org_id: str,
    entity_id: str,
    *,
    line_seq: int = 1,
    qty: Decimal = Decimal("1000.000"),
    net_eur: Decimal = Decimal("1350.00"),
    net_eur_eff: Decimal = Decimal("1300.00"),
    supplier: str = SUPPLIER,
    period: str = PERIOD,
) -> None:
    """One line that breaches a 0.20 €/L agreed discount by 0.15 €/L —
    €150.00 recoverable per 1,000 L, hand-computed."""
    await fuel_ingest.ingest_transaction(
        db_session,
        org_id,
        entity_id=entity_id,
        supplier=supplier,
        period=period,
        line_seq=line_seq,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=date(2026, 5, 12),
        station="Demo Station Riga",
        product="DIESEL",
        qty=qty,
        currency="EUR",
        net_local=net_eur,
        vat_local=Decimal("283.50"),
        gross_local=net_eur + Decimal("283.50"),
        net_eur=net_eur,
        vat_eur=Decimal("283.50"),
        net_eur_eff=net_eur_eff,
    )


async def _org_with_breach(db_session, *, name: str = "Claim-back Org", seed: int = 822):
    org = await make_org(db_session, name=name)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id, country="LV", seed=seed)
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        expected_discount_eur_l=Decimal("0.20"),
    )
    await _seed_breach(db_session, org.id, entity.id)
    return org, entity


async def _opened(db_session, org_id: str) -> VatOverchargeClaim:
    return await overcharge.open_claim(db_session, org_id, supplier=SUPPLIER, period=PERIOD)


# --------------------------------------------------------------------------- #
# Opening
# --------------------------------------------------------------------------- #


async def test_wo82_open_claim_snapshots_the_detected_euro(db_session):
    org, _ = await _org_with_breach(db_session)
    claim = await _opened(db_session, org.id)

    assert claim.status == "detected" == overcharge.INITIAL_STATE
    assert claim.detected_eur == Decimal("150.00")
    assert claim.lines_count == 1
    assert claim.recovered_eur is None


async def test_wo82_open_claim_is_idempotent_on_the_natural_key(db_session):
    """A second call returns the SAME row unchanged — it never re-snapshots a
    figure an operator has already quoted to a supplier."""
    org, entity = await _org_with_breach(db_session)
    first = await _opened(db_session, org.id)
    # A later ingest doubles the exposure...
    await _seed_breach(db_session, org.id, entity.id, line_seq=2)

    second = await _opened(db_session, org.id)

    assert second.id == first.id
    assert second.detected_eur == Decimal("150.00")  # ...and the snapshot holds
    assert second.lines_count == 1
    # The LIVE figure moved, which is exactly the information the frozen one hides.
    live = await contract_audit.audit(db_session, org.id, period=PERIOD, supplier=SUPPLIER)
    assert live.recover_eur == Decimal("300.00")


async def test_wo82_open_claim_refuses_when_detection_found_nothing(db_session):
    """No breach -> 422 `no_overcharge_detected`, and NO row is created: the
    worklist can never carry a €0 demand."""
    org = await make_org(db_session, name="No Breach Org")
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id, country="LV", seed=8231)
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        expected_discount_eur_l=Decimal("0.20"),
    )
    # applied is exactly 0.20 — the boundary case, honoured.
    await _seed_breach(db_session, org.id, entity.id, net_eur_eff=Decimal("1150.00"))

    with pytest.raises(AppError) as exc:
        await _opened(db_session, org.id)
    assert exc.value.code == "no_overcharge_detected"
    assert (
        await db_session.scalar(
            select(VatOverchargeClaim).where(VatOverchargeClaim.org_id == org.id)
        )
        is None
    )


async def test_wo82_opening_audits_the_frozen_figure(db_session):
    org, _ = await _org_with_breach(db_session)
    claim = await _opened(db_session, org.id)
    await db_session.flush()

    event = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.org_id == org.id, AuditEvent.action == "transport.overcharge_open"
        )
    )
    assert event is not None and event.target_id == claim.id
    assert '"old_status":null' in (event.meta or "")
    assert '"new_status":"detected"' in (event.meta or "")
    assert '"detected_eur":"150.00"' in (event.meta or "")


# --------------------------------------------------------------------------- #
# The harvested edges — one test per legal move
# --------------------------------------------------------------------------- #


async def test_wo82_the_edge_set_is_exactly_the_harvested_chain(db_session):
    assert overcharge.TRANSITIONS == {
        "detected": ("packaged",),
        "packaged": ("claimed",),
        "claimed": ("recovered", "rejected", "written_off"),
        "recovered": (),
        "rejected": (),
        "written_off": (),
    }


async def test_wo82_detected_advances_to_packaged(db_session):
    org, _ = await _org_with_breach(db_session)
    claim = await _opened(db_session, org.id)
    moved = await overcharge.advance_claim(db_session, org.id, claim.id, to_status="packaged")
    assert moved.status == "packaged"


async def test_wo82_packaged_advances_to_claimed(db_session):
    org, _ = await _org_with_breach(db_session)
    claim = await _opened(db_session, org.id)
    await overcharge.advance_claim(db_session, org.id, claim.id, to_status="packaged")
    moved = await overcharge.advance_claim(db_session, org.id, claim.id, to_status="claimed")
    assert moved.status == "claimed"


@pytest.mark.parametrize("outcome", ["rejected", "written_off"])
async def test_wo82_a_claimed_claimback_reaches_each_non_cash_outcome(db_session, outcome):
    org, _ = await _org_with_breach(db_session)
    claim = await _opened(db_session, org.id)
    await overcharge.advance_claim(db_session, org.id, claim.id, to_status="packaged")
    await overcharge.advance_claim(db_session, org.id, claim.id, to_status="claimed")

    moved = await overcharge.advance_claim(
        db_session, org.id, claim.id, to_status=outcome, note="Supplier disputes the term"
    )

    assert moved.status == outcome
    assert moved.recovered_eur is None
    assert moved.note == "Supplier disputes the term"


async def test_wo82_a_claimed_claimback_books_cash_on_recovered(db_session):
    org, _ = await _org_with_breach(db_session)
    claim = await _opened(db_session, org.id)
    await overcharge.advance_claim(db_session, org.id, claim.id, to_status="packaged")
    await overcharge.advance_claim(db_session, org.id, claim.id, to_status="claimed")

    moved = await overcharge.advance_claim(
        db_session,
        org.id,
        claim.id,
        to_status="recovered",
        recovered_eur=Decimal("150.00"),
        note="Credit note CN-2026-0007",
    )

    assert moved.status == "recovered"
    assert moved.recovered_eur == Decimal("150.00")


# --------------------------------------------------------------------------- #
# Refusals — nothing mutated
# --------------------------------------------------------------------------- #


async def _assert_unchanged(db_session, claim, before) -> None:
    await db_session.refresh(claim)
    columns = [c.key for c in VatOverchargeClaim.__mapper__.column_attrs]
    after = {c: str(getattr(claim, c)) for c in columns if c not in ("created_at", "updated_at")}
    assert after == before


def _snapshot(claim) -> dict[str, str]:
    columns = [c.key for c in VatOverchargeClaim.__mapper__.column_attrs]
    return {c: str(getattr(claim, c)) for c in columns if c not in ("created_at", "updated_at")}


async def test_wo82_an_illegal_transition_is_refused_with_nothing_mutated(db_session):
    """`detected -> claimed` skips the harvested `packaged` step: 409
    `overcharge_transition_invalid`, and every column reads back unchanged."""
    org, _ = await _org_with_breach(db_session)
    claim = await _opened(db_session, org.id)
    before = _snapshot(claim)

    with pytest.raises(AppError) as exc:
        await overcharge.advance_claim(db_session, org.id, claim.id, to_status="claimed")
    assert exc.value.code == "overcharge_transition_invalid"
    assert exc.value.status == 409

    await _assert_unchanged(db_session, claim, before)


@pytest.mark.parametrize("terminal", ["recovered", "rejected", "written_off"])
async def test_wo82_a_terminal_state_accepts_no_further_transition(db_session, terminal):
    org, _ = await _org_with_breach(db_session)
    claim = await _opened(db_session, org.id)
    await overcharge.advance_claim(db_session, org.id, claim.id, to_status="packaged")
    await overcharge.advance_claim(db_session, org.id, claim.id, to_status="claimed")
    kwargs = {"recovered_eur": Decimal("150.00")} if terminal == "recovered" else {}
    await overcharge.advance_claim(db_session, org.id, claim.id, to_status=terminal, **kwargs)
    before = _snapshot(claim)

    with pytest.raises(AppError) as exc:
        await overcharge.advance_claim(db_session, org.id, claim.id, to_status="packaged")
    assert exc.value.code == "overcharge_transition_invalid"

    await _assert_unchanged(db_session, claim, before)


async def test_wo82_an_unknown_target_state_is_refused_422(db_session):
    org, _ = await _org_with_breach(db_session)
    claim = await _opened(db_session, org.id)
    before = _snapshot(claim)

    with pytest.raises(AppError) as exc:
        await overcharge.advance_claim(db_session, org.id, claim.id, to_status="settled")
    assert exc.value.code == "invalid_overcharge_status"

    await _assert_unchanged(db_session, claim, before)


async def test_wo82_recovering_without_an_amount_is_refused(db_session):
    org, _ = await _org_with_breach(db_session)
    claim = await _opened(db_session, org.id)
    await overcharge.advance_claim(db_session, org.id, claim.id, to_status="packaged")
    await overcharge.advance_claim(db_session, org.id, claim.id, to_status="claimed")
    before = _snapshot(claim)

    with pytest.raises(AppError) as exc:
        await overcharge.advance_claim(db_session, org.id, claim.id, to_status="recovered")
    assert exc.value.code == "recovered_amount_required"

    await _assert_unchanged(db_session, claim, before)


async def test_wo82_recovering_more_than_detected_is_refused(db_session):
    """The no-over-crediting invariant (§4 item 13) applied to this ledger: a
    north-star total unbounded by its own evidence is not a measurement.
    €150.00 is accepted; €150.01 is not."""
    org, _ = await _org_with_breach(db_session)
    claim = await _opened(db_session, org.id)
    await overcharge.advance_claim(db_session, org.id, claim.id, to_status="packaged")
    await overcharge.advance_claim(db_session, org.id, claim.id, to_status="claimed")
    before = _snapshot(claim)

    with pytest.raises(AppError) as exc:
        await overcharge.advance_claim(
            db_session, org.id, claim.id, to_status="recovered", recovered_eur=Decimal("150.01")
        )
    assert exc.value.code == "recovered_amount_invalid"
    await _assert_unchanged(db_session, claim, before)

    # Exactly the detected euro IS accepted — the other side of the boundary.
    moved = await overcharge.advance_claim(
        db_session, org.id, claim.id, to_status="recovered", recovered_eur=Decimal("150.00")
    )
    assert moved.recovered_eur == Decimal("150.00")


async def test_wo82_a_non_cash_outcome_refuses_an_amount(db_session):
    org, _ = await _org_with_breach(db_session)
    claim = await _opened(db_session, org.id)
    await overcharge.advance_claim(db_session, org.id, claim.id, to_status="packaged")
    await overcharge.advance_claim(db_session, org.id, claim.id, to_status="claimed")
    before = _snapshot(claim)

    with pytest.raises(AppError) as exc:
        await overcharge.advance_claim(
            db_session, org.id, claim.id, to_status="rejected", recovered_eur=Decimal("50.00")
        )
    assert exc.value.code == "recovered_amount_not_applicable"
    await _assert_unchanged(db_session, claim, before)


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


async def test_wo82_every_transition_writes_an_audit_row_old_to_new(db_session):
    org, _ = await _org_with_breach(db_session)
    claim = await _opened(db_session, org.id)
    await overcharge.advance_claim(db_session, org.id, claim.id, to_status="packaged")
    await overcharge.advance_claim(db_session, org.id, claim.id, to_status="claimed")
    await overcharge.advance_claim(
        db_session, org.id, claim.id, to_status="recovered", recovered_eur=Decimal("120.00")
    )
    await db_session.flush()

    events = list(
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.org_id == org.id,
                AuditEvent.action == "transport.overcharge_transition",
            )
        )
    )
    assert len(events) == 3
    metas = [e.meta or "" for e in events]
    assert '"old_status":"detected"' in metas[0] and '"new_status":"packaged"' in metas[0]
    assert '"old_status":"packaged"' in metas[1] and '"new_status":"claimed"' in metas[1]
    assert '"old_status":"claimed"' in metas[2] and '"new_status":"recovered"' in metas[2]
    assert '"recovered_eur":"120.00"' in metas[2]


# --------------------------------------------------------------------------- #
# The north-star total
# --------------------------------------------------------------------------- #


async def test_wo82_recovered_total_counts_only_recovered_claims(db_session):
    """Three claim-backs, one per outcome. Only the recovered one is booked
    cash — §2.4's "booked-cash north star" is not the detected exposure."""
    org = await make_org(db_session, name="North Star Org")
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id, country="LV", seed=8241)
    for supplier in ("Q8", "BP", "DKV"):
        await contract_audit.set_term(
            db_session,
            org.id,
            supplier=supplier,
            country="LV",
            product_group="Diesel",
            expected_discount_eur_l=Decimal("0.20"),
        )
    for i, supplier in enumerate(("Q8", "BP", "DKV"), start=1):
        await _seed_breach(db_session, org.id, entity.id, line_seq=i, supplier=supplier)

    outcomes = {"Q8": "recovered", "BP": "rejected", "DKV": "written_off"}
    for supplier, outcome in outcomes.items():
        claim = await overcharge.open_claim(db_session, org.id, supplier=supplier, period=PERIOD)
        await overcharge.advance_claim(db_session, org.id, claim.id, to_status="packaged")
        await overcharge.advance_claim(db_session, org.id, claim.id, to_status="claimed")
        kwargs = {"recovered_eur": Decimal("90.00")} if outcome == "recovered" else {}
        await overcharge.advance_claim(db_session, org.id, claim.id, to_status=outcome, **kwargs)

    assert await overcharge.recovered_total(db_session, org.id) == Decimal("90.00")
    assert await overcharge.recovered_total(db_session, org.id, year=2026) == Decimal("90.00")
    # A different refund year sees none of it.
    assert await overcharge.recovered_total(db_session, org.id, year=2025) == Decimal("0.00")


async def test_wo82_recovered_total_is_zero_not_null_when_nothing_came_back(db_session):
    org, _ = await _org_with_breach(db_session)
    await _opened(db_session, org.id)
    assert await overcharge.recovered_total(db_session, org.id) == Decimal("0.00")


async def test_wo82_the_lifecycle_is_org_scoped_with_overlapping_data(db_session):
    """Two orgs with identical supplier codes, periods and amounts. Neither can
    see, list or advance the other's claim-back; a cross-tenant id is an opaque
    404 (§4.4)."""
    org_a, _ = await _org_with_breach(db_session, name="Claim-back Org A", seed=8251)
    org_b, _ = await _org_with_breach(db_session, name="Claim-back Org B", seed=8252)
    claim_a = await _opened(db_session, org_a.id)
    claim_b = await _opened(db_session, org_b.id)

    assert [c.id for c in await overcharge.list_claims(db_session, org_a.id)] == [claim_a.id]
    assert [c.id for c in await overcharge.list_claims(db_session, org_b.id)] == [claim_b.id]

    with pytest.raises(AppError) as exc:
        await overcharge.get_claim(db_session, org_a.id, claim_b.id)
    assert exc.value.code == "overcharge_claim_not_found"
    assert exc.value.status == 404

    with pytest.raises(AppError):
        await overcharge.advance_claim(db_session, org_a.id, claim_b.id, to_status="packaged")
    await db_session.refresh(claim_b)
    assert claim_b.status == "detected"


async def test_wo82_the_lifecycle_is_gated_on_the_transport_entitlement(db_session):
    org = await make_org(db_session, name="Unentitled Claim-back Org")
    with pytest.raises(AppError) as exc:
        await overcharge.list_claims(db_session, org.id)
    assert exc.value.code == "module_not_enabled"


async def test_wo82_listing_filters_on_the_harvested_state_vocabulary(db_session):
    org, _ = await _org_with_breach(db_session)
    claim = await _opened(db_session, org.id)
    assert [c.id for c in await overcharge.list_claims(db_session, org.id, status="detected")] == [
        claim.id
    ]
    assert await overcharge.list_claims(db_session, org.id, status="recovered") == []
    with pytest.raises(AppError) as exc:
        await overcharge.list_claims(db_session, org.id, status="settled")
    assert exc.value.code == "invalid_overcharge_status"
