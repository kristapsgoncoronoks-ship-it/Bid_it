"""Project profitability phase 2 — allocation and the close-time freeze.

The two claims phase 2 makes, pinned:

1. **Allocation never invents or loses a cent.** One supplier invoice covering
   many jobs splits under the precedence rule (line > split > whole-invoice),
   quantized per share with the rounding residue on the largest share — so the
   parts always sum to the base, provable per test with hand-checkable numbers.
2. **A closed project's figure does not move behind the client's back.** The
   close stores the P&L in the same transaction as the transition; a document
   arriving after the close surfaces as a labelled adjustment next to the
   frozen figure; reopening discards the snapshot, audited.

Industry-neutral fixtures throughout (generic suppliers, generic jobs).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, LineItem
from app.models.organization import Organization
from app.models.vendor import Vendor


async def _org(db) -> str:
    return await db.scalar(select(Organization.id).where(Organization.name == "Acme"))


async def _project(client, code) -> str:
    r = await client.post("/api/v1/masters/projects", json={"code": code, "name": f"Job {code}"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _invoice(db, org_id, *, subtotal, number, lines=()):
    vendor = await db.scalar(select(Vendor).where(Vendor.org_id == org_id))
    if vendor is None:
        vendor = Vendor(org_id=org_id, name="Generic Supplier OU")
        db.add(vendor)
        await db.flush()
    inv = Invoice(
        org_id=org_id,
        vendor_id=vendor.id,
        invoice_number=number,
        issue_date=date(2026, 8, 1),
        subtotal=Decimal(subtotal),
        total=Decimal(subtotal),
    )
    db.add(inv)
    await db.flush()
    ids = []
    for amount in lines:
        li = LineItem(invoice_id=inv.id, description=f"Line {amount}", amount=Decimal(amount))
        db.add(li)
        await db.flush()
        ids.append(li.id)
    await db.commit()
    return inv.id, ids


async def _pnl(client, project_id) -> dict:
    r = await client.get(f"/api/v1/masters/projects/{project_id}/pnl")
    assert r.status_code == 200, r.text
    return r.json()


async def _version(client, project_id) -> int:
    rows = (await client.get("/api/v1/masters/projects")).json()
    return next(r["version"] for r in rows if r["id"] == project_id)


async def _transition(client, project_id, status):
    r = await client.patch(
        f"/api/v1/masters/projects/{project_id}",
        json={"status": status, "version": await _version(client, project_id)},
    )
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# Allocation — the precedence rule, cent-exact
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_split_divides_the_invoice_and_the_parts_sum_exactly(auth_client, db_session):
    """100.01 at 60/40 → 60.01 + 40.00 (residue on the largest share). The sum
    of what the projects see IS the invoice — no cent invented, none lost."""
    org_id = await _org(db_session)
    p1, p2 = await _project(auth_client, "SPL-1"), await _project(auth_client, "SPL-2")
    invoice_id, _ = await _invoice(db_session, org_id, subtotal="100.01", number="SUP-SPLIT")

    r = await auth_client.put(
        f"/api/v1/invoices/{invoice_id}/allocation",
        json={
            "splits": [
                {"project_id": p1, "percent": "60"},
                {"project_id": p2, "percent": "40"},
            ]
        },
    )
    assert r.status_code == 200, r.text

    a = Decimal((await _pnl(auth_client, p1))["invoice_costs"])
    b = Decimal((await _pnl(auth_client, p2))["invoice_costs"])
    assert a == Decimal("60.01")
    assert b == Decimal("40.00")
    assert a + b == Decimal("100.01")


@pytest.mark.asyncio
async def test_a_three_way_split_where_naive_rounding_drifts(auth_client, db_session):
    """10.00 at 33.33/33.33/33.34: each share rounds to 3.33, summing to 9.99 —
    a cent LOST unless the residue lands on the largest share. This is the test
    the 60/40 case cannot be (its rounding happens to sum cleanly); the first
    seeded run proved that by staying green with the residue rule removed."""
    org_id = await _org(db_session)
    p1 = await _project(auth_client, "TRI-1")
    p2 = await _project(auth_client, "TRI-2")
    p3 = await _project(auth_client, "TRI-3")
    invoice_id, _ = await _invoice(db_session, org_id, subtotal="10.00", number="SUP-TRI")

    r = await auth_client.put(
        f"/api/v1/invoices/{invoice_id}/allocation",
        json={
            "splits": [
                {"project_id": p1, "percent": "33.33"},
                {"project_id": p2, "percent": "33.33"},
                {"project_id": p3, "percent": "33.34"},
            ]
        },
    )
    assert r.status_code == 200, r.text

    shares = [Decimal((await _pnl(auth_client, p))["invoice_costs"]) for p in (p1, p2, p3)]
    assert sum(shares) == Decimal("10.00"), f"a cent was invented or lost: {shares}"
    assert shares[2] == Decimal("3.34"), "the residue lands on the largest share"


@pytest.mark.asyncio
async def test_a_tagged_line_wins_and_the_remainder_follows_the_invoice(auth_client, db_session):
    """Precedence end to end: a line tagged to P1 claims its 30; the remainder
    (100 − 30 = 70) follows the invoice's own project P2."""
    org_id = await _org(db_session)
    p1, p2 = await _project(auth_client, "LIN-1"), await _project(auth_client, "LIN-2")
    invoice_id, line_ids = await _invoice(
        db_session, org_id, subtotal="100.00", number="SUP-LINES", lines=("30.00", "70.00")
    )

    r = await auth_client.put(
        f"/api/v1/invoices/{invoice_id}/allocation",
        json={"project_id": p2, "lines": {line_ids[0]: p1}},
    )
    assert r.status_code == 200, r.text

    assert (await _pnl(auth_client, p1))["invoice_costs"] == "30.00"
    assert (await _pnl(auth_client, p2))["invoice_costs"] == "70.00"


@pytest.mark.asyncio
async def test_splits_that_do_not_sum_to_100_are_refused(auth_client, db_session):
    org_id = await _org(db_session)
    p1 = await _project(auth_client, "BAD-1")
    invoice_id, _ = await _invoice(db_session, org_id, subtotal="100.00", number="SUP-BAD")

    r = await auth_client.put(
        f"/api/v1/invoices/{invoice_id}/allocation",
        json={"splits": [{"project_id": p1, "percent": "90"}]},
    )
    assert r.status_code == 400
    assert "100" in r.json()["detail"]


@pytest.mark.asyncio
async def test_an_unknown_project_in_the_allocation_is_an_opaque_404(auth_client, db_session):
    org_id = await _org(db_session)
    invoice_id, _ = await _invoice(db_session, org_id, subtotal="50.00", number="SUP-XT")

    r = await auth_client.put(
        f"/api/v1/invoices/{invoice_id}/allocation",
        json={"project_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_replacing_a_split_replaces_it_entirely(auth_client, db_session):
    """PUT semantics: the second allocation is the whole truth — no ghost of the
    first survives to double-count."""
    org_id = await _org(db_session)
    p1, p2 = await _project(auth_client, "REP-1"), await _project(auth_client, "REP-2")
    invoice_id, _ = await _invoice(db_session, org_id, subtotal="100.00", number="SUP-REP")

    await auth_client.put(
        f"/api/v1/invoices/{invoice_id}/allocation",
        json={"splits": [{"project_id": p1, "percent": "100"}]},
    )
    await auth_client.put(
        f"/api/v1/invoices/{invoice_id}/allocation",
        json={"splits": [{"project_id": p2, "percent": "100"}]},
    )

    assert (await _pnl(auth_client, p1))["invoice_costs"] == "0.00"
    assert (await _pnl(auth_client, p2))["invoice_costs"] == "100.00"


# --------------------------------------------------------------------------- #
# The freeze — a closed figure does not move behind the client's back
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_closing_freezes_the_pnl_and_late_costs_become_adjustments(auth_client, db_session):
    org_id = await _org(db_session)
    project_id = await _project(auth_client, "FRZ-1")
    inv1, _ = await _invoice(db_session, org_id, subtotal="200.00", number="SUP-BEFORE")
    await auth_client.put(f"/api/v1/invoices/{inv1}/allocation", json={"project_id": project_id})

    await _transition(auth_client, project_id, "closed")

    frozen = await _pnl(auth_client, project_id)
    assert frozen["basis"] == "net_eur_frozen"
    assert frozen["costs"] == "200.00"
    assert frozen["pnl_frozen_at"] is not None
    assert frozen["adjustments"] == {}

    # The supplier invoice for the job's last week arrives after the close.
    inv2, _ = await _invoice(db_session, org_id, subtotal="50.00", number="SUP-AFTER")
    await auth_client.put(f"/api/v1/invoices/{inv2}/allocation", json={"project_id": project_id})

    after = await _pnl(auth_client, project_id)
    assert after["costs"] == "200.00", "the frozen figure must not move"
    assert after["adjustments"]["costs"] == "50.00", "the late cost is displayed, not hidden"
    assert after["adjustments"]["profit"] == "-50.00"


@pytest.mark.asyncio
async def test_reopening_discards_the_snapshot_and_goes_live_again(auth_client, db_session):
    org_id = await _org(db_session)
    project_id = await _project(auth_client, "FRZ-2")
    inv1, _ = await _invoice(db_session, org_id, subtotal="100.00", number="SUP-RE1")
    await auth_client.put(f"/api/v1/invoices/{inv1}/allocation", json={"project_id": project_id})
    await _transition(auth_client, project_id, "closed")

    inv2, _ = await _invoice(db_session, org_id, subtotal="25.00", number="SUP-RE2")
    await auth_client.put(f"/api/v1/invoices/{inv2}/allocation", json={"project_id": project_id})
    await _transition(auth_client, project_id, "active")

    live = await _pnl(auth_client, project_id)
    assert live["basis"] == "net_eur_live"
    assert live["costs"] == "125.00", "reopened = live: every real cost counts again"
    assert live["adjustments"] == {}
    assert live["pnl_frozen_at"] is None


@pytest.mark.asyncio
async def test_the_close_and_its_snapshot_are_audited_together(auth_client, db_session):
    from app.models.audit import AuditEvent

    project_id = await _project(auth_client, "FRZ-3")
    await _transition(auth_client, project_id, "closed")

    from app.services.audit import A

    event = await db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.action == A.MASTER_UPDATE, AuditEvent.target_id == project_id)
        .order_by(AuditEvent.at_ms.desc())
    )
    assert event is not None
    assert "pnl_frozen" in (event.meta or ""), (
        "the audit trail must show WHAT figure was frozen at close"
    )


# --------------------------------------------------------------------------- #
# The list — which contracts lose money
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_summary_carries_every_project_with_margin(auth_client, db_session):
    org_id = await _org(db_session)
    p1 = await _project(auth_client, "SUM-1")
    inv, _ = await _invoice(db_session, org_id, subtotal="80.00", number="SUP-SUM")
    await auth_client.put(f"/api/v1/invoices/{inv}/allocation", json={"project_id": p1})

    r = await auth_client.get("/api/v1/masters/projects-pnl-summary")
    assert r.status_code == 200, r.text
    rows = {row["code"]: row for row in r.json()}
    assert rows["SUM-1"]["costs"] == "80.00"
    assert rows["SUM-1"]["basis"] == "net_eur_live"


@pytest.mark.asyncio
async def test_allocation_reads_back_in_the_shape_the_put_accepts(auth_client, db_session):
    """Read → edit → PUT back, no translation: the GET's body is a valid PUT
    body, so a UI round-trip can never corrupt an allocation it didn't touch."""
    org_id = await _org(db_session)
    p1, p2 = await _project(auth_client, "RT-1"), await _project(auth_client, "RT-2")
    invoice_id, line_ids = await _invoice(
        db_session, org_id, subtotal="100.00", number="SUP-RT", lines=("40.00", "60.00")
    )

    put = await auth_client.put(
        f"/api/v1/invoices/{invoice_id}/allocation",
        json={
            "project_id": p1,
            "splits": [
                {"project_id": p1, "percent": "70"},
                {"project_id": p2, "percent": "30"},
            ],
            "lines": {line_ids[0]: p2},
        },
    )
    assert put.status_code == 200, put.text

    got = (await auth_client.get(f"/api/v1/invoices/{invoice_id}/allocation")).json()
    assert got["project_id"] == p1
    assert {(s["project_id"], s["percent"]) for s in got["splits"]} == {
        (p1, "70.00"),
        (p2, "30.00"),
    }
    assert got["lines"] == {line_ids[0]: p2}

    # The GET body IS a valid PUT body — round-trip changes nothing.
    again = await auth_client.put(f"/api/v1/invoices/{invoice_id}/allocation", json=got)
    assert again.status_code == 200, again.text
    assert (await auth_client.get(f"/api/v1/invoices/{invoice_id}/allocation")).json() == got
