"""WO-82 — supplier overcharge DETECTION (G4.5, R41; `BA_fleet_fuel.md` §2.5).

Every euro asserted here is hand-computed in `Decimal` from the harvested
formula, never read back from the code under test:

    eur_l_doc = net_eur     / qty          (as invoiced)
    eur_l_eff = net_eur_eff / qty          (effective — the R49 basis)
    applied   = eur_l_doc − eur_l_eff      (the rebate ACTUALLY applied)
    short discount  when  applied < expected − 0.005
    over ceiling    when  eur_l_eff > max  + 0.005
    recover_eur = gap × litres, dropped if ≤ 0

Basis on every figure: **NET EUR/L, final — VAT excluded, rebates applied**
(§3.G G1 / R49). Fixtures use round litres and exact cent amounts so the
expected values are exact decimals a reviewer can verify by hand.

There is no float in this file and none in the module it proves.
"""

from __future__ import annotations

import inspect
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.core.errors import AppError
from app.models.transport.fuel_transaction import FuelTransaction
from app.services.transport import contract_audit
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org

pytestmark = pytest.mark.asyncio

PERIOD = "2026-05"
SUPPLIER = "Q8"
STATION_A = "Demo Station Riga"
STATION_B = "Demo Station Ventspils"


async def _txn(
    db_session,
    org_id: str,
    entity_id: str,
    *,
    line_seq: int,
    qty: Decimal,
    net_eur: Decimal,
    net_eur_eff: Decimal,
    station: str = STATION_A,
    product: str = "DIESEL",
    country: str = "LV",
    currency: str = "EUR",
    supplier: str = SUPPLIER,
    period: str = PERIOD,
) -> FuelTransaction:
    """One validated line. `net_local`/`vat_local`/`gross_local` are seeded so
    the row is realistic, and the point of several tests below is that the audit
    NEVER reads them."""
    from app.services.transport import fuel_ingest

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
        station=station,
        product=product,
        qty=qty,
        currency=currency,
        net_local=net_eur,
        vat_local=vat_eur,
        gross_local=net_eur + vat_eur,
        net_eur=net_eur,
        vat_eur=vat_eur,
        net_eur_eff=net_eur_eff,
    )


async def _org(db_session):
    org = await make_org(db_session, name="Overcharge Detection Org")
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id, country="LV", seed=821)
    return org, entity


# --------------------------------------------------------------------------- #
# The two harvested flags, against hand-computed decimals
# --------------------------------------------------------------------------- #


async def test_wo82_short_discount_is_detected_against_a_hand_computed_decimal(db_session):
    """1,000 L invoiced at €1,350.00 with €1,300.00 effective:
    applied = 1.35 − 1.30 = 0.05 €/L against an agreed 0.20 €/L.
    gap = 0.1500; recover_eur = 0.15 × 1000 = 150.00."""
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
        net_eur_eff=Decimal("1300.00"),
    )

    result = await contract_audit.audit(db_session, org.id, period=PERIOD, supplier=SUPPLIER)

    assert len(result.breaches) == 1
    b = result.breaches[0]
    assert b.flag == contract_audit.FLAG_SHORT_DISCOUNT == "short discount"
    assert b.eur_l_doc == Decimal("1.3500")
    assert b.eur_l_eff == Decimal("1.3000")
    assert b.agreed_eur_l == Decimal("0.2000")
    assert b.actual_eur_l == Decimal("0.0500")
    assert b.gap_eur_l == Decimal("0.1500")
    assert b.recover_eur == Decimal("150.00")
    assert result.recover_eur == Decimal("150.00")
    assert result.currency == "EUR"
    assert result.lines_audited == 1
    assert result.lines_without_terms == 0


async def test_wo82_over_ceiling_is_detected_against_a_hand_computed_decimal(db_session):
    """2,000 L effective at €1.29/L against a contracted ceiling of €1.20/L:
    gap = 0.0900; recover_eur = 0.09 × 2000 = 180.00."""
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
        qty=Decimal("2000.000"),
        net_eur=Decimal("2580.00"),
        net_eur_eff=Decimal("2580.00"),
    )

    result = await contract_audit.audit(db_session, org.id, period=PERIOD)

    assert len(result.breaches) == 1
    b = result.breaches[0]
    assert b.flag == contract_audit.FLAG_OVER_CEILING == "over ceiling"
    assert b.agreed_eur_l == Decimal("1.2000")
    assert b.actual_eur_l == Decimal("1.2900")
    assert b.gap_eur_l == Decimal("0.0900")
    assert b.recover_eur == Decimal("180.00")
    assert result.recover_eur == Decimal("180.00")


# --------------------------------------------------------------------------- #
# The boundaries — both sides of every edge (work-order template rule 4)
# --------------------------------------------------------------------------- #


async def test_wo82_exact_boundary_applied_equals_expected_is_not_a_breach(db_session):
    """The EXACT-boundary case the order names: the actual price equals the
    agreed basis. `applied < expected − tol` is false, so no breach at all —
    not a zero-euro finding."""
    org, entity = await _org(db_session)
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        expected_discount_eur_l=Decimal("0.20"),
    )
    # 1,000 L: doc 1.35, eff 1.15 -> applied exactly 0.20.
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
        net_eur_eff=Decimal("1150.00"),
    )

    result = await contract_audit.audit(db_session, org.id, period=PERIOD)

    assert result.breaches == ()
    assert result.recover_eur == Decimal("0.00")
    assert result.lines_audited == 1  # audited, and it passed


async def test_wo82_exact_boundary_eff_equals_max_is_not_a_breach(db_session):
    """The ceiling half of the same edge: `eff_l > max + tol` is false when the
    effective price equals the ceiling exactly."""
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
        net_eur=Decimal("1200.00"),
        net_eur_eff=Decimal("1200.00"),
    )

    result = await contract_audit.audit(db_session, org.id, period=PERIOD)

    assert result.breaches == ()
    assert result.recover_eur == Decimal("0.00")


async def test_wo82_exactly_one_tolerance_short_is_allowed_and_one_cent_more_is_not(db_session):
    """The harvested TOLERANCE = 0.005 €/L, proven on BOTH sides one step apart.

    Line 1: applied 0.1950 = expected − 0.005 exactly -> `<` is false -> allowed.
    Line 2: applied 0.1949, one 0.0001 step further -> refused, gap 0.0051.
    """
    org, entity = await _org(db_session)
    assert contract_audit.TOLERANCE == Decimal("0.005")
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        expected_discount_eur_l=Decimal("0.20"),
    )
    # 10,000 L makes a 0.0001 €/L step land on a whole cent of net.
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("10000.000"),
        net_eur=Decimal("13500.00"),
        net_eur_eff=Decimal("11550.00"),  # applied = 0.1950
    )
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=2,
        qty=Decimal("10000.000"),
        net_eur=Decimal("13500.00"),
        net_eur_eff=Decimal("11551.00"),  # applied = 0.1949
    )

    result = await contract_audit.audit(db_session, org.id, period=PERIOD)

    assert len(result.breaches) == 1
    b = result.breaches[0]
    assert b.actual_eur_l == Decimal("0.1949")
    assert b.gap_eur_l == Decimal("0.0051")
    assert b.recover_eur == Decimal("51.00")


# --------------------------------------------------------------------------- #
# No terms means no finding — never a false positive
# --------------------------------------------------------------------------- #


async def test_wo82_no_agreed_terms_means_no_finding(db_session):
    """An unconfigured supplier reads as "we have not told the system what was
    agreed" — counted in `lines_without_terms`, never flagged."""
    org, entity = await _org(db_session)
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
        net_eur_eff=Decimal("1350.00"),
    )

    result = await contract_audit.audit(db_session, org.id, period=PERIOD)

    assert result.breaches == ()
    assert result.recover_eur == Decimal("0.00")
    assert result.lines_audited == 0
    assert result.lines_without_terms == 1


async def test_wo82_an_inactive_term_produces_no_finding(db_session):
    """A lapsed contract is deactivated, not deleted (so a past period can still
    be audited against the terms that applied to it) — and a deactivated term
    audits nothing."""
    org, entity = await _org(db_session)
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        expected_discount_eur_l=Decimal("0.20"),
        active=False,
    )
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
        net_eur_eff=Decimal("1350.00"),
    )

    result = await contract_audit.audit(db_session, org.id, period=PERIOD)

    assert result.breaches == ()
    assert result.lines_without_terms == 1


async def test_wo82_a_term_for_another_country_or_product_never_matches(db_session):
    """The term grain is (supplier, country, station pattern, product group).
    A DE term and an AdBlue term both leave a LV diesel line unaudited."""
    org, entity = await _org(db_session)
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="DE",
        product_group="Diesel",
        expected_discount_eur_l=Decimal("0.20"),
    )
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="AdBlue",
        expected_discount_eur_l=Decimal("0.20"),
    )
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
        net_eur_eff=Decimal("1350.00"),
    )

    result = await contract_audit.audit(db_session, org.id, period=PERIOD)

    assert result.breaches == ()
    assert result.lines_without_terms == 1


# --------------------------------------------------------------------------- #
# Term selection: exactly one term per line
# --------------------------------------------------------------------------- #


async def test_wo82_the_most_specific_station_term_wins_and_never_double_counts(db_session):
    """A wildcard term and a station-specific one both match the line. Exactly
    ONE is applied — the specific one — so `recover_eur` is 0.05 × 1000 = 50.00
    and NOT the 200.00 a double count would produce."""
    org, entity = await _org(db_session)
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        expected_discount_eur_l=Decimal("0.20"),  # fleet-wide
    )
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        station_like="Demo Station Riga",
        expected_discount_eur_l=Decimal("0.10"),  # this station only
    )
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
        net_eur_eff=Decimal("1300.00"),  # applied 0.05
    )

    result = await contract_audit.audit(db_session, org.id, period=PERIOD)

    assert len(result.breaches) == 1
    assert result.breaches[0].agreed_eur_l == Decimal("0.1000")
    assert result.recover_eur == Decimal("50.00")


async def test_wo82_a_station_pattern_matches_like_style_and_falls_back_to_the_wildcard(
    db_session,
):
    """`%` is a LIKE wildcard run. The Riga line takes the 0.10 pattern term;
    the Ventspils line falls back to the fleet-wide 0.20 term."""
    org, entity = await _org(db_session)
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        expected_discount_eur_l=Decimal("0.20"),
    )
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        station_like="%Riga",
        expected_discount_eur_l=Decimal("0.10"),
    )
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
        net_eur_eff=Decimal("1350.00"),  # applied 0.00
        station=STATION_A,
    )
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=2,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
        net_eur_eff=Decimal("1350.00"),
        station=STATION_B,
    )

    result = await contract_audit.audit(db_session, org.id, period=PERIOD)

    by_station = {b.station: b for b in result.breaches}
    assert by_station[STATION_A].agreed_eur_l == Decimal("0.1000")
    assert by_station[STATION_A].recover_eur == Decimal("100.00")
    assert by_station[STATION_B].agreed_eur_l == Decimal("0.2000")
    assert by_station[STATION_B].recover_eur == Decimal("200.00")
    assert result.recover_eur == Decimal("300.00")


# --------------------------------------------------------------------------- #
# Degenerate lines
# --------------------------------------------------------------------------- #


async def test_wo82_a_zero_quantity_line_is_skipped_not_divided_by(db_session):
    """§4.2: quantity "can be 0 (promo correction lines)". A €/L price is
    undefined for such a line, so it is skipped and counted — never divided by,
    and never reported as a pass or a breach."""
    org, entity = await _org(db_session)
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Promo adj",
        expected_discount_eur_l=Decimal("0.20"),
    )
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("0.000"),
        net_eur=Decimal("-25.00"),
        net_eur_eff=Decimal("-25.00"),
        product="PROMO CORRECTION",
    )

    result = await contract_audit.audit(db_session, org.id, period=PERIOD)

    assert result.breaches == ()
    assert result.lines_skipped_zero_qty == 1
    assert result.lines_audited == 0


async def test_wo82_recover_eur_is_dropped_when_not_positive(db_session):
    """ "dropped if ≤ 0" (verbatim). A line whose applied discount EXCEEDS the
    agreed one is not a breach — it is the supplier being generous."""
    org, entity = await _org(db_session)
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        expected_discount_eur_l=Decimal("0.10"),
    )
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
        net_eur_eff=Decimal("1100.00"),  # applied 0.25 > agreed 0.10
    )

    result = await contract_audit.audit(db_session, org.id, period=PERIOD)

    assert result.breaches == ()
    assert result.recover_eur == Decimal("0.00")


# --------------------------------------------------------------------------- #
# §4.14 — EUR only, never a cross-currency sum
# --------------------------------------------------------------------------- #


async def test_wo82_mixed_currency_lines_total_in_eur_and_never_sum_a_local_amount(db_session):
    """A supplier month spanning EUR and PLN. Both lines' EUR price columns are
    populated by ingestion (a line it cannot convert never exists), so the total
    is a genuine EUR sum — and the document currency rides each finding as
    provenance without ever entering a total (§4.14).

    PLN line: 1,000 L, net_eur 1,350.00, net_eur_eff 1,300.00 -> applied 0.05,
    gap 0.15, recover 150.00. EUR line: 500 L, 675.00 / 650.00 -> applied 0.05,
    gap 0.15, recover 75.00. Total 225.00 EUR, hand-computed.
    """
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
        net_eur_eff=Decimal("1300.00"),
        currency="PLN",
    )
    await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=2,
        qty=Decimal("500.000"),
        net_eur=Decimal("675.00"),
        net_eur_eff=Decimal("650.00"),
        currency="EUR",
    )

    result = await contract_audit.audit(db_session, org.id, period=PERIOD)

    assert result.currency == "EUR"
    assert {b.document_currency for b in result.breaches} == {"PLN", "EUR"}
    assert result.recover_eur == Decimal("225.00")
    # The PLN line's own euro is the EUR figure, not a re-labelled local amount.
    pln = next(b for b in result.breaches if b.document_currency == "PLN")
    assert pln.recover_eur == Decimal("150.00")


async def test_wo82_the_module_reads_no_document_currency_column():
    """Structural §4.14 proof: the three document-currency amount columns never
    appear in this module, so no path can sum one or mislabel it EUR."""
    src = inspect.getsource(contract_audit)
    for column in ("net_local", "vat_local", "gross_local"):
        assert column not in src, f"{column} is read in a EUR-only analysis (§4.14)"


async def test_wo82_no_float_anywhere_in_the_detection_module():
    src = inspect.getsource(contract_audit)
    assert "float" not in src, "float in a money path (§4.9)"


async def test_wo82_detection_holds_no_writable_intent():
    """R41's "read-only over the analytics", asserted structurally over the
    detection functions only — the TERM CRUD in the same module legitimately
    writes, and is audited."""
    for fn in (
        contract_audit.audit,
        contract_audit._term_for,
        contract_audit._breach_for,
        contract_audit._finding,
    ):
        src = inspect.getsource(fn)
        assert "db.add" not in src
        assert "db.delete" not in src
        assert "db.flush" not in src
        assert "audit_svc.record" not in src


async def test_wo82_detection_mutates_no_transaction(db_session):
    """Behavioural half of the same guarantee: every column of every fuel
    transaction is byte-identical after a run."""
    org, entity = await _org(db_session)
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        expected_discount_eur_l=Decimal("0.20"),
    )
    txn = await _txn(
        db_session,
        org.id,
        entity.id,
        line_seq=1,
        qty=Decimal("1000.000"),
        net_eur=Decimal("1350.00"),
        net_eur_eff=Decimal("1300.00"),
    )
    columns = [c.key for c in FuelTransaction.__mapper__.column_attrs]

    def snapshot():
        # tzinfo is dropped before comparing: SQLite hands the same stored
        # instant back tz-AWARE after a refresh and tz-NAIVE before one, which
        # is a representation artefact, not a write. Every VALUE — including
        # both timestamps — is still compared exactly, so a real mutation
        # (which would bump `updated_at`) still fails this test.
        def norm(v):
            return v.replace(tzinfo=None) if isinstance(v, datetime) else v

        return {c: norm(getattr(txn, c)) for c in columns}

    before = snapshot()

    await contract_audit.audit(db_session, org.id, period=PERIOD)

    await db_session.refresh(txn)
    assert snapshot() == before


# --------------------------------------------------------------------------- #
# Term CRUD, validation and org scoping
# --------------------------------------------------------------------------- #


async def test_wo82_setting_a_term_twice_with_the_same_values_audits_nothing(db_session):
    from sqlalchemy import func, select

    from app.models.audit import AuditEvent

    org, _ = await _org(db_session)
    kwargs = dict(
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        expected_discount_eur_l=Decimal("0.20"),
    )
    await contract_audit.set_term(db_session, org.id, **kwargs)
    await db_session.flush()
    first = await db_session.scalar(
        select(func.count(AuditEvent.id)).where(AuditEvent.org_id == org.id)
    )
    await contract_audit.set_term(db_session, org.id, **kwargs)
    await db_session.flush()
    second = await db_session.scalar(
        select(func.count(AuditEvent.id)).where(AuditEvent.org_id == org.id)
    )
    assert first == second


async def test_wo82_changing_a_term_audits_old_to_new(db_session):
    from sqlalchemy import select

    from app.models.audit import AuditEvent

    org, _ = await _org(db_session)
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        expected_discount_eur_l=Decimal("0.20"),
    )
    await contract_audit.set_term(
        db_session,
        org.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        expected_discount_eur_l=Decimal("0.25"),
    )
    await db_session.flush()
    events = list(
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.org_id == org.id,
                AuditEvent.action == "transport.contract_term_set",
            )
        )
    )
    assert len(events) == 2
    # `AuditEvent.meta` is the hash-chained JSON TEXT column, read as text here
    # exactly as the WO-73 lifecycle suite reads it.
    change = events[-1].meta or ""
    assert '"old":{"active":"True","expected_discount_eur_l":"0.2000"' in change
    assert '"new":{"active":"True","expected_discount_eur_l":"0.2500"' in change


async def test_wo82_a_term_asserting_no_figure_is_refused(db_session):
    org, _ = await _org(db_session)
    with pytest.raises(AppError) as exc:
        await contract_audit.set_term(
            db_session, org.id, supplier=SUPPLIER, country="LV", product_group="Diesel"
        )
    assert exc.value.code == "term_has_no_figure"


async def test_wo82_an_unknown_product_group_is_refused(db_session):
    org, _ = await _org(db_session)
    with pytest.raises(AppError) as exc:
        await contract_audit.set_term(
            db_session,
            org.id,
            supplier=SUPPLIER,
            country="LV",
            product_group="Kerosene",
            expected_discount_eur_l=Decimal("0.20"),
        )
    assert exc.value.code == "invalid_product_group"


async def test_wo82_a_malformed_period_is_refused_and_audits_nothing(db_session):
    org, _ = await _org(db_session)
    with pytest.raises(AppError) as exc:
        await contract_audit.audit(db_session, org.id, period="2026-13")
    assert exc.value.code == "invalid_period"


async def test_wo82_the_module_is_gated_on_the_transport_entitlement(db_session):
    org = await make_org(db_session, name="No Transport Org")
    with pytest.raises(AppError) as exc:
        await contract_audit.audit(db_session, org.id, period=PERIOD)
    assert exc.value.code == "module_not_enabled"


async def test_wo82_terms_and_findings_are_org_scoped(db_session):
    """Two orgs with IDENTICAL-LOOKING data: same supplier code, same period,
    same station, same amounts. Only A has a term configured, so only A has a
    finding — a broken org filter would surface B's line."""
    org_a = await make_org(db_session, name="Overcharge Org A")
    org_b = await make_org(db_session, name="Overcharge Org B")
    await enable_transport(db_session, org_a.id)
    await enable_transport(db_session, org_b.id)
    entity_a = await make_entity(db_session, org_a.id, country="LV", seed=8221)
    entity_b = await make_entity(db_session, org_b.id, country="LV", seed=8222)

    await contract_audit.set_term(
        db_session,
        org_a.id,
        supplier=SUPPLIER,
        country="LV",
        product_group="Diesel",
        expected_discount_eur_l=Decimal("0.20"),
    )
    for org, entity in ((org_a, entity_a), (org_b, entity_b)):
        await _txn(
            db_session,
            org.id,
            entity.id,
            line_seq=1,
            qty=Decimal("1000.000"),
            net_eur=Decimal("1350.00"),
            net_eur_eff=Decimal("1300.00"),
        )

    a = await contract_audit.audit(db_session, org_a.id, period=PERIOD)
    b = await contract_audit.audit(db_session, org_b.id, period=PERIOD)

    assert len(a.breaches) == 1
    assert a.breaches[0].entity_id == entity_a.id
    assert b.breaches == ()
    assert b.lines_without_terms == 1
    assert await contract_audit.list_terms(db_session, org_b.id) == []


async def test_wo82_the_result_carries_the_price_basis_and_the_legal_framing(db_session):
    """§3.G G1 ("stated on any new report surface") and R53 (the framing must
    not be flattened) — both ride the result so no surface can render the number
    without them."""
    org, _ = await _org(db_session)
    result = await contract_audit.audit(db_session, org.id, period=PERIOD)
    assert result.price_basis == "NET EUR/L, final — VAT excluded, rebates applied"
    assert result.legal_framing.startswith("Money the supplier owes")
