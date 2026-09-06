"""WO-AH — "due in the next N days": the R12 `action_deadline` as a worklist.

The recovery dashboard bucketed claims by readiness state and never by date;
the deadline an authority's request must be answered by (R12, stamped with the
manual status code) lived on the row and on no surface. `due_soon` is a date
view over it: open claims with a deadline on or before today + N, soonest
first, ALREADY-OVERDUE ONES INCLUDED and flagged.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.errors import AppError
from app.services.transport import claim as claim_svc
from app.services.transport import recovery
from tests.transport.conftest import enable_transport, make_entity, make_org

TODAY = date(2026, 9, 6)


async def _claim_with_deadline(
    db_session, org, entity, *, period: str, deadline: date | None, country="LV"
):
    claim = await claim_svc.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country=country, ref_period=period
    )
    if deadline is not None:
        # R12's deadline is stamped by `status.set_status_code` with a manual
        # reminder code, which the service (rightly) refuses before submission.
        # The view under test reads rows, so the fixture writes the row the
        # way that call would have left it: a submitted claim carrying "2B".
        claim.status = "submitted"
        claim.status_code = "2B"
        claim.action_deadline = deadline
    await db_session.commit()
    return claim


@pytest.mark.asyncio
async def test_wo_ah_lists_open_claims_by_deadline_overdue_first_and_flagged(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()

    overdue = await _claim_with_deadline(
        db_session, org, entity, period="2026-Q1", deadline=date(2026, 9, 1)
    )
    soon = await _claim_with_deadline(
        db_session, org, entity, period="2026-Q2", deadline=date(2026, 9, 15)
    )
    later = await _claim_with_deadline(
        db_session, org, entity, period="2025-Q4", deadline=date(2026, 10, 30)
    )
    none = await _claim_with_deadline(db_session, org, entity, period="2026-Q3", deadline=None)

    view = await recovery.due_soon(db_session, org.id, days=14, today=TODAY)
    assert view.days == 14 and view.today == TODAY
    assert [i.claim_id for i in view.items] == [overdue.id, soon.id]
    assert later.id not in {i.claim_id for i in view.items}
    assert none.id not in {i.claim_id for i in view.items}

    first, second = view.items
    assert first.overdue is True and first.days_left == -5 and first.status_code == "2B"
    assert second.overdue is False and second.days_left == 9
    assert view.overdue_claims == 1
    # A draft has no frozen figure — null, never a made-up zero.
    assert first.vat_eur is None

    # A wider horizon takes the later one in, in date order — across refund years.
    wide = await recovery.due_soon(db_session, org.id, days=60, today=TODAY)
    assert [i.ref_period for i in wide.items] == ["2026-Q1", "2026-Q2", "2025-Q4"]


@pytest.mark.asyncio
async def test_wo_ah_a_terminal_claim_with_a_past_deadline_is_history_not_work(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    claim = await _claim_with_deadline(
        db_session, org, entity, period="2026-Q1", deadline=date(2026, 9, 1)
    )
    claim.status = "rejected"
    await db_session.commit()
    view = await recovery.due_soon(db_session, org.id, days=14, today=TODAY)
    assert view.items == ()


@pytest.mark.asyncio
async def test_wo_ah_the_horizon_is_bounded_and_the_module_gate_holds(db_session):
    org = await make_org(db_session)
    await db_session.commit()
    with pytest.raises(AppError) as refused:
        await recovery.due_soon(db_session, org.id, days=14, today=TODAY)
    assert refused.value.code == "module_not_enabled"

    await enable_transport(db_session, org.id)
    await db_session.commit()
    for bad in (0, 366):
        with pytest.raises(AppError) as refused:
            await recovery.due_soon(db_session, org.id, days=bad, today=TODAY)
        assert refused.value.code == "invalid_days"


@pytest.mark.asyncio
async def test_wo_ah_over_http_the_view_is_a_transport_read(auth_client, db_session):
    from sqlalchemy import select

    from app.models.organization import Organization
    from app.models.user import User

    user = await db_session.scalar(select(User).order_by(User.created_at).limit(1))
    org = await db_session.get(Organization, user.org_id)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    await _claim_with_deadline(db_session, org, entity, period="2026-Q1", deadline=date.today())

    r = await auth_client.get("/api/v1/transport/recovery-dashboard/due-soon", params={"days": 7})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["days"] == 7 and len(body["items"]) == 1
    assert body["items"][0]["days_left"] == 0 and body["items"][0]["overdue"] is False

    bad = await auth_client.get("/api/v1/transport/recovery-dashboard/due-soon", params={"days": 0})
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_days"
