"""Independent project EUR basis expectations, missing rates and frozen history."""

from datetime import date
from decimal import Decimal

import pytest

from app.models.costing import Project
from app.models.fx import EcbRate
from tests.test_project_profitability import _issuer_ready, _project


async def issue(client, pid, currency, amount="100.00"):
    response = await client.post(
        "/api/v1/issued",
        json={
            "project_id": pid,
            "buyer_name": "Synthetic Buyer",
            "currency": currency,
            "issue_date": "2026-10-01",
            "lines": [
                {
                    "description": "Synthetic work",
                    "quantity": "1",
                    "unit_price": amount,
                    "vat_rate": "0",
                }
            ],
        },
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_project_revenue_converts_each_source_before_summing(auth_client, db_session):
    await _issuer_ready(auth_client)
    pid = await _project(auth_client)
    # ECB convention: foreign units per EUR. 100 / (10/9) rounds to EUR90.
    db_session.add(EcbRate(rate_date=date(2026, 10, 1), currency="USD", rate=Decimal("1.11111111")))
    await db_session.commit()
    await issue(auth_client, pid, "EUR")
    foreign = await issue(auth_client, pid, "USD")
    response = await auth_client.get(f"/api/v1/masters/projects/{pid}/pnl")
    assert response.status_code == 200
    pnl = response.json()
    assert pnl["revenue"] == "190.00"
    assert pnl["profit"] == "190.00"
    assert pnl["complete"] is True
    trace = next(row for row in pnl["fx_trace"] if row["source_id"] == foreign["id"])
    assert trace["currency"] == "USD" and trace["original_amount"] == "100.00"
    assert trace["amount_eur"] == "90.00" and trace["rate_date"] == "2026-10-01"
    assert trace["approximate"] is False
    rows = (await auth_client.get("/api/v1/masters/projects-pnl-summary")).json()
    assert next(row for row in rows if row["project_id"] == pid)["revenue"] == "190.00"


@pytest.mark.asyncio
async def test_missing_currency_rate_never_returns_confident_margin_or_freezes(
    auth_client, db_session
):
    await _issuer_ready(auth_client)
    pid = await _project(auth_client)
    await issue(auth_client, pid, "ZZZ")
    pnl = (await auth_client.get(f"/api/v1/masters/projects/{pid}/pnl")).json()
    assert pnl["complete"] is False
    assert pnl["revenue"] is None and pnl["profit"] is None and pnl["margin_pct"] is None
    assert pnl["issues"] and "ZZZ" in str(pnl["issues"])
    plan = await auth_client.put(
        f"/api/v1/masters/projects/{pid}/invoicing-plan",
        json=[{"label": "Contract", "amount": "200.00"}],
    )
    assert plan.status_code == 200
    assert plan.json()["complete"] is False and plan.json()["remaining"] is None
    assert plan.json()["rows"][0]["amount"] == "200.00"
    readback = (await auth_client.get(f"/api/v1/masters/projects/{pid}/invoicing-plan")).json()
    assert readback["rows"] == plan.json()["rows"]
    assert (
        await auth_client.post(f"/api/v1/masters/projects/{pid}/acceptance", json={})
    ).status_code == 200
    refusal = await auth_client.post(f"/api/v1/masters/projects/{pid}/final-invoice-draft", json={})
    assert refusal.status_code == 422 and "evidence" in refusal.json()["detail"]
    actions = await auth_client.get("/api/v1/next-actions")
    assert actions.status_code == 200
    assert any(action["kind"] == "project_currency_review" for action in actions.json())
    project = await db_session.get(Project, pid)
    version = project.version
    response = await auth_client.patch(
        f"/api/v1/masters/projects/{pid}", json={"status": "closed", "version": version}
    )
    assert response.status_code == 422
    await db_session.refresh(project)
    assert project.status == "active" and project.closed_pnl_json is None


@pytest.mark.asyncio
async def test_legacy_snapshot_is_preserved_and_explicitly_unverified(auth_client, db_session):
    await _issuer_ready(auth_client)
    pid = await _project(auth_client)
    project = await db_session.get(Project, pid)
    old = '{"revenue":"200.00","credited":"0.00","costs":"0.00","invoice_costs":"0.00","expense_costs":"0.00","manual_costs":"0.00","profit":"200.00"}'
    project.status = "closed"
    project.closed_pnl_json = old
    await db_session.commit()
    pnl = (await auth_client.get(f"/api/v1/masters/projects/{pid}/pnl")).json()
    assert pnl["basis"] == "legacy_currency_unverified"
    assert pnl["complete"] is False and pnl["profit"] is None
    await db_session.refresh(project)
    assert project.closed_pnl_json == old


@pytest.mark.asyncio
async def test_foreign_credit_and_frozen_rate_evidence(auth_client, db_session):
    from app.models.fx import EcbRate

    await _issuer_ready(auth_client)
    pid = await _project(auth_client)
    quote = EcbRate(rate_date=date(2026, 10, 1), currency="USD", rate=Decimal("1.11111111"))
    db_session.add(quote)
    await db_session.commit()
    inv = await issue(auth_client, pid, "USD")
    response = await auth_client.post(
        f"/api/v1/issued/{inv['id']}/credit-note",
        json={
            "lines": [
                {
                    "description": "Partial reversal",
                    "quantity": "1",
                    "unit_price": "25.00",
                    "vat_rate": "0",
                }
            ],
        },
    )
    assert response.status_code == 201
    pnl = (await auth_client.get(f"/api/v1/masters/projects/{pid}/pnl")).json()
    assert pnl["revenue"] == "67.50" and pnl["credited"] == "22.50"
    project = await db_session.get(Project, pid)
    response = await auth_client.patch(
        f"/api/v1/masters/projects/{pid}", json={"status": "closed", "version": project.version}
    )
    assert response.status_code == 200
    await db_session.refresh(project)
    frozen_bytes = project.closed_pnl_json
    quote.rate = Decimal("2")
    await db_session.commit()
    pnl = (await auth_client.get(f"/api/v1/masters/projects/{pid}/pnl")).json()
    assert pnl["basis"] == "net_eur_frozen" and pnl["revenue"] == "67.50"
    assert pnl["adjustments"]["revenue"] == "-30.00"
    assert all(Decimal(row["rate"]) == Decimal("1.111111") for row in pnl["fx_trace"])
    await db_session.refresh(project)
    assert project.closed_pnl_json == frozen_bytes


@pytest.mark.asyncio
async def test_foreign_received_allocation_conserves_converted_cents(auth_client, db_session):
    from app.models.invoice import Invoice
    from tests.test_project_allocation import _invoice
    from tests.test_project_profitability import _org

    projects = [await _project(auth_client, code=f"FX-{i}") for i in range(3)]
    org = await _org(db_session)
    invoice_id, lines = await _invoice(
        db_session, org, subtotal="0.03", number="Synthetic FX cents", lines=("0.01", "0.01")
    )
    invoice = await db_session.get(Invoice, invoice_id)
    invoice.currency = "USD"
    invoice.fx_rate = Decimal("2")
    invoice.total_eur = Decimal("0.02")
    invoice.fx_source = "stated"
    await db_session.commit()
    response = await auth_client.put(
        f"/api/v1/invoices/{invoice_id}/allocation",
        json={
            "project_id": projects[2],
            "lines": {lines[0]: projects[0], lines[1]: projects[1]},
        },
    )
    assert response.status_code == 200
    results = [
        (await auth_client.get(f"/api/v1/masters/projects/{pid}/pnl")).json() for pid in projects
    ]
    assert all(row["complete"] for row in results)
    assert sum((Decimal(row["costs"]) for row in results), Decimal("0")) == Decimal("0.02")
    by_id = dict(zip(projects, results, strict=True))
    assert by_id[max(projects)]["costs"] == "0.00"
    assert all(row["fx_trace"][0]["fx_source"] == "stated" for row in results)
    bulk = (await auth_client.get("/api/v1/masters/projects-pnl-summary")).json()
    assert sum((Decimal(row["costs"]) for row in bulk), Decimal("0")) == Decimal("0.02")


@pytest.mark.asyncio
async def test_expense_report_currency_and_manual_reversal(auth_client, db_session):
    from sqlalchemy import select

    from app.models.expense import ExpenseItem, ExpenseReport
    from app.models.user import User
    from tests.test_project_profitability import _org

    pid = await _project(auth_client)
    org = await _org(db_session)
    user = await db_session.scalar(select(User.id).where(User.org_id == org))
    db_session.add(EcbRate(rate_date=date(2026, 10, 1), currency="USD", rate=Decimal("2")))
    report = ExpenseReport(
        org_id=org,
        employee_id=user,
        employee_name="Synthetic Owner",
        title="Synthetic report",
        status="approved",
        currency="USD",
    )
    db_session.add(report)
    await db_session.flush()
    db_session.add(
        ExpenseItem(
            org_id=org,
            report_id=report.id,
            project_id=pid,
            spend_date=date(2026, 10, 1),
            description="Synthetic expense",
            amount=Decimal("120"),
            vat_amount=Decimal("20"),
        )
    )
    await db_session.commit()
    response = await auth_client.post(
        f"/api/v1/masters/projects/{pid}/cost-entries",
        json={"label": "Correction", "amount": "-10.00"},
    )
    assert response.status_code == 201
    pnl = (await auth_client.get(f"/api/v1/masters/projects/{pid}/pnl")).json()
    assert pnl["expense_costs"] == "50.00" and pnl["manual_costs"] == "-10.00"
    assert pnl["costs"] == "40.00" and pnl["profit"] == "-40.00" and pnl["margin_pct"] is None


@pytest.mark.asyncio
async def test_archive_keeps_frozen_evidence_until_explicit_reopen(auth_client, db_session):
    await _issuer_ready(auth_client)
    pid = await _project(auth_client)
    await issue(auth_client, pid, "EUR")

    async def transition(status):
        await db_session.refresh(project)
        response = await auth_client.patch(
            f"/api/v1/masters/projects/{pid}", json={"status": status, "version": project.version}
        )
        assert response.status_code == 200
        await db_session.refresh(project)

    project = await db_session.get(Project, pid)
    await transition("closed")
    snapshot = project.closed_pnl_json
    await transition("archived")
    assert project.closed_pnl_json == snapshot
    pnl = (await auth_client.get(f"/api/v1/masters/projects/{pid}/pnl")).json()
    assert pnl["basis"] == "net_eur_frozen" and pnl["revenue"] == "100.00"
    await transition("active")
    assert project.closed_pnl_json is None


@pytest.mark.asyncio
async def test_unverified_legacy_receipt_conversion_is_not_a_zero_cost(auth_client, db_session):
    from sqlalchemy import select

    from app.models.expense import ExpenseItem, ExpenseReport
    from app.models.user import User
    from tests.test_project_profitability import _org

    pid = await _project(auth_client)
    org = await _org(db_session)
    user = await db_session.scalar(select(User.id).where(User.org_id == org))
    report = ExpenseReport(
        org_id=org,
        employee_id=user,
        employee_name="Synthetic Owner",
        title="Legacy report",
        status="approved",
        currency="EUR",
    )
    db_session.add(report)
    await db_session.flush()
    db_session.add(
        ExpenseItem(
            org_id=org,
            report_id=report.id,
            project_id=pid,
            spend_date=date(2026, 10, 1),
            description="Unconverted legacy receipt",
            amount=Decimal("0"),
            vat_amount=Decimal("0"),
            currency="USD",
            original_amount=Decimal("100"),
            fx_source="unknown",
        )
    )
    await db_session.commit()
    pnl = (await auth_client.get(f"/api/v1/masters/projects/{pid}/pnl")).json()
    assert pnl["complete"] is False and pnl["costs"] is None and pnl["profit"] is None
    assert pnl["issues"][0]["code"] == "unverified_receipt_conversion"
    assert pnl["fx_trace"][0]["amount_eur"] is None
    plan = (await auth_client.get(f"/api/v1/masters/projects/{pid}/invoicing-plan")).json()
    assert plan["complete"] is True and plan["issued_total"] == "0.00"


@pytest.mark.parametrize("stored", ["[]", '{"basis_version":2,"complete":true}'])
@pytest.mark.asyncio
async def test_invalid_legacy_snapshot_does_not_break_other_project_reports(
    auth_client, db_session, stored
):
    pid = await _project(auth_client)
    good = await _project(auth_client, code="SYNTH-GOOD")
    project = await db_session.get(Project, pid)
    project.status = "closed"
    project.closed_pnl_json = stored
    await db_session.commit()
    response = await auth_client.get("/api/v1/masters/projects-pnl-summary")
    assert response.status_code == 200
    rows = {row["project_id"]: row for row in response.json()}
    assert rows[pid]["complete"] is False and rows[pid]["profit"] is None
    assert rows[good]["complete"] is True and rows[good]["profit"] == "0.00"
    await db_session.refresh(project)
    assert project.closed_pnl_json == stored
