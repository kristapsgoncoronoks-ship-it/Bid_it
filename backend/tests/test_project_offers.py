"""Offers/estimates + the invoicing plan — lifecycle phase 4 (§5a).

What phase 4 claims, pinned:

1. **History survives every edit** — a revision is a new version, the prior
   flips to superseded, and terminal decisions cannot be mutated in place.
2. **Acceptance seeds only an EMPTY plan** — a plan someone shaped by hand is
   theirs; acceptance never rewrites it.
3. **The plan is tracked against the SAME revenue figure the P&L shows** — the
   two screens can never disagree about how much has been issued.
4. **The estimate reaches the P&L** — estimated_revenue appears the moment an
   offer is accepted, so estimated-vs-actual is readable from day one.

Industry-neutral fixtures throughout.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.organization import Organization


async def _org(db) -> str:
    return await db.scalar(select(Organization.id).where(Organization.name == "Acme"))


async def _project(client, code) -> str:
    r = await client.post("/api/v1/masters/projects", json={"code": code, "name": f"Job {code}"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


async def _offer(client, project_id, *, lines=None, title="Quote"):
    r = await client.post(
        f"/api/v1/masters/projects/{project_id}/offers",
        json={
            "title": title,
            "lines": lines or [{"description": "Work performed", "amount": "1000.00"}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_offers_number_sequentially_with_the_default_prefix(auth_client):
    project_id = await _project(auth_client, "OFF-SEQ")

    first = await _offer(auth_client, project_id)
    second = await _offer(auth_client, project_id)

    assert first["number"] == "OFF-1"
    assert second["number"] == "OFF-2"
    assert first["version"] == 1
    assert first["status"] == "draft"
    assert first["total"] == "1000.00"


@pytest.mark.asyncio
async def test_the_client_sets_the_numbering_prefix(auth_client, db_session):
    """Owner decision: the numbering LOGIC is the client's. The platform's one
    rule — per-org uniqueness — holds whatever prefix they pick."""
    org = await db_session.scalar(select(Organization).where(Organization.name == "Acme"))
    org.offer_prefix = "QUO/"
    await db_session.commit()
    project_id = await _project(auth_client, "OFF-PFX")

    offer = await _offer(auth_client, project_id)

    assert offer["number"] == "QUO/1"


@pytest.mark.asyncio
async def test_a_revision_is_a_new_version_and_history_survives(auth_client):
    project_id = await _project(auth_client, "OFF-REV")
    v1 = await _offer(auth_client, project_id)

    rev = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/offers/{v1['id']}/revise",
        json={"lines": [{"description": "Work performed", "amount": "1200.00"}]},
    )
    assert rev.status_code == 201, rev.text
    v2 = rev.json()
    assert v2["number"] == v1["number"]
    assert v2["version"] == 2
    assert v2["total"] == "1200.00"

    listed = (await auth_client.get(f"/api/v1/masters/projects/{project_id}/offers")).json()
    by_version = {o["version"]: o["status"] for o in listed}
    assert by_version == {1: "superseded", 2: "draft"}, (
        "the prior version must survive, marked superseded — history is the record"
    )

    # A superseded version cannot be revised — only the latest can.
    stale = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/offers/{v1['id']}/revise",
        json={"lines": [{"description": "X", "amount": "1"}]},
    )
    assert stale.status_code == 400


@pytest.mark.asyncio
async def test_terminal_offers_do_not_transition(auth_client):
    project_id = await _project(auth_client, "OFF-TERM")
    offer = await _offer(auth_client, project_id)
    base = f"/api/v1/masters/projects/{project_id}/offers/{offer['id']}/transition"

    assert (await auth_client.post(base, json={"status": "accepted"})).status_code == 200
    again = await auth_client.post(base, json={"status": "rejected"})
    assert again.status_code == 400, "an accepted offer is a decision, not a draft"


@pytest.mark.asyncio
async def test_acceptance_seeds_an_empty_plan_from_the_offer_lines(auth_client):
    project_id = await _project(auth_client, "OFF-SEED")
    offer = await _offer(
        auth_client,
        project_id,
        lines=[
            {"description": "Advance", "amount": "3000.00"},
            {"description": "On completion", "amount": "7000.00"},
        ],
    )

    r = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/offers/{offer['id']}/transition",
        json={"status": "accepted"},
    )
    assert r.status_code == 200, r.text

    plan = (await auth_client.get(f"/api/v1/masters/projects/{project_id}/invoicing-plan")).json()
    assert [(row["label"], row["amount"]) for row in plan["rows"]] == [
        ("Advance", "3000.00"),
        ("On completion", "7000.00"),
    ]
    assert plan["contracted_total"] == "10000.00"


@pytest.mark.asyncio
async def test_acceptance_never_rewrites_a_hand_shaped_plan(auth_client):
    """The plan belongs to whoever shaped it. Acceptance seeds only emptiness."""
    project_id = await _project(auth_client, "OFF-KEEP")
    put = await auth_client.put(
        f"/api/v1/masters/projects/{project_id}/invoicing-plan",
        json=[{"label": "Hand-agreed advance", "amount": "500.00"}],
    )
    assert put.status_code == 200, put.text

    offer = await _offer(auth_client, project_id)
    r = await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/offers/{offer['id']}/transition",
        json={"status": "accepted"},
    )
    assert r.status_code == 200

    plan = (await auth_client.get(f"/api/v1/masters/projects/{project_id}/invoicing-plan")).json()
    assert [row["label"] for row in plan["rows"]] == ["Hand-agreed advance"], (
        "acceptance must not overwrite a plan someone already shaped"
    )


@pytest.mark.asyncio
async def test_the_plan_tracks_the_pnl_revenue_figure(auth_client, db_session):
    """contracted − issued = remaining, where issued IS the P&L's revenue —
    same basis, so the plan screen and the P&L can never disagree."""
    project_id = await _project(auth_client, "OFF-TRK")
    await auth_client.put(
        f"/api/v1/masters/projects/{project_id}/invoicing-plan",
        json=[
            {"label": "Advance", "amount": "300.00"},
            {"label": "Final", "amount": "700.00"},
        ],
    )
    # Issue revenue under the project (module + issuer setup, then one invoice).
    assert (
        await auth_client.put(
            "/api/v1/issuer",
            json={
                "legal_name": "Acme OU",
                "reg_number": "12345678",
                "vat_number": "EE101234567",
                "address_line1": "Main 1",
                "city": "Tallinn",
                "postal_code": "10111",
                "country": "EE",
                "invoice_prefix": "ACM-",
            },
        )
    ).status_code == 200
    assert (
        await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    ).status_code == 200
    issued = await auth_client.post(
        "/api/v1/issued",
        json={
            "buyer_name": "Customer OU",
            "project_id": project_id,
            "lines": [
                {
                    "description": "Advance",
                    "quantity": "1",
                    "unit_price": "300.00",
                    "vat_rate": "22",
                }
            ],
        },
    )
    assert issued.status_code == 201, issued.text

    plan = (await auth_client.get(f"/api/v1/masters/projects/{project_id}/invoicing-plan")).json()
    pnl = (await auth_client.get(f"/api/v1/masters/projects/{project_id}/pnl")).json()
    assert plan["contracted_total"] == "1000.00"
    assert plan["issued_total"] == pnl["revenue"] == "300.00"
    assert plan["remaining"] == "700.00"


@pytest.mark.asyncio
async def test_the_accepted_estimate_reaches_the_pnl(auth_client):
    project_id = await _project(auth_client, "OFF-EST")
    pnl = (await auth_client.get(f"/api/v1/masters/projects/{project_id}/pnl")).json()
    assert pnl["estimated_revenue"] is None, "no estimate before any acceptance"

    offer = await _offer(
        auth_client, project_id, lines=[{"description": "Work", "amount": "2500.00"}]
    )
    await auth_client.post(
        f"/api/v1/masters/projects/{project_id}/offers/{offer['id']}/transition",
        json={"status": "accepted"},
    )

    pnl = (await auth_client.get(f"/api/v1/masters/projects/{project_id}/pnl")).json()
    assert pnl["estimated_revenue"] == "2500.00"


@pytest.mark.asyncio
async def test_plan_rows_must_be_labelled_and_positive(auth_client):
    project_id = await _project(auth_client, "OFF-VAL")
    base = f"/api/v1/masters/projects/{project_id}/invoicing-plan"

    assert (
        await auth_client.put(base, json=[{"label": "Advance", "amount": "-10"}])
    ).status_code == 400
    # Pydantic refuses a blank label before the service sees it — either layer
    # refusing is correct; the subject is that it IS refused.
    r = await auth_client.put(base, json=[{"label": "", "amount": "10"}])
    assert r.status_code in (400, 422)
