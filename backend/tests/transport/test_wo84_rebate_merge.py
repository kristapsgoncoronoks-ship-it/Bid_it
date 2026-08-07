"""WO-84 — the OFF-INVOICE rebate merge into `net_eur_eff` and its source guard
(G4.2, R50; `BA_fleet_fuel.md` §4.2, §2.5, §5.1).

Every euro asserted here is hand-computed in `Decimal` from the harvested model,
never read back from the code under test:

    net_eur_eff = net_eur − (rebate_total × qty_line / qty_total)   [pro-rata by litres]
    eur_l_doc   = net_eur     / qty        (as invoiced — on-invoice discounts inside)
    eur_l_eff   = net_eur_eff / qty        (effective — after the off-invoice layer)
    applied     = eur_l_doc − eur_l_eff    (the rebate ACTUALLY applied, §4.2)

Basis on every figure: **NET EUR/L, final — VAT excluded, rebates applied**
(§3.G G1 / R49). Fixtures use round litres and exact cent amounts so the
expected values are exact decimals a reviewer can verify by hand.

There is no float in this file and none in the module it proves.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.off_invoice_rebate import VatOffInvoiceRebate
from app.services import fx
from app.services.transport import close, contract_audit, fuel_ingest, rebate
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org

pytestmark = pytest.mark.asyncio

PERIOD = "2026-05"
PRIOR = "2026-04"
SUPPLIER = "Q8"
REBATE_PARTY = "Demo Rebate Partner"
STATION = "Demo Station Riga"


async def _org(db_session, name: str = "Rebate Merge Org", seed: int = 841):
    org = await make_org(db_session, name=name)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id, country="LV", seed=seed)
    return org, entity


async def _txn(
    db_session,
    org_id: str,
    entity_id: str,
    *,
    line_seq: int,
    qty: Decimal,
    net_eur: Decimal,
    country: str = "LV",
    period: str = PERIOD,
    supplier: str = SUPPLIER,
    currency: str = "EUR",
) -> FuelTransaction:
    """One validated line, ingested through the REAL writer so `net_eur_eff`
    starts where production starts it: at the identity (`= net_eur`)."""
    vat_eur = (net_eur * Decimal("0.21")).quantize(Decimal("0.01"))
    return await fuel_ingest.ingest_transaction(
        db_session,
        org_id,
        entity_id=entity_id,
        supplier=supplier,
        period=period,
        line_seq=line_seq,
        country=country,
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=date(2026, 5, 12),
        station=STATION,
        product="DIESEL",
        qty=qty,
        currency=currency,
        net_local=net_eur,
        vat_local=vat_eur,
        gross_local=net_eur + vat_eur,
        net_eur=net_eur,
        vat_eur=vat_eur,
    )


async def _rebate(
    db_session,
    org_id: str,
    *,
    amount_local: Decimal,
    currency: str = "EUR",
    country: str = "LV",
    period: str = PERIOD,
    source_ref: str = "RBT-0001",
    source_party: str = REBATE_PARTY,
    rebate_date: date = date(2026, 6, 5),
    supplier: str = SUPPLIER,
) -> VatOffInvoiceRebate:
    return await rebate.record_rebate(
        db_session,
        org_id,
        supplier=supplier,
        country=country,
        period=period,
        source_ref=source_ref,
        source_party=source_party,
        rebate_date=rebate_date,
        currency=currency,
        amount_local=amount_local,
    )


# --------------------------------------------------------------------------- #
# §4.2's two tiers — on-invoice only, off-invoice only, and both together
# --------------------------------------------------------------------------- #


async def test_wo84_on_invoice_only_leaves_effective_equal_to_invoiced(db_session):
    """§4.2: an ON-invoice discount is *"already inside `net_eur`"*. With no
    off-invoice rebate recorded, the merge finds nothing and the effective net
    stays at the invoiced net — 1,000 L at €1,300.00 (a list €1.35/L already
    discounted to €1.30/L on the document): eur_l_doc == eur_l_eff == 1.3000,
    applied == 0."""
    org, entity = await _org(db_session)
    txn = await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1300.00"),
    )
    await db_session.commit()

    result = await rebate.merge_period(db_session, org.id, PERIOD)

    assert result.rebate_total_eur == Decimal("0.00")
    assert result.changed == 0
    await db_session.refresh(txn)
    assert Decimal(txn.net_eur_eff) == Decimal("1300.00")

    qty = Decimal("1000.000")
    assert Decimal(txn.net_eur) / qty == Decimal("1.3")
    assert Decimal(txn.net_eur_eff) / qty == Decimal("1.3")
    assert Decimal(txn.net_eur) / qty - Decimal(txn.net_eur_eff) / qty == Decimal("0")


async def test_wo84_off_invoice_only_merges_the_whole_rebate(db_session):
    """§4.2's canonical case: the supplier invoices at LIST price and the rebate
    partner issues a separate rebate invoice per country. 1,000 L at €1,350.00
    list, €50.00 recorded rebate:
        net_eur_eff = 1350.00 − 50.00 = 1300.00
        eur_l_doc = 1.3500, eur_l_eff = 1.3000, applied = 0.0500."""
    org, entity = await _org(db_session)
    txn = await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    await _rebate(db_session, org.id, amount_local=Decimal("50.00"))
    await db_session.commit()

    result = await rebate.merge_period(db_session, org.id, PERIOD)
    await db_session.commit()

    assert result.rebate_total_eur == Decimal("50.00")
    assert result.lines_allocated == 1
    assert result.changed == 1

    await db_session.refresh(txn)
    assert Decimal(txn.net_eur_eff) == Decimal("1300.00")
    assert Decimal(txn.net_eur) == Decimal("1350.00")  # the invoiced net NEVER moves

    qty = Decimal("1000.000")
    eur_l_doc = Decimal(txn.net_eur) / qty
    eur_l_eff = Decimal(txn.net_eur_eff) / qty
    assert eur_l_doc == Decimal("1.35")
    assert eur_l_eff == Decimal("1.3")
    assert eur_l_doc - eur_l_eff == Decimal("0.05")


async def test_wo84_both_tiers_together_compose_and_the_identity_still_holds(db_session):
    """Both tiers on one line. The document already carries an on-invoice
    discount (list €1.40/L invoiced at €1.35/L ⇒ net_eur 1,350.00 for 1,000 L),
    and a €50.00 off-invoice rebate lands on top.

    §4.2 is explicit that the on-invoice tier is INSIDE `net_eur`, so
    `applied = eur_l_doc − eur_l_eff` must equal the OFF-invoice layer ALONE
    (0.0500 €/L) — not the combined 0.1000 €/L. That is the whole point of
    keeping the two tiers in two columns."""
    org, entity = await _org(db_session)
    txn = await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    await _rebate(db_session, org.id, amount_local=Decimal("50.00"))
    await db_session.commit()

    await rebate.merge_period(db_session, org.id, PERIOD)
    await db_session.commit()
    await db_session.refresh(txn)

    qty = Decimal("1000.000")
    list_price = Decimal("1.40")
    eur_l_doc = Decimal(txn.net_eur) / qty
    eur_l_eff = Decimal(txn.net_eur_eff) / qty

    assert list_price - eur_l_doc == Decimal("0.05")  # the ON-invoice tier
    assert eur_l_doc - eur_l_eff == Decimal("0.05")  # the OFF-invoice tier ALONE
    assert list_price - eur_l_eff == Decimal("0.10")  # both, only when asked for both


# --------------------------------------------------------------------------- #
# The allocation: pro-rata by litres, summing EXACTLY to the recorded rebate
# --------------------------------------------------------------------------- #


async def test_wo84_allocation_is_pro_rata_by_litres_and_sums_to_the_rebate_exactly(db_session):
    """Three lines of unequal litres (500 / 300 / 200 = 1,000 L) sharing a
    €100.00 rebate ⇒ 50.00 / 30.00 / 20.00, and the applied €/L is the SAME
    0.1000 on every line (which is why the allocation is by litres: it is the
    shape a €/L contract term is compared against).

    The shares sum to exactly 100.00 — no cent lost, none invented."""
    org, entity = await _org(db_session)
    rows = [
        await _txn(
            db_session,
            org.id,
            entity.id,
            line_seq=1,
            qty=Decimal("500.000"),
            net_eur=Decimal("675.00"),
        ),
        await _txn(
            db_session,
            org.id,
            entity.id,
            line_seq=2,
            qty=Decimal("300.000"),
            net_eur=Decimal("405.00"),
        ),
        await _txn(
            db_session,
            org.id,
            entity.id,
            line_seq=3,
            qty=Decimal("200.000"),
            net_eur=Decimal("270.00"),
        ),
    ]
    await _rebate(db_session, org.id, amount_local=Decimal("100.00"))
    await db_session.commit()

    result = await rebate.merge_period(db_session, org.id, PERIOD)
    await db_session.commit()

    shares = {a.fuel_transaction_id: a.share_eur for a in result.allocations}
    assert sum(shares.values(), Decimal("0")) == Decimal("100.00")
    assert shares[rows[0].id] == Decimal("50.00")
    assert shares[rows[1].id] == Decimal("30.00")
    assert shares[rows[2].id] == Decimal("20.00")

    for row, qty, expected_eff in (
        (rows[0], Decimal("500.000"), Decimal("625.00")),
        (rows[1], Decimal("300.000"), Decimal("375.00")),
        (rows[2], Decimal("200.000"), Decimal("250.00")),
    ):
        await db_session.refresh(row)
        assert Decimal(row.net_eur_eff) == expected_eff
        applied = Decimal(row.net_eur) / qty - Decimal(row.net_eur_eff) / qty
        assert applied == Decimal("0.1")


async def test_wo84_a_rebate_that_does_not_divide_evenly_still_sums_to_the_recorded_euro(
    db_session,
):
    """Three equal 100 L lines sharing €10.00 would each take €3.333… — the
    cumulative-then-quantize walk gives 3.33 / 3.34 / 3.33, summing to EXACTLY
    10.00. Quantizing each share independently would have produced 9.99 and
    silently lost a cent from a price surface."""
    org, entity = await _org(db_session)
    rows = [
        await _txn(
            db_session,
            org.id,
            entity.id,
            line_seq=i,
            qty=Decimal("100.000"),
            net_eur=Decimal("135.00"),
        )
        for i in (1, 2, 3)
    ]
    await _rebate(db_session, org.id, amount_local=Decimal("10.00"))
    await db_session.commit()

    result = await rebate.merge_period(db_session, org.id, PERIOD)
    await db_session.commit()

    shares = [a.share_eur for a in sorted(result.allocations, key=lambda a: a.fuel_transaction_id)]
    assert sum(shares, Decimal("0")) == Decimal("10.00")

    total_eff = Decimal("0")
    for row in rows:
        await db_session.refresh(row)
        total_eff += Decimal(row.net_eur_eff)
    # 3 × 135.00 − 10.00
    assert total_eff == Decimal("395.00")


async def test_wo84_zero_litre_lines_receive_nothing(db_session):
    """§4.2 row 9: `qty` can be 0 (promo correction lines). A per-litre rebate
    has nothing to allocate to them, so they keep `net_eur_eff == net_eur` while
    the fuelling line absorbs the whole rebate."""
    org, entity = await _org(db_session)
    fuel_line = await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    promo = await _txn(
        db_session, org.id, entity.id, line_seq=2, qty=Decimal("0.000"), net_eur=Decimal("12.00")
    )
    await _rebate(db_session, org.id, amount_local=Decimal("50.00"))
    await db_session.commit()

    result = await rebate.merge_period(db_session, org.id, PERIOD)
    await db_session.commit()

    assert result.lines_allocated == 1
    await db_session.refresh(fuel_line)
    await db_session.refresh(promo)
    assert Decimal(fuel_line.net_eur_eff) == Decimal("1300.00")
    assert Decimal(promo.net_eur_eff) == Decimal(promo.net_eur) == Decimal("12.00")


# --------------------------------------------------------------------------- #
# Idempotency — the property that lets the close be re-run
# --------------------------------------------------------------------------- #


async def test_wo84_merge_is_idempotent_and_writes_nothing_on_a_re_run(db_session):
    """`merge_period` recomputes from the AS-INVOICED `net_eur`, never from the
    column's current value, so a second run finds every row already equal:
    `changed == 0`, the figure byte-identical, and NO second audit row."""
    org, entity = await _org(db_session)
    txn = await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    await _rebate(db_session, org.id, amount_local=Decimal("50.00"))
    await db_session.commit()

    first = await rebate.merge_period(db_session, org.id, PERIOD)
    await db_session.commit()
    assert first.changed == 1

    second = await rebate.merge_period(db_session, org.id, PERIOD)
    await db_session.commit()
    assert second.changed == 0
    assert second.rebate_total_eur == Decimal("50.00")

    await db_session.refresh(txn)
    assert Decimal(txn.net_eur_eff) == Decimal("1300.00")

    merges = list(
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.org_id == org.id,
                AuditEvent.action == "transport.rebate_merge",
            )
        )
    )
    assert len(merges) == 1, "a re-run must not audit an unchanged figure"


async def test_wo84_merge_audits_every_changed_row_old_to_new(db_session):
    """§4.16 — a changed money figure carries old→new, plus the source refs that
    justify it (the audit trail IS the traceability R50 asks for)."""
    org, entity = await _org(db_session)
    txn = await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    await _rebate(db_session, org.id, amount_local=Decimal("50.00"), source_ref="RBT-0007")
    await db_session.commit()

    await rebate.merge_period(db_session, org.id, PERIOD)
    await db_session.commit()

    event = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.org_id == org.id,
            AuditEvent.action == "transport.rebate_merge",
            AuditEvent.target_id == txn.id,
        )
    )
    assert event is not None
    meta = json.loads(event.meta) if isinstance(event.meta, str) else event.meta
    assert meta["old_net_eur_eff"] == "1350.00"
    assert meta["new_net_eur_eff"] == "1300.00"
    assert meta["rebate_share_eur"] == "50.00"
    assert meta["source_refs"] == ["RBT-0007"]


async def test_wo84_a_corrected_rebate_recomputes_from_the_invoiced_net(db_session):
    """A correction re-posts the SAME document number with a new amount. The
    next merge recomputes from `net_eur` (1,350.00 − 80.00), it does NOT
    compound the delta onto the already-adjusted 1,300.00."""
    org, entity = await _org(db_session)
    txn = await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    await _rebate(db_session, org.id, amount_local=Decimal("50.00"), source_ref="RBT-0001")
    await db_session.commit()
    await rebate.merge_period(db_session, org.id, PERIOD)
    await db_session.commit()

    await _rebate(db_session, org.id, amount_local=Decimal("80.00"), source_ref="RBT-0001")
    await db_session.commit()
    await rebate.merge_period(db_session, org.id, PERIOD)
    await db_session.commit()

    await db_session.refresh(txn)
    assert Decimal(txn.net_eur_eff) == Decimal("1270.00")


# --------------------------------------------------------------------------- #
# The SOURCE GUARD, half one: a rebate is never inferred (fail-CLOSED)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "source_ref,source_party",
    [("", REBATE_PARTY), ("   ", REBATE_PARTY), ("RBT-0001", ""), ("RBT-0001", "  ")],
)
async def test_wo84_unsourced_rebate_is_refused_with_nothing_mutated(
    db_session, source_ref, source_party
):
    """R50's *"guard the input source"*: a rebate with no identified document or
    no identified issuing party is refused BEFORE any query. Whitespace is
    refused exactly like emptiness — `"  "` is what an unchecked form post
    produces and it is exactly as unsourced.

    Nothing is mutated: no rebate row, and the transaction's effective net stays
    at the invoiced net."""
    org, entity = await _org(db_session)
    txn = await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await _rebate(
            db_session,
            org.id,
            amount_local=Decimal("50.00"),
            source_ref=source_ref,
            source_party=source_party,
        )
    assert exc.value.code == "rebate_source_required"
    await db_session.rollback()

    assert (await db_session.scalars(select(VatOffInvoiceRebate))).all() == []
    await db_session.refresh(txn)
    assert Decimal(txn.net_eur_eff) == Decimal("1350.00")


@pytest.mark.parametrize("bad", [Decimal("0.00"), Decimal("-10.00")])
async def test_wo84_a_non_positive_rebate_is_refused(db_session, bad):
    """A zero or negative rebate is not a rebate. Refused before FX, so a
    malformed amount never even reaches a rate lookup."""
    org, _entity = await _org(db_session)
    with pytest.raises(AppError) as exc:
        await _rebate(db_session, org.id, amount_local=bad)
    assert exc.value.code == "rebate_amount_invalid"
    assert (await db_session.scalars(select(VatOffInvoiceRebate))).all() == []


# --------------------------------------------------------------------------- #
# §4.15 — FX refuses rather than guesses
# --------------------------------------------------------------------------- #


async def test_wo84_non_eur_rebate_without_a_cached_rate_refuses_per_4_15(db_session):
    """A rebate in a currency with NO cached ECB rate is REFUSED, not converted
    at a guessed rate — `amount_eur` is NOT NULL, so "never a guessed number"
    means "no row at all". Nothing is written.

    ZAR is deliberately outside the test fixture's bundled ECB / fallback
    currency set (`app.services.fx.FALLBACK_RATES`) — the same currency the
    G3.2 ingestion suites use for exactly this proof, and the one guaranteed to
    have zero cached rate."""
    org, entity = await _org(db_session)
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await _rebate(db_session, org.id, amount_local=Decimal("215.00"), currency="ZAR")
    assert exc.value.code == "fx_rate_unavailable"
    await db_session.rollback()
    assert (await db_session.scalars(select(VatOffInvoiceRebate))).all() == []


async def test_wo84_non_eur_rebate_with_a_cached_rate_divides_and_records_ecb_provenance(
    db_session,
):
    """The allowed side of the same gate. ECB publishes units per 1 EUR, so
    converting to EUR DIVIDES: 215.00 PLN / 4.30 = 50.00 EUR exactly, stored
    with `fx_source="ecb"` and the official rate + as-of date frozen (§4.15,
    the platform's one convention)."""
    org, entity = await _org(db_session)
    txn = await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    await fx.load_rates(db_session, [(date(2026, 6, 5), "PLN", Decimal("4.30"))])
    row = await _rebate(db_session, org.id, amount_local=Decimal("215.00"), currency="PLN")
    await db_session.commit()

    assert row.amount_local == Decimal("215.00")
    assert row.amount_eur == Decimal("50.00")
    assert row.fx_source == "ecb"
    assert row.fx_rate == Decimal("4.300000")
    assert row.fx_ecb_rate == Decimal("4.300000")
    assert row.fx_ecb_date == date(2026, 6, 5)

    await rebate.merge_period(db_session, org.id, PERIOD)
    await db_session.commit()
    await db_session.refresh(txn)
    # The merge summed the EUR column only — §4.14, never a raw PLN figure.
    assert Decimal(txn.net_eur_eff) == Decimal("1300.00")


async def test_wo84_an_eur_rebate_records_the_identity_and_no_rate(db_session):
    org, _entity = await _org(db_session)
    row = await _rebate(db_session, org.id, amount_local=Decimal("50.00"))
    await db_session.commit()
    assert row.fx_source == "eur"
    assert row.fx_rate is None and row.fx_ecb_rate is None and row.fx_ecb_date is None
    assert row.amount_eur == row.amount_local == Decimal("50.00")


# --------------------------------------------------------------------------- #
# Fail-CLOSED merge refusals — nothing partially applied
# --------------------------------------------------------------------------- #


async def test_wo84_a_rebate_with_no_transactions_refuses(db_session):
    """Silently dropping the rebate would be exactly the disappearance R50
    exists to prevent, so the merge refuses instead."""
    org, _entity = await _org(db_session)
    await _rebate(db_session, org.id, amount_local=Decimal("50.00"))
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await rebate.merge_period(db_session, org.id, PERIOD)
    assert exc.value.code == "rebate_has_no_transactions"


async def test_wo84_a_rebate_with_no_litres_to_allocate_refuses(db_session):
    org, entity = await _org(db_session)
    await _txn(
        db_session, org.id, entity.id, line_seq=1, qty=Decimal("0.000"), net_eur=Decimal("12.00")
    )
    await _rebate(db_session, org.id, amount_local=Decimal("50.00"))
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await rebate.merge_period(db_session, org.id, PERIOD)
    assert exc.value.code == "rebate_no_litres_to_allocate"


async def test_wo84_a_rebate_exceeding_the_net_refuses_and_changes_nothing(db_session):
    """A rebate bigger than the invoiced net is a data error, not a discount: a
    non-positive effective net would corrupt every €/L derived from it. The
    two-phase resolve means the OTHER country's line is untouched too — nothing
    partially applied."""
    org, entity = await _org(db_session)
    bad = await _txn(
        db_session, org.id, entity.id, line_seq=1, qty=Decimal("100.000"), net_eur=Decimal("135.00")
    )
    other = await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=2,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
        country="EE",
    )
    await _rebate(db_session, org.id, amount_local=Decimal("200.00"), country="LV")
    await _rebate(
        db_session, org.id, amount_local=Decimal("50.00"), country="EE", source_ref="RBT-EE-1"
    )
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await rebate.merge_period(db_session, org.id, PERIOD)
    assert exc.value.code == "rebate_exceeds_net"
    await db_session.rollback()

    await db_session.refresh(bad)
    await db_session.refresh(other)
    assert Decimal(bad.net_eur_eff) == Decimal("135.00")
    assert Decimal(other.net_eur_eff) == Decimal("1350.00")


# --------------------------------------------------------------------------- #
# Tenancy
# --------------------------------------------------------------------------- #


async def test_wo84_merge_is_org_scoped(db_session):
    """Identical-looking data in two orgs: org A's merge must not move a single
    figure in org B (master-context §8 — overlapping fixtures, not distinct
    ones, or a broken filter passes by accident)."""
    org_a, entity_a = await _org(db_session, name="Rebate Org A", seed=8411)
    org_b, entity_b = await _org(db_session, name="Rebate Org B", seed=8412)

    txn_a = await _txn(
        db_session,
        org_a.id,
        entity_a.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    txn_b = await _txn(
        db_session,
        org_b.id,
        entity_b.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    await _rebate(db_session, org_a.id, amount_local=Decimal("50.00"))
    await db_session.commit()

    await rebate.merge_period(db_session, org_a.id, PERIOD)
    await db_session.commit()

    await db_session.refresh(txn_a)
    await db_session.refresh(txn_b)
    assert Decimal(txn_a.net_eur_eff) == Decimal("1300.00")
    assert Decimal(txn_b.net_eur_eff) == Decimal("1350.00")


# --------------------------------------------------------------------------- #
# The engine owns the write — the close is what merges
# --------------------------------------------------------------------------- #


async def test_wo84_the_close_runs_the_merge(db_session):
    """§3.H: `net_eur_eff` on an already-validated transaction is product data
    the ENGINE owns. Recording a rebate changes NO transaction; the close does,
    and reports what it did on its own return value and audit meta."""
    org, entity = await _org(db_session)
    txn = await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    await _rebate(db_session, org.id, amount_local=Decimal("50.00"))
    await db_session.commit()

    await db_session.refresh(txn)
    assert Decimal(txn.net_eur_eff) == Decimal("1350.00"), "recording must not touch a transaction"

    summary = await close.run_close(db_session, org.id, PERIOD)
    await db_session.commit()

    assert summary["rebate_merge"]["rebate_total_eur"] == "50.00"
    assert summary["rebate_merge"]["changed"] == 1
    await db_session.refresh(txn)
    assert Decimal(txn.net_eur_eff) == Decimal("1300.00")


async def test_wo84_a_second_close_changes_nothing(db_session):
    org, entity = await _org(db_session)
    txn = await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    await _rebate(db_session, org.id, amount_local=Decimal("50.00"))
    await db_session.commit()

    await close.run_close(db_session, org.id, PERIOD)
    await db_session.commit()
    summary = await close.run_close(db_session, org.id, PERIOD)
    await db_session.commit()

    assert summary["rebate_merge"]["changed"] == 0
    await db_session.refresh(txn)
    assert Decimal(txn.net_eur_eff) == Decimal("1300.00")


# --------------------------------------------------------------------------- #
# The SOURCE GUARD, half two: a rebate layer that DISAPPEARS (advisory)
# --------------------------------------------------------------------------- #


async def test_wo84_missing_source_warning_fires_when_a_known_rebate_layer_disappears(db_session):
    """§2.5's "Expected rebate", harvested: the expectation is learned from
    history. Q8/LV carried a rebate document in 2026-04 and has litres in
    2026-05 with no document recorded ⇒ warn LOUDLY (R50: *"it does not silently
    produce list-price analytics"*)."""
    org, entity = await _org(db_session)
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
        period=PRIOR,
    )
    await _rebate(db_session, org.id, amount_local=Decimal("50.00"), period=PRIOR)
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    await db_session.commit()

    warnings = await rebate.missing_source_warnings(db_session, org.id, period=PERIOD)
    assert len(warnings) == 1
    assert "Q8/LV" in warnings[0]
    assert PRIOR in warnings[0]
    assert "LIST" in warnings[0]


async def test_wo84_missing_source_warning_is_silent_for_a_supplier_that_never_had_one(db_session):
    """The falsifying half: with no history there is no expectation. Crying wolf
    on every ordinary supplier would train an operator to ignore the one warning
    that matters."""
    org, entity = await _org(db_session)
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
        supplier="TFC",
    )
    await db_session.commit()

    assert await rebate.missing_source_warnings(db_session, org.id, period=PERIOD) == ()


async def test_wo84_missing_source_warning_is_silent_once_the_document_is_recorded(db_session):
    org, entity = await _org(db_session)
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
        period=PRIOR,
    )
    await _rebate(db_session, org.id, amount_local=Decimal("50.00"), period=PRIOR)
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    await _rebate(db_session, org.id, amount_local=Decimal("50.00"), source_ref="RBT-0002")
    await db_session.commit()

    assert await rebate.missing_source_warnings(db_session, org.id, period=PERIOD) == ()


async def test_wo84_missing_source_warning_is_silent_without_activity_this_period(db_session):
    """No litres in the period ⇒ nothing is being priced at list, so there is
    nothing to warn about."""
    org, entity = await _org(db_session)
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
        period=PRIOR,
    )
    await _rebate(db_session, org.id, amount_local=Decimal("50.00"), period=PRIOR)
    await db_session.commit()

    assert await rebate.missing_source_warnings(db_session, org.id, period=PERIOD) == ()


# --------------------------------------------------------------------------- #
# CONSUMER IMPACT — contract_audit's findings against post-merge prices
# --------------------------------------------------------------------------- #


async def test_wo84_contract_audit_findings_are_correct_against_post_merge_prices(db_session):
    """The defect this order closes, proven end to end on one fixture.

    A supplier invoices 1,000 L at LIST €1.35/L and pays its agreed €0.05/L
    rebate off-invoice (€50.00). The contracted term is
    `expected_discount_eur_l = 0.05`.

    BEFORE the merge: `applied = 1.3500 − 1.3500 = 0` < 0.05 − 0.005 ⇒ a `short
    discount` finding worth 0.05 × 1000 = €50.00 — a demand for money the
    supplier has already paid.
    AFTER the merge: `applied = 1.3500 − 1.3000 = 0.0500`, which is not below
    the tolerance band ⇒ NO finding.
    """
    org, entity = await _org(db_session)
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        expected_discount_eur_l=Decimal("0.05"),
    )
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    await db_session.commit()

    before = await contract_audit.audit(db_session, org.id, period=PERIOD)
    assert len(before.breaches) == 1
    assert before.breaches[0].flag == contract_audit.FLAG_SHORT_DISCOUNT
    assert before.recover_eur == Decimal("50.00")

    await _rebate(db_session, org.id, amount_local=Decimal("50.00"))
    await db_session.commit()
    await rebate.merge_period(db_session, org.id, PERIOD)
    await db_session.commit()

    after = await contract_audit.audit(db_session, org.id, period=PERIOD)
    assert after.breaches == ()
    assert after.recover_eur == Decimal("0.00")
    assert after.lines_audited == 1  # the line was audited, it simply passed


async def test_wo84_a_genuinely_short_rebate_is_still_detected_after_the_merge(db_session):
    """The merge must not silence a REAL breach. Agreed 0.20 €/L, only 0.05 €/L
    actually paid off-invoice ⇒ gap 0.1500, recover_eur = 0.15 × 1000 = 150.00
    — the same figure WO-82's own suite asserts, now reached through a recorded
    rebate document instead of a hand-set column."""
    org, entity = await _org(db_session)
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        expected_discount_eur_l=Decimal("0.20"),
    )
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    await _rebate(db_session, org.id, amount_local=Decimal("50.00"))
    await db_session.commit()
    await rebate.merge_period(db_session, org.id, PERIOD)
    await db_session.commit()

    result = await contract_audit.audit(db_session, org.id, period=PERIOD)
    assert len(result.breaches) == 1
    breach = result.breaches[0]
    assert breach.flag == contract_audit.FLAG_SHORT_DISCOUNT
    assert breach.eur_l_doc == Decimal("1.3500")
    assert breach.eur_l_eff == Decimal("1.3000")
    assert breach.actual_eur_l == Decimal("0.0500")
    assert breach.agreed_eur_l == Decimal("0.2000")
    assert breach.gap_eur_l == Decimal("0.1500")
    assert breach.recover_eur == Decimal("150.00")


async def test_wo84_contract_audit_carries_the_source_guard_warning(db_session):
    """R50 at the surface that would otherwise publish list-price analytics
    silently. The warning is ADVISORY: the euro and the findings are unchanged
    by its presence."""
    org, entity = await _org(db_session)
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        max_net_eur_l=Decimal("1.20"),
    )
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
        period=PRIOR,
    )
    await _rebate(db_session, org.id, amount_local=Decimal("50.00"), period=PRIOR)
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
    )
    await db_session.commit()

    result = await contract_audit.audit(db_session, org.id, period=PERIOD)
    assert len(result.source_warnings) == 1
    assert "Q8/LV" in result.source_warnings[0]
    # Advisory — the finding is still computed exactly as before.
    assert len(result.breaches) == 1
    assert result.breaches[0].flag == contract_audit.FLAG_OVER_CEILING
