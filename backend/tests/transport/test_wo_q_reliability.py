"""WO-Q — the supplier-reliability board: evidence, not a verdict.

The owner's §12 criteria, built to `docs/design/supplier-reliability-rating.md`.
What must hold:

- each criterion's band comes out of HAND-COMPUTED figures, one supplier placed
  in each band per criterion, and the overall rating is the WORST band — never
  a weighted score;
- a thin sample publishes NO band at all (`insufficient_history`), because a
  `clean` label nobody earned is the same false comfort as an unearned finding;
- an IGNORED claim-back still counts (design §4.1 — otherwise the rating could
  be managed by ignoring, the silent dead end §12 was decided to prevent);
- the framing travels verbatim, the rule travels beside the band, and the
  thresholds say whether they are the org's or the platform's;
- the R53-shaped constraints are STRUCTURAL: no claim vocabulary anywhere on
  the surface, no import path from a reliability figure into the claim-back
  euro, no write verb but the threshold one — each scan carrying a
  seeded-violation self-test, because a scan that cannot fail proves nothing.
  Those four are source scans that await nothing, so they live next door in
  `test_wo_q_reliability_structure.py`, clear of this module's asyncio mark.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.transport.contract_term import VatSupplierContractTerm
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.overcharge import VatOverchargeClaim
from app.services.transport import reliability
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org

pytestmark = pytest.mark.asyncio


def _months(n: int) -> list[str]:
    """The n most recent accounting months, oldest first — the same arithmetic
    the service uses, called through the service so a drift in one is a drift
    in both."""
    return reliability.window_months(date.today())[-n:]


def _txn(
    org_id: str,
    entity_id: str,
    *,
    supplier: str,
    period: str,
    seq: int,
    qty: str = "1000.000",
    net_eur: str = "1400.00",
    currency: str = "EUR",
    fx_source: str | None = "eur",
    fx_rate: Decimal | None = None,
    fx_ecb_rate: Decimal | None = None,
) -> FuelTransaction:
    return FuelTransaction(
        org_id=org_id,
        entity_id=entity_id,
        supplier=supplier,
        period=period,
        line_seq=seq,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=date(int(period[:4]), int(period[5:]), 15),
        station="Demo Station Riga",
        product="DIESEL",
        product_group="Diesel",
        qty=Decimal(qty),
        currency=currency,
        net_local=Decimal(net_eur),
        vat_local=Decimal("0.00"),
        gross_local=Decimal(net_eur),
        net_eur=Decimal(net_eur),
        vat_eur=Decimal("0.00"),
        net_eur_eff=Decimal(net_eur),
        fx_source=fx_source,
        fx_rate=fx_rate,
        fx_ecb_rate=fx_ecb_rate,
    )


def _by_supplier(rep: reliability.ReliabilityReport) -> dict:
    return {s.supplier: s for s in rep.suppliers}


def _crit(entry, key: str) -> reliability.Criterion:
    return next(c for c in entry.criteria if c.key == key)


# --------------------------------------------------------------------------- #
# The window and the thin-sample refusal
# --------------------------------------------------------------------------- #


async def test_window_is_twelve_months_ending_this_month():
    months = reliability.window_months(date(2026, 8, 27))
    assert len(months) == reliability.WINDOW_MONTHS
    assert months[-1] == "2026-08"
    assert months[0] == "2025-09"
    assert months == sorted(months)  # oldest first, no gaps


async def test_a_thin_sample_publishes_no_band_at_all(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    # Two months of activity — below MIN_ACTIVE_MONTHS.
    for i, period in enumerate(_months(2)):
        db_session.add(_txn(org.id, entity.id, supplier="THIN", period=period, seq=i))
    await db_session.commit()

    rep = await reliability.report(db_session, org.id)
    thin = _by_supplier(rep)["THIN"]
    assert thin.overall == reliability.BAND_INSUFFICIENT
    assert thin.active_months == 2
    assert thin.criteria == ()  # no band nobody earned


# --------------------------------------------------------------------------- #
# Criterion 1 — overcharges, incl. the ignored-still-counts rule
# --------------------------------------------------------------------------- #


async def test_overcharge_bands_are_hand_computable_and_ignored_cases_count(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    months = _months(4)

    # CLEAN: four months of spend, no claim-backs at all.
    # RECURRING: three claim-backs (the default case threshold).
    # FINDINGS: one claim-back, small euro against large spend.
    for i, period in enumerate(months):
        for supplier in ("CLEANCO", "RECURCO", "FINDCO"):
            db_session.add(
                _txn(org.id, entity.id, supplier=supplier, period=period, seq=i, net_eur="10000.00")
            )
    for i, period in enumerate(months[:3]):
        db_session.add(
            VatOverchargeClaim(
                org_id=org.id,
                supplier="RECURCO",
                period=period,
                status="ignored" if i == 0 else "detected",
                detected_eur=Decimal("10.00"),
                lines_count=1,
            )
        )
    db_session.add(
        VatOverchargeClaim(
            org_id=org.id,
            supplier="FINDCO",
            period=months[0],
            status="recovered",
            detected_eur=Decimal("10.00"),
            recovered_eur=Decimal("10.00"),
            lines_count=1,
        )
    )
    await db_session.commit()

    rep = await reliability.report(db_session, org.id)
    entries = _by_supplier(rep)

    clean = _crit(entries["CLEANCO"], reliability.CRITERION_OVERCHARGES)
    assert clean.band == reliability.BAND_CLEAN
    assert clean.figures["cases"] == 0
    assert clean.figures["detected_eur"] == Decimal("0.00")

    rec = _crit(entries["RECURCO"], reliability.CRITERION_OVERCHARGES)
    assert rec.band == reliability.BAND_RECURRING  # 3 cases == the threshold
    assert rec.figures["cases"] == 3
    assert rec.figures["detected_eur"] == Decimal("30.00")
    # The IGNORED case is present, counted, and named as its own outcome —
    # ignoring an overcharge does not remove it from the evidence.
    assert rec.figures["outcomes"]["ignored"] == 1
    assert rec.figures["outcomes"]["detected"] == 2

    fnd = _crit(entries["FINDCO"], reliability.CRITERION_OVERCHARGES)
    assert fnd.band == reliability.BAND_FINDINGS
    assert fnd.figures["cases"] == 1
    # €10 detected against €40,000 spend = €0.25 per €1,000 — under the €5 rule.
    assert fnd.figures["detected_eur_per_1000_spend"] == Decimal("0.25")
    # The rule that produced the band travels WITH it.
    assert "3 or more cases" in fnd.rule and "5.00 EUR" in fnd.rule


async def test_a_small_supplier_is_not_labelled_for_being_small(db_session):
    """The normalisation earning its keep: the same euro against a small spend
    IS a finding, and against a large spend is not."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    months = _months(3)
    for i, period in enumerate(months):
        db_session.add(
            _txn(org.id, entity.id, supplier="SMALLCO", period=period, seq=i, net_eur="100.00")
        )
    db_session.add(
        VatOverchargeClaim(
            org_id=org.id,
            supplier="SMALLCO",
            period=months[0],
            status="detected",
            detected_eur=Decimal("50.00"),
            lines_count=1,
        )
    )
    await db_session.commit()

    rep = await reliability.report(db_session, org.id)
    crit = _crit(_by_supplier(rep)["SMALLCO"], reliability.CRITERION_OVERCHARGES)
    # €50 against €300 spend = €166.67 per €1,000 — far past the €5 rule, on ONE case.
    assert crit.figures["detected_eur_per_1000_spend"] == Decimal("166.67")
    assert crit.band == reliability.BAND_RECURRING


# --------------------------------------------------------------------------- #
# Criterion 2 — exchange-rate treatment
# --------------------------------------------------------------------------- #


async def test_fx_markup_is_measured_in_basis_points_and_eur_lines_are_excluded(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    months = _months(3)

    # MARKUPCO: three PLN lines on the supplier's own stated rate, each 100 bps
    # over ECB (4.50 stated vs 4.4554... ECB). Exactly at neither bound — the
    # median lands at 100 bps, past the 50 bps rule.
    for i, period in enumerate(months):
        db_session.add(
            _txn(
                org.id,
                entity.id,
                supplier="MARKUPCO",
                period=period,
                seq=i,
                currency="PLN",
                fx_source="stated",
                fx_rate=Decimal("4.545000"),
                fx_ecb_rate=Decimal("4.500000"),
            )
        )
        # EURCO bills in euros: no rate is involved, so it must NOT be diluted
        # into a share or measured as a markup.
        db_session.add(
            _txn(org.id, entity.id, supplier="EURCO", period=period, seq=i, fx_source="eur")
        )
    await db_session.commit()

    rep = await reliability.report(db_session, org.id)
    entries = _by_supplier(rep)

    mk = _crit(entries["MARKUPCO"], reliability.CRITERION_FX)
    assert mk.figures["foreign_currency_lines"] == 3
    assert mk.figures["supplier_stated_rate_lines"] == 3
    assert mk.figures["measured_lines"] == 3
    # (4.545 - 4.500) / 4.500 = 1% = 100.00 bps, exactly.
    assert mk.figures["median_markup_bps"] == Decimal("100.00")
    assert mk.band == reliability.BAND_RECURRING

    eur = _crit(entries["EURCO"], reliability.CRITERION_FX)
    assert eur.figures["foreign_currency_lines"] == 0
    assert eur.figures["median_markup_bps"] is None  # never measured, never 0
    assert eur.band == reliability.BAND_CLEAN


async def test_a_euro_with_no_rate_provenance_cannot_be_STORED_at_all(db_session):
    """The finding the design named second turned out to be unrepresentable,
    and this test is why the criterion no longer counts it: WO-88's writer gate
    and WO-89's CHECK make a foreign-currency line asserting a euro with no
    rate provenance impossible to store. A criterion that can never fire would
    read as a clean bill on a question nobody asked."""
    from sqlalchemy.exc import IntegrityError

    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    db_session.add(
        _txn(
            org.id,
            entity.id,
            supplier="NOPROVCO",
            period=_months(1)[0],
            seq=1,
            currency="SEK",
            fx_source="unknown",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


# --------------------------------------------------------------------------- #
# Criterion 3 — lines nobody agreed to
# --------------------------------------------------------------------------- #


async def test_ungoverned_share_counts_both_untermed_lines_and_breaches(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    months = _months(3)

    # GOVERNEDCO has a ceiling term it respects; UNTERMEDCO has no term at all.
    db_session.add(
        VatSupplierContractTerm(
            org_id=org.id,
            supplier="GOVERNEDCO",
            country="LV",
            product_group="Diesel",
            max_net_eur_l=Decimal("2.0000"),
            active=True,
        )
    )
    for i, period in enumerate(months):
        # 1000 L at €1,400 = €1.40/L, comfortably under the €2.00 ceiling.
        db_session.add(_txn(org.id, entity.id, supplier="GOVERNEDCO", period=period, seq=i))
        db_session.add(_txn(org.id, entity.id, supplier="UNTERMEDCO", period=period, seq=i))
    await db_session.commit()

    rep = await reliability.report(db_session, org.id)
    entries = _by_supplier(rep)

    gov = _crit(entries["GOVERNEDCO"], reliability.CRITERION_UNGOVERNED)
    assert gov.figures["lines_without_agreed_terms"] == 0
    assert gov.figures["lines_breaching_a_term"] == 0
    assert gov.figures["finding_share_pct"] == Decimal("0.00")
    assert gov.band == reliability.BAND_CLEAN

    unt = _crit(entries["UNTERMEDCO"], reliability.CRITERION_UNGOVERNED)
    assert unt.figures["lines_without_agreed_terms"] == 3
    assert unt.figures["finding_share_pct"] == Decimal("100.00")
    assert unt.band == reliability.BAND_RECURRING


# --------------------------------------------------------------------------- #
# The overall rating, the framing, and the thresholds
# --------------------------------------------------------------------------- #


async def test_overall_is_the_worst_band_never_an_average(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    months = _months(3)
    # Clean on overcharges and on FX, RECURRING on ungoverned lines. An average
    # of three bands would read "clean-ish"; the worst-of rule reads recurring.
    for i, period in enumerate(months):
        db_session.add(_txn(org.id, entity.id, supplier="MIXEDCO", period=period, seq=i))
    await db_session.commit()

    rep = await reliability.report(db_session, org.id)
    entry = _by_supplier(rep)["MIXEDCO"]
    bands = {c.key: c.band for c in entry.criteria}
    assert bands[reliability.CRITERION_OVERCHARGES] == reliability.BAND_CLEAN
    assert bands[reliability.CRITERION_FX] == reliability.BAND_CLEAN
    assert bands[reliability.CRITERION_UNGOVERNED] == reliability.BAND_RECURRING
    assert entry.overall == reliability.BAND_RECURRING


async def test_thresholds_default_then_become_the_orgs_own_audited(auth_client, db_session):
    """Through the API, because `is_default` is what the screen renders to tell
    a reader whether a band came from their rule or the platform's."""
    from sqlalchemy import select

    from app.models.organization import Organization

    org_id = await db_session.scalar(select(Organization.id))
    await enable_transport(db_session, org_id)

    first = await auth_client.get("/api/v1/transport/reliability/thresholds")
    assert first.status_code == 200, first.text
    assert first.json()["is_default"] is True
    assert first.json()["overcharge_cases"] == reliability.DEFAULT_OVERCHARGE_CASES

    put = await auth_client.put(
        "/api/v1/transport/reliability/thresholds",
        json={
            "overcharge_cases": 5,
            "overcharge_eur_per_1000": "12.50",
            "fx_markup_bps": 75,
            "ungoverned_share_pct": "20.00",
        },
    )
    assert put.status_code == 200, put.text
    assert put.json()["is_default"] is False
    assert put.json()["overcharge_cases"] == 5

    # Audited old→new, defaults included: moving OFF the platform default is
    # itself a decision worth a record.
    events = (await auth_client.get("/api/v1/audit", params={"limit": 50})).json()
    items = events["items"] if isinstance(events, dict) else events
    rows = [e for e in items if e.get("action") == "transport.reliability_thresholds_set"]
    assert rows, items
    assert rows[0]["meta"]["before"]["was_default"] is True
    assert rows[0]["meta"]["after"]["overcharge_cases"] == 5


async def test_a_zero_case_threshold_is_refused_with_a_sentence(auth_client, db_session):
    from sqlalchemy import select

    from app.models.organization import Organization

    org_id = await db_session.scalar(select(Organization.id))
    await enable_transport(db_session, org_id)
    bad = await auth_client.put(
        "/api/v1/transport/reliability/thresholds",
        json={
            "overcharge_cases": 0,
            "overcharge_eur_per_1000": "5.00",
            "fx_markup_bps": 50,
            "ungoverned_share_pct": "10.00",
        },
    )
    assert bad.status_code == 422, bad.text


async def test_the_board_route_carries_the_framing_verbatim(auth_client, db_session):
    from sqlalchemy import select

    from app.models.organization import Organization

    org_id = await db_session.scalar(select(Organization.id))
    await enable_transport(db_session, org_id)
    r = await auth_client.get("/api/v1/transport/reliability")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["framing"] == reliability.FRAMING
    assert body["window_from"] and body["window_to"]
    assert body["thresholds"]["is_default"] is True
