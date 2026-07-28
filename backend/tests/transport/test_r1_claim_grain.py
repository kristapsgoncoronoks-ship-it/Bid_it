"""R1 — a VAT refund claim is keyed (org, entity, refund_country, ref_period),
period in {YYYY-Qn, YYYY-YEAR}. Art. 16 Dir. 2008/9/EC: a refund period is a
calendar quarter or a calendar year (min 3 months, max 1 year).
`docs/plan/shared/specs/BA_fleet_fuel.md` R1 row: "Creating two claims with
the same key upserts, never duplicates."
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.transport.vat_claim import VatRefundClaim
from app.services.transport import claim
from tests.transport.conftest import enable_transport, make_entity, make_org


@pytest.mark.asyncio
async def test_r1_get_or_create_claim_upserts_never_duplicates(db_session):
    """Calling `get_or_create_claim` twice with the SAME grain key returns the
    SAME row and creates no second one — the R1 acceptance test verbatim."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    first = await claim.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="lv", ref_period="2025-Q4"
    )
    await db_session.commit()

    second = await claim.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="LV", ref_period="2025-Q4"
    )
    await db_session.commit()

    assert first.id == second.id
    count = await db_session.scalar(
        select(VatRefundClaim.id).where(
            VatRefundClaim.org_id == org.id,
            VatRefundClaim.entity_id == entity.id,
            VatRefundClaim.refund_country == "LV",
            VatRefundClaim.ref_period == "2025-Q4",
        )
    )
    assert count == first.id
    all_rows = (
        await db_session.scalars(
            select(VatRefundClaim).where(VatRefundClaim.entity_id == entity.id)
        )
    ).all()
    assert len(all_rows) == 1

    # Audited exactly once — the second (idempotent) call is a no-op read, not
    # a second mutation.
    creates = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.org_id == org.id, AuditEvent.action == "transport.claim_create"
            )
        )
    ).all()
    assert len(creates) == 1


@pytest.mark.asyncio
async def test_r1_country_normalized_to_uppercase_stays_the_same_grain(db_session):
    """A lower-case refund_country resolves to the SAME row as upper-case —
    the grain is normalized, not case-sensitive by accident."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    a = await claim.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="be", ref_period="2025-YEAR"
    )
    b = await claim.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="BE", ref_period="2025-YEAR"
    )
    assert a.id == b.id
    assert a.refund_country == "BE"


@pytest.mark.asyncio
async def test_r1_grain_uniqueness_constraint_rejects_a_raw_duplicate_insert(db_session):
    """The DB constraint itself (`uq_vat_refund_claims_grain`) is the backstop
    under a lost race — a bare second INSERT with the identical key, bypassing
    the service's find-first lookup, must be refused by the database."""
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()

    db_session.add(
        VatRefundClaim(
            org_id=org.id, entity_id=entity.id, refund_country="SE", ref_period="2026-Q1"
        )
    )
    await db_session.commit()

    db_session.add(
        VatRefundClaim(
            org_id=org.id, entity_id=entity.id, refund_country="SE", ref_period="2026-Q1"
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_r1_different_periods_or_countries_are_distinct_claims(db_session):
    """Changing ANY one of the four grain fields is a different claim — the
    constraint must not be over-broad."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    q4 = await claim.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="DE", ref_period="2025-Q4"
    )
    year = await claim.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="DE", ref_period="2025-YEAR"
    )
    other_country = await claim.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="FR", ref_period="2025-Q4"
    )
    assert len({q4.id, year.id, other_country.id}) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "good", ["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4", "2025-YEAR", "2099-YEAR"]
)
async def test_r1_valid_period_shapes_accepted(good):
    claim.validate_ref_period(good)  # does not raise


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "2025-Q5",  # no 5th quarter
        "2025-Q0",  # no 0th quarter
        "2025",  # missing suffix
        "25-Q1",  # 2-digit year
        "2025-YEARLY",  # not the exact literal
        "2025-year",  # case-sensitive
        "INPUT-Q1",  # a synthetic placeholder must never even reach the parser
        "",
    ],
)
async def test_r1_malformed_periods_rejected(bad):
    with pytest.raises(AppError) as exc:
        claim.validate_ref_period(bad)
    assert exc.value.code == "invalid_period"
    assert exc.value.status == 422


@pytest.mark.asyncio
async def test_r1_get_or_create_claim_rejects_malformed_period_and_writes_nothing(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)

    with pytest.raises(AppError) as exc:
        await claim.get_or_create_claim(
            db_session, org.id, entity_id=entity.id, refund_country="LV", ref_period="2025-Q9"
        )
    assert exc.value.code == "invalid_period"

    rows = (await db_session.scalars(select(VatRefundClaim))).all()
    assert rows == []


@pytest.mark.asyncio
async def test_r1_unknown_entity_is_a_404_not_found_never_a_500(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)

    with pytest.raises(AppError) as exc:
        await claim.get_or_create_claim(
            db_session,
            org.id,
            entity_id=str(uuid.uuid4()),
            refund_country="LV",
            ref_period="2025-Q4",
        )
    assert exc.value.code == "entity_not_found"
    assert exc.value.status == 404


@pytest.mark.asyncio
async def test_r1_cross_tenant_entity_is_opaque_not_found_never_leaks_across_orgs(db_session):
    """Tenancy invariant (master context §4.4): a cross-tenant fetch by id is
    an opaque 404, never a 403 or a successful cross-tenant claim. Org B's
    entity id is a perfectly real id — just not org A's."""
    org_a = await make_org(db_session, name="Org A")
    org_b = await make_org(db_session, name="Org B")
    await enable_transport(db_session, org_a.id)
    entity_b = await make_entity(db_session, org_b.id)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await claim.get_or_create_claim(
            db_session,
            org_a.id,
            entity_id=entity_b.id,
            refund_country="LV",
            ref_period="2025-Q4",
        )
    assert exc.value.code == "entity_not_found"
    assert exc.value.status == 404

    # And org A really did get nothing written.
    rows = (
        await db_session.scalars(select(VatRefundClaim).where(VatRefundClaim.org_id == org_a.id))
    ).all()
    assert rows == []
