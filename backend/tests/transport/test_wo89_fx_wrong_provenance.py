"""WO-89 — a non-EUR fuel line may not claim the EUR identity provenance
(master-context §4.15/§4.14; `BA_fleet_fuel.md` R56).

WO-88 closed the two combinations in which a stored euro denies that a rate was
USED. This closes the one in which it denies that a rate was NEEDED: a non-EUR
document currency carrying `fx_source='eur'`, which `app/models/fx.py` defines
as *"the amount was already EUR (identity, rate 1)"*. Before this order the
production writer STORED that row — the recon probe drove
`fuel_ingest.ingest_transaction` and got back a PLN line with
`net_eur = 1400.00` and the provenance "no conversion was required".

Two layers are asserted here, and — as in WO-88 — the second is the point:

1. `fuel_ingest._require_fx_provenance` refuses it at the ONE service that
   writes `fuel_transactions`, fail CLOSED, writing nothing;
2. `ck_fuel_transactions_fx_provenance` / `ck_vat_off_invoice_rebates_fx_provenance`
   refuse the SAME combination in the database, for every path that never comes
   through the gate.

Three things this file also proves it did NOT break: WO-88's two refusals keep
their own `fx_rate_unavailable` code (one predicate, three cases, two codes),
every legal combination still lands, and `savings._require_eur_basis` is
untouched.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ValidationError
from app.models.audit import AuditEvent
from app.models.fx import FxSource
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.off_invoice_rebate import VatOffInvoiceRebate
from app.services import audit
from app.services.transport import fuel_ingest, rebate, savings
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org

# No module-level `pytest.mark.asyncio`: `pytest.ini` sets `asyncio_mode = auto`
# (the WO-87/WO-88 convention, kept).

PERIOD = "2026-05"
TXN_DATE = date(2026, 5, 14)

_SEQ = {"n": 0}


async def _org_entity(db_session, *, seed: int | None = None):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id, country="LV", seed=seed)
    await db_session.commit()
    return org.id, entity.id


def _kwargs(**overrides):
    """The argument tuple of one diesel line, EUR unless a test says otherwise."""
    _SEQ["n"] += 1
    net = Decimal("1400.00")
    base = dict(
        supplier="Q8",
        period=PERIOD,
        line_seq=_SEQ["n"],
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=TXN_DATE,
        station="Demo Station Riga",
        product="DIESEL",
        qty=Decimal("1000.000"),
        currency="EUR",
        net_local=net,
        vat_local=Decimal("294.00"),
        gross_local=Decimal("1694.00"),
        net_eur=net,
        vat_eur=Decimal("294.00"),
    )
    base.update(overrides)
    return base


def _detached(**overrides) -> FuelTransaction:
    """A `FuelTransaction` built in memory and NEVER added to a session — the
    only way an untrustworthy row still exists after WO-88/WO-89."""
    row = FuelTransaction(
        org_id="org", entity_id="entity", product_group="Diesel", **_kwargs(**overrides)
    )
    row.net_eur_eff = row.net_eur
    return row


async def _count(db_session, org_id: str) -> int:
    return (
        await db_session.scalar(
            select(func.count())
            .select_from(FuelTransaction)
            .where(FuelTransaction.org_id == org_id)
        )
    ) or 0


# --------------------------------------------------------------------------- #
# Layer 1 — the service gate refuses, and writes nothing
# --------------------------------------------------------------------------- #


def test_wo89_the_eur_literal_matches_the_platform_enum():
    """`fuel_ingest.FX_SOURCE_EUR` is a LITERAL, not an import (ADR-0023 rule 2
    — a transport service may only import `app.models.transport` /
    `app.models.base`, which is why `savings.py` and `rebate.py` state their own
    the same way). A test file has no such restriction, so the two are pinned
    together here: a drift would silently stop the gate recognising the one
    value that means "this amount needed no conversion at all"."""
    assert fuel_ingest.FX_SOURCE_EUR == FxSource.eur.value
    assert fuel_ingest.FX_SOURCE_UNKNOWN == FxSource.unknown.value


@pytest.mark.parametrize("currency", ["PLN", "SEK", "pln"])
async def test_wo89_ingest_refuses_a_non_eur_line_claiming_the_eur_identity(db_session, currency):
    """The defect this order exists to close, and it is not hypothetical: the
    recon probe drove exactly this call and got a stored row back. `eur` means
    *"the amount was already EUR (identity, rate 1)"*; a zloty amount was not.

    The lower-case variant proves the gate folds case the same way the CHECK's
    `upper(currency)` does — a caller cannot slip the row past by shouting less.
    """
    org_id, entity_id = await _org_entity(db_session)

    with pytest.raises(ValidationError) as exc:
        await fuel_ingest.ingest_transaction(
            db_session, org_id, entity_id=entity_id, **_kwargs(currency=currency, fx_source="eur")
        )

    assert exc.value.code == "fx_provenance_inconsistent"
    assert exc.value.status == 422
    assert await _count(db_session, org_id) == 0


async def test_wo89_a_refused_ingestion_writes_no_audit_event(db_session):
    """§4.16's mirror image: nothing happened, so nothing is recorded as having
    happened. The gate still runs before `db.add` and before the flush."""
    org_id, entity_id = await _org_entity(db_session)

    with pytest.raises(ValidationError):
        await fuel_ingest.ingest_transaction(
            db_session, org_id, entity_id=entity_id, **_kwargs(currency="PLN", fx_source="eur")
        )
    await db_session.rollback()

    events = (await db_session.scalars(select(AuditEvent).where(AuditEvent.org_id == org_id))).all()
    # The action string is read off the enum, not retyped — an assertion over a
    # name that does not exist would pass while proving nothing.
    assert [e for e in events if e.action == audit.A.FUEL_TRANSACTION_INGEST.value] == []


async def test_wo89_the_gate_still_runs_before_any_database_read(db_session):
    """Ordering is part of the contract, and WO-89 does not move it: the
    provenance check is pure, so an argument tuple that can never become a row
    is refused before the entity lookup — no query, no side effect, and the
    error names the real problem rather than the first thing that happens to
    fail."""
    org_id, _entity_id = await _org_entity(db_session)

    with pytest.raises(ValidationError) as exc:
        await fuel_ingest.ingest_transaction(
            db_session,
            org_id,
            entity_id="00000000-0000-0000-0000-000000000000",
            **_kwargs(currency="PLN", fx_source="eur"),
        )
    assert exc.value.code == "fx_provenance_inconsistent"  # not entity_not_found


async def test_wo89_the_module_entitlement_still_wins(db_session):
    """The entitlement gate stays FIRST: a caller without the transport module
    learns that, not the FX detail of a row it may not write at all."""
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id, country="LV")
    await db_session.commit()

    with pytest.raises(Exception) as exc:
        await fuel_ingest.ingest_transaction(
            db_session, org.id, entity_id=entity.id, **_kwargs(currency="PLN", fx_source="eur")
        )
    assert getattr(exc.value, "code", None) == "module_not_enabled"


def test_wo89_the_refusal_names_the_currency_and_not_the_wrong_remedy():
    """Why the code is distinct, asserted rather than argued in prose. Every
    `fx_rate_unavailable` message in this tree ends in an instruction to go and
    obtain the rate ("refresh the ECB rates", "resolve the rate and re-ingest").
    Here the rate is probably cached already and that instruction would send an
    operator to the wrong fix, so the message must name the currency and the
    real remedy instead."""
    with pytest.raises(ValidationError) as exc:
        fuel_ingest._require_fx_provenance("PLN", "eur")

    message = str(exc.value)
    assert "PLN" in message
    assert "ecb" in message and "stated" in message  # the two honest provenances
    assert "refresh" not in message.lower()
    assert "ECB rates" not in message


# --------------------------------------------------------------------------- #
# WO-88 is NOT weakened — one predicate, three cases, two codes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("currency", "fx_source"), [("EUR", "unknown"), ("PLN", "unknown"), ("PLN", None)]
)
async def test_wo89_wo88s_two_refusals_keep_their_own_code(db_session, currency, fx_source):
    """Extending a predicate is where codes silently drift. WO-88's two
    combinations still raise `fx_rate_unavailable` — for those, "go and get the
    rate" IS the remedy — and still write nothing."""
    org_id, entity_id = await _org_entity(db_session)

    with pytest.raises(ValidationError) as exc:
        await fuel_ingest.ingest_transaction(
            db_session,
            org_id,
            entity_id=entity_id,
            **_kwargs(currency=currency, fx_source=fx_source),
        )

    assert exc.value.code == "fx_rate_unavailable"
    assert await _count(db_session, org_id) == 0


def test_wo89_the_one_predicate_maps_each_case_to_exactly_one_code():
    """The whole rule on one line, so a future edit that blurs the two codes
    together fails here rather than in a support ticket."""
    seen = {}
    for currency, fx_source in (
        ("EUR", "unknown"),
        ("PLN", "unknown"),
        ("PLN", None),
        ("PLN", "eur"),
        ("SEK", "eur"),
    ):
        with pytest.raises(ValidationError) as exc:
            fuel_ingest._require_fx_provenance(currency, fx_source)
        seen[(currency, fx_source)] = exc.value.code

    assert seen == {
        ("EUR", "unknown"): "fx_rate_unavailable",
        ("PLN", "unknown"): "fx_rate_unavailable",
        ("PLN", None): "fx_rate_unavailable",
        ("PLN", "eur"): "fx_provenance_inconsistent",
        ("SEK", "eur"): "fx_provenance_inconsistent",
    }


# --------------------------------------------------------------------------- #
# The other side of the gate — every legal combination still lands
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("currency", "fx_source"),
    [
        ("EUR", "eur"),
        ("EUR", None),
        ("eur", "eur"),
        ("PLN", "ecb"),
        ("PLN", "stated"),
        ("SEK", "ecb"),
    ],
)
async def test_wo89_every_legal_combination_is_still_accepted(db_session, currency, fx_source):
    """Both sides of the boundary. EUR is the identity — in either case — a
    converted line carries `ecb`, a supplier-stated line carries `stated`, and a
    EUR line with no provenance at all stays legal because EUR involves no rate.
    A gate that refused any of these would have broken the production path."""
    org_id, entity_id = await _org_entity(db_session)

    row = await fuel_ingest.ingest_transaction(
        db_session, org_id, entity_id=entity_id, **_kwargs(currency=currency, fx_source=fx_source)
    )
    await db_session.commit()

    assert row.fx_source == fx_source
    assert row.currency == currency
    assert row.net_eur == Decimal("1400.00")
    assert await _count(db_session, org_id) == 1


# --------------------------------------------------------------------------- #
# Layer 2 — THE DATABASE ITSELF REFUSES (the load-bearing test of this order)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("currency", ["PLN", "SEK", "pln"])
async def test_wo89_the_database_refuses_a_direct_insert_that_bypasses_the_service(
    db_session, currency
):
    """`ck_fuel_transactions_fx_provenance`, third conjunct. The service gate
    protects the one path that exists today; the constraint protects every path
    that does not exist yet — a migration, a fixture, a repair script, a future
    writer. The lower-case case pins `upper(currency)` on the SQL side."""
    org_id, entity_id = await _org_entity(db_session)

    db_session.add(
        FuelTransaction(
            org_id=org_id,
            entity_id=entity_id,
            product_group="Diesel",
            net_eur_eff=Decimal("1400.00"),
            **_kwargs(currency=currency, fx_source="eur"),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_wo89_the_database_refuses_an_update_that_makes_a_stored_row_inconsistent(
    db_session,
):
    """A legal converted row tampered into one claiming it never needed
    converting — refused by storage, not merely by the writer that is no longer
    involved once the row exists."""
    org_id, entity_id = await _org_entity(db_session)
    row = await fuel_ingest.ingest_transaction(
        db_session, org_id, entity_id=entity_id, **_kwargs(currency="PLN", fx_source="ecb")
    )
    await db_session.commit()

    row.fx_source = "eur"
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_wo89_the_database_still_accepts_every_legal_row(db_session):
    """The positive control. A constraint that refused everything would pass
    every negative test above and be worse than useless, so all five legal
    shapes are inserted RAW, past the service, and stored."""
    org_id, entity_id = await _org_entity(db_session)

    legal = (("EUR", None), ("EUR", "eur"), ("eur", "eur"), ("PLN", "ecb"), ("PLN", "stated"))
    for currency, fx_source in legal:
        db_session.add(
            FuelTransaction(
                org_id=org_id,
                entity_id=entity_id,
                product_group="Diesel",
                net_eur_eff=Decimal("1400.00"),
                **_kwargs(currency=currency, fx_source=fx_source),
            )
        )
    await db_session.commit()
    assert await _count(db_session, org_id) == len(legal)


# --------------------------------------------------------------------------- #
# The OTHER money-bearing transport table (WO-84's rebate registry)
# --------------------------------------------------------------------------- #


def _rebate_row(org_id: str, *, currency: str, fx_source: str | None, seq: int = 1):
    return VatOffInvoiceRebate(
        org_id=org_id,
        supplier="Q8",
        country="LV",
        period=PERIOD,
        source_ref=f"RB-{seq:04d}",
        source_party="Q8 Rebate Desk",
        rebate_date=TXN_DATE,
        currency=currency,
        amount_local=Decimal("215.00"),
        amount_eur=Decimal("215.00"),
        fx_source=fx_source,
    )


@pytest.mark.parametrize("currency", ["PLN", "SEK"])
async def test_wo89_the_rebate_table_refuses_the_same_wrong_provenance(db_session, currency):
    """Parity with `fuel_transactions`, per WO-88's finding 5. `amount_eur` is
    NOT NULL and `> 0` on this table, so a non-EUR rebate claiming the identity
    provenance is the same fabricated conversion in a different column."""
    org = await make_org(db_session)
    await db_session.commit()

    db_session.add(_rebate_row(org.id, currency=currency, fx_source="eur"))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_wo89_the_rebate_table_still_accepts_what_its_writer_produces(db_session):
    """`rebate._resolve_eur` emits exactly two provenances; both must survive
    the widened constraint, or the order would have broken the writer it
    protects."""
    org = await make_org(db_session)
    await db_session.commit()

    db_session.add(_rebate_row(org.id, currency="EUR", fx_source=rebate.FX_SOURCE_EUR, seq=1))
    db_session.add(_rebate_row(org.id, currency="PLN", fx_source=rebate.FX_SOURCE_ECB, seq=2))
    await db_session.commit()

    stored = (await db_session.scalars(select(VatOffInvoiceRebate))).all()
    assert {(r.currency, r.fx_source) for r in stored} == {("EUR", "eur"), ("PLN", "ecb")}


async def test_wo89_the_rebate_writer_cannot_produce_the_wrong_provenance(db_session):
    """WO-89 adds NO service gate to `rebate.record_rebate`, because a second
    check there would be dead code (WO-88's own reasoning for this table). That
    claim is asserted here through the REAL function rather than assumed: EUR
    gets the identity, and a foreign currency can only ever come back `ecb` —
    there is no branch that returns `eur` for anything else."""
    eur = await rebate._resolve_eur(
        db_session, amount_local=Decimal("215.00"), currency="PLN", on_date=TXN_DATE
    )
    assert eur[4] == rebate.FX_SOURCE_ECB

    same = await rebate._resolve_eur(
        db_session, amount_local=Decimal("215.00"), currency="EUR", on_date=TXN_DATE
    )
    assert same[4] == rebate.FX_SOURCE_EUR

    lower = await rebate._resolve_eur(
        db_session, amount_local=Decimal("215.00"), currency="eur", on_date=TXN_DATE
    )
    assert lower[4] == rebate.FX_SOURCE_EUR


# --------------------------------------------------------------------------- #
# Defence in depth — WO-87's analysis boundary is UNCHANGED
# --------------------------------------------------------------------------- #


def test_wo89_the_analysis_boundary_is_unchanged():
    """WO-87's `savings._require_eur_basis` is deliberately NOT widened by this
    order: after WO-89 the wrong-provenance row cannot exist in storage, so the
    analysis boundary can never meet one, and a guard clause that cannot fire is
    unreachable code with a test that proves nothing. What it DOES still do —
    refuse WO-88's two combinations with `fx_rate_unavailable` and pass the
    legal ones — is re-asserted here so the decision is visible rather than
    silent, and so a future edit to that function fails in this file too."""
    for row in (_detached(fx_source="unknown"), _detached(currency="PLN", fx_source=None)):
        with pytest.raises(ValidationError) as exc:
            savings._require_eur_basis([row])
        assert exc.value.code == "fx_rate_unavailable"

    savings._require_eur_basis(
        [_detached(fx_source=None), _detached(currency="PLN", fx_source="ecb")]
    )
    # And it still PASSES the wrong-provenance row — which is exactly why the
    # writer and the database, not the reader, are where this rule had to land.
    savings._require_eur_basis([_detached(currency="PLN", fx_source="eur")])


async def test_wo89_a_second_tenants_row_is_unaffected(db_session):
    """The gate is per-row and org-agnostic. Tenant B holds an identical-looking
    legal line — same supplier, same period, same currency, same euro — and
    tenant A's refusal changes nothing about it (§8: overlapping fixtures, so a
    broken filter cannot pass by accident)."""
    org_a, entity_a = await _org_entity(db_session, seed=1)
    org_b, entity_b = await _org_entity(db_session, seed=2)

    await fuel_ingest.ingest_transaction(
        db_session, org_b, entity_id=entity_b, **_kwargs(currency="PLN", fx_source="ecb")
    )
    await db_session.commit()

    with pytest.raises(ValidationError):
        await fuel_ingest.ingest_transaction(
            db_session, org_a, entity_id=entity_a, **_kwargs(currency="PLN", fx_source="eur")
        )
    await db_session.rollback()

    assert await _count(db_session, org_a) == 0
    assert await _count(db_session, org_b) == 1
