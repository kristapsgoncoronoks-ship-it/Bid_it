"""WO-91 — the diesel-excise RATE registry (G4.6, R42).

`BA_fleet_fuel.md` §2.4 fixes the default and the fact that it is a placeholder:
*"rate default **EUR 30/1,000 L is an explicit PLACEHOLDER** in the reported
EUR 25-33 band, admin-overridable"*, over *"7 countries (BE·FR·IT·SI·HU·ES·HR)"*.
R42 makes the override a requirement.

WHAT THIS FILE IS DELIBERATELY STRICT ABOUT
---------------------------------------------
* **the placeholder is pinned to the spec's own number and band** — a silent
  drift of the default would change every figure this surface produces;
* **the seven-country set is the spec's, exactly** — an eighth state would be an
  invented legal claim, so `set_rate` refuses it on BOTH sides of the boundary;
* **a zero is not a rate** — `0` refused, `0.0001` accepted, and the refusal
  says why (a state that refunds nothing is expressed by holding no rate);
* **`None` is not zero** — a country outside the seven resolves to `None`;
* **the CRUD is audited old->new**, because a rate multiplies real litres into a
  figure a haulier files with a customs authority.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import PermissionError, ValidationError
from app.models.audit import AuditEvent
from app.models.transport.excise_rate import VatExciseRate
from app.services.transport import excise, queries
from tests.transport.conftest import enable_transport, make_org


async def _org(db_session, *, name: str = "WO91 Rates Org"):
    org = await make_org(db_session, name=name)
    await enable_transport(db_session, org.id)
    await db_session.commit()
    return org.id


# --------------------------------------------------------------------------- #
# The harvested constants
# --------------------------------------------------------------------------- #


def test_wo91_the_seven_refund_states_are_the_harvested_set():
    """`BA_fleet_fuel.md` §2.4: *"7 countries (BE·FR·IT·SI·HU·ES·HR)"*; Appendix
    B: *"Countries: BE, FR, IT, SI, HU, ES, HR"*. Pinned as a SET so the
    ordering choice (sorted, for stable surfaces) cannot hide a membership
    change."""
    assert set(excise.REFUND_COUNTRIES) == {"BE", "FR", "IT", "SI", "HU", "ES", "HR"}
    assert len(excise.REFUND_COUNTRIES) == 7
    assert list(excise.REFUND_COUNTRIES) == sorted(excise.REFUND_COUNTRIES)


def test_wo91_the_default_rate_is_the_harvested_placeholder_inside_its_reported_band():
    """Appendix B: *"Default rate EUR 30.00 / 1,000 L (PLACEHOLDER in the
    reported EUR 25-33 band)"*. Both halves are pinned — the number, and the
    fact that it sits inside the band the spec reports it as a placeholder
    for."""
    assert excise.DEFAULT_RATE_EUR_PER_1000L == Decimal("30.0000")
    low, high = excise.REPORTED_RATE_BAND_EUR
    assert (low, high) == (Decimal("25.00"), Decimal("33.00"))
    assert low <= excise.DEFAULT_RATE_EUR_PER_1000L <= high


def test_wo91_the_mechanism_denominator_is_one_thousand_litres():
    """§2.4 / §1.3 / R42 all state `litres × rate/1,000 L`."""
    assert excise.RATE_LITRES == Decimal("1000")


def test_wo91_the_rate_query_is_org_scoped():
    """Every builder in the canonical registry filters the tenant, and this one
    decides WHICH state's rate multiplies a haulier's litres."""
    compiled = str(queries.excise_rates("org-1"))
    assert "vat_excise_rates.org_id = :org_id_1" in compiled
    keyed = str(queries.excise_rates("org-1", country="FR"))
    assert "vat_excise_rates.country = :country_1" in keyed


# --------------------------------------------------------------------------- #
# Resolution — override, default, None
# --------------------------------------------------------------------------- #


async def test_wo91_an_unconfigured_refund_state_resolves_to_the_default(db_session):
    org_id = await _org(db_session)
    assert await excise.rate_for(db_session, org_id, "FR") == Decimal("30.0000")


async def test_wo91_a_state_outside_the_seven_resolves_to_none_not_zero(db_session):
    """The distinction the whole analysis rests on: `None` means *we hold no
    rate for this state*, and the report emits no row for it. A zero would read
    as *"this state refunds you nothing"* — a different, false claim."""
    org_id = await _org(db_session)
    assert await excise.rate_for(db_session, org_id, "LV") is None
    assert excise.default_rate_for("LV") is None
    assert excise.default_rate_for("FR") == Decimal("30.0000")


async def test_wo91_an_override_wins_over_the_default_and_removal_restores_it(db_session):
    org_id = await _org(db_session)
    await excise.set_rate(db_session, org_id, country="FR", rate_eur_per_1000l=Decimal("22.5000"))
    await db_session.commit()
    assert await excise.rate_for(db_session, org_id, "FR") == Decimal("22.5000")
    # Untouched states keep the placeholder.
    assert await excise.rate_for(db_session, org_id, "IT") == Decimal("30.0000")

    assert await excise.remove_rate(db_session, org_id, country="FR") is True
    await db_session.commit()
    assert await excise.rate_for(db_session, org_id, "FR") == Decimal("30.0000")
    # Removing an absent override is a no-op, never a 404.
    assert await excise.remove_rate(db_session, org_id, country="FR") is False


async def test_wo91_list_rates_resolves_every_refund_state(db_session):
    """The resolved view answers *"what rate will my figure use?"*, so it names
    all seven — an org that has typed nothing must still see the placeholder it
    is being computed at, not an empty list."""
    org_id = await _org(db_session)
    await excise.set_rate(db_session, org_id, country="HU", rate_eur_per_1000l=Decimal("27.1234"))
    await db_session.commit()

    rates = await excise.list_rates(db_session, org_id)
    assert set(rates) == set(excise.REFUND_COUNTRIES)
    assert rates["HU"] == Decimal("27.1234")
    assert rates["BE"] == Decimal("30.0000")

    rows = await excise.list_rate_rows(db_session, org_id)
    assert [(r.country, Decimal(r.rate_eur_per_1000l)) for r in rows] == [
        ("HU", Decimal("27.1234"))
    ]


# --------------------------------------------------------------------------- #
# The gates — both sides of every boundary
# --------------------------------------------------------------------------- #


async def test_wo91_set_rate_refuses_a_state_outside_the_seven(db_session):
    """Fail-CLOSED, and the refusal says WHY: storing a rate for a state the
    spec does not record as operating the regime would assert that one exists
    there (master-context §10)."""
    org_id = await _org(db_session)
    with pytest.raises(ValidationError) as exc:
        await excise.set_rate(
            db_session, org_id, country="LV", rate_eur_per_1000l=Decimal("30.0000")
        )
    assert exc.value.code == "excise_country_not_supported"
    assert "LV" in str(exc.value)
    assert await db_session.scalar(select(VatExciseRate).where(VatExciseRate.org_id == org_id)) is (
        None
    )


async def test_wo91_set_rate_refuses_a_non_positive_rate_on_both_sides(db_session):
    org_id = await _org(db_session)
    for bad in (Decimal("0"), Decimal("-1.0000")):
        with pytest.raises(ValidationError) as exc:
            await excise.set_rate(db_session, org_id, country="FR", rate_eur_per_1000l=bad)
        assert exc.value.code == "invalid_excise_rate"
    # The smallest storable positive rate is accepted — the boundary is `> 0`,
    # not an invented minimum.
    row = await excise.set_rate(
        db_session, org_id, country="FR", rate_eur_per_1000l=Decimal("0.0001")
    )
    await db_session.commit()
    assert Decimal(row.rate_eur_per_1000l) == Decimal("0.0001")


async def test_wo91_set_rate_refuses_a_malformed_country(db_session):
    org_id = await _org(db_session)
    with pytest.raises(ValidationError) as exc:
        await excise.set_rate(db_session, org_id, country="F", rate_eur_per_1000l=Decimal("30"))
    assert exc.value.code == "invalid_country"


async def test_wo91_the_rate_crud_is_module_gated(db_session):
    """ADR-P3 rule 3 — the service gates itself, fails CLOSED, and signals with
    an `AppError` rather than an `HTTPException`."""
    org = await make_org(db_session, name="WO91 Module Off")
    await db_session.commit()
    for call in (
        excise.list_rates(db_session, org.id),
        excise.list_rate_rows(db_session, org.id),
        excise.set_rate(db_session, org.id, country="FR", rate_eur_per_1000l=Decimal("30")),
        excise.remove_rate(db_session, org.id, country="FR"),
    ):
        with pytest.raises(PermissionError) as exc:
            await call
        assert exc.value.code == "module_not_enabled"


# --------------------------------------------------------------------------- #
# §4.16 — every rate change has an actor and an old->new
# --------------------------------------------------------------------------- #


async def _actions(db_session, org_id: str) -> list[AuditEvent]:
    return list(
        await db_session.scalars(
            select(AuditEvent).where(AuditEvent.org_id == org_id).order_by(AuditEvent.seq)
        )
    )


async def test_wo91_setting_and_removing_a_rate_audits_old_to_new(db_session):
    org_id = await _org(db_session)

    await excise.set_rate(db_session, org_id, country="ES", rate_eur_per_1000l=Decimal("28.0000"))
    await db_session.commit()
    await excise.set_rate(db_session, org_id, country="ES", rate_eur_per_1000l=Decimal("29.5000"))
    await db_session.commit()
    await excise.remove_rate(db_session, org_id, country="ES")
    await db_session.commit()

    events = [e for e in await _actions(db_session, org_id) if e.action.startswith("transport.")]
    assert [e.action for e in events] == [
        "transport.excise_rate_set",
        "transport.excise_rate_set",
        "transport.excise_rate_remove",
    ]
    # `AuditEvent.meta` is the hash-chained JSON TEXT column (the WO-82
    # convention) — parsed here rather than indexed as a mapping.
    metas = [json.loads(e.meta or "{}") for e in events]
    assert metas[0]["old"] is None
    assert metas[0]["new"] == "28.0000"
    assert metas[0]["replaced_default"] == "30.0000"
    assert metas[1]["old"] == "28.0000"
    assert metas[1]["new"] == "29.5000"
    assert metas[2]["old"] == "29.5000"
    assert metas[2]["new"] is None
    assert metas[2]["restored_default"] == "30.0000"


async def test_wo91_resetting_the_identical_rate_is_a_silent_no_op(db_session):
    """The `contract_audit.set_term` convention: an unchanged value audits
    nothing, so the trail records real changes rather than page refreshes."""
    org_id = await _org(db_session)
    await excise.set_rate(db_session, org_id, country="SI", rate_eur_per_1000l=Decimal("26.0000"))
    await db_session.commit()
    before = len(await _actions(db_session, org_id))

    await excise.set_rate(db_session, org_id, country="SI", rate_eur_per_1000l=Decimal("26.0000"))
    await db_session.commit()
    assert len(await _actions(db_session, org_id)) == before


# --------------------------------------------------------------------------- #
# §4.1 — org scoping over the service path
# --------------------------------------------------------------------------- #


async def test_wo91_one_orgs_rate_is_invisible_to_another(db_session):
    a = await _org(db_session, name="WO91 Org A")
    b = await _org(db_session, name="WO91 Org B")
    await excise.set_rate(db_session, a, country="FR", rate_eur_per_1000l=Decimal("22.5000"))
    await excise.set_rate(db_session, b, country="FR", rate_eur_per_1000l=Decimal("27.7500"))
    await db_session.commit()

    assert await excise.rate_for(db_session, a, "FR") == Decimal("22.5000")
    assert await excise.rate_for(db_session, b, "FR") == Decimal("27.7500")
    assert [r.country for r in await excise.list_rate_rows(db_session, a)] == ["FR"]
