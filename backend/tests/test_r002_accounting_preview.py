"""R002: an explicit approved population, true currency basis and frozen download."""

import csv
import io

from app.models.invoice import Invoice, WorkflowState
from app.models.issuer import IssuerProfile
from app.services import accounting_preview


async def setup(auth_client):
    response = await auth_client.get("/api/v1/issuer/registry")
    assert response.status_code == 200, response.text
    entities = response.json()
    if not entities:
        response = await auth_client.get("/api/v1/issuer")
        assert response.status_code == 200
        entity = response.json()["id"]
    else:
        entity = entities[0]["id"]
    return {
        "entity_id": entity,
        "date_from": "2026-05-01",
        "date_to": "2026-05-31",
        "currency": "EUR",
    }


async def invoice(
    auth_client,
    db_session,
    scope,
    number="BILL-1",
    *,
    approved=True,
    currency="EUR",
    fx_rate=None,
    issue_date="2026-05-10",
    assigned=True,
    tax_rate="21",
):
    response = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Synthetic Supplier",
            "invoice_number": number,
            "issue_date": issue_date,
            "currency": currency,
            "fx_rate": fx_rate,
            "line_items": [
                {
                    "description": "Service",
                    "quantity": "1",
                    "unit_price": "100",
                    "tax_rate": tax_rate,
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    row = await db_session.get(Invoice, response.json()["id"])
    row.legal_entity_id = scope["entity_id"] if assigned else None
    row.workflow_state = WorkflowState.approved if approved else WorkflowState.draft
    await db_session.commit()
    return row


async def preview(auth_client, scope):
    response = await auth_client.post("/api/v1/export/accounting/preview", json=scope)
    assert response.status_code == 200, response.text
    return response.json()


async def download(auth_client, snapshot):
    response = await auth_client.post(
        "/api/v1/export/accounting/download", json={"token": snapshot["token"]}
    )
    assert response.status_code == 200, response.text
    return response, list(csv.DictReader(io.StringIO(response.text)))


async def test_generic_v2_names_gross_eur_and_keeps_original_net_tax(auth_client, db_session):
    scope = await setup(auth_client)
    await invoice(auth_client, db_session, scope)
    snapshot = await preview(auth_client, scope)
    response, rows = await download(auth_client, snapshot)
    assert response.headers["x-export-contract"] == "generic-v2"
    assert "invoices-generic-v2.csv" in response.headers["content-disposition"]
    assert "Net EUR" not in rows[0]
    assert rows[0]["Net"] == "100.00"
    assert rows[0]["VAT"] == "21.00"
    assert rows[0]["Gross"] == rows[0]["Gross EUR"] == "121.00"
    assert snapshot["gross"] == snapshot["gross_eur"] == "121.00"


async def test_foreign_conversion_is_gross_and_missing_conversion_is_explicit(
    auth_client, db_session
):
    scope = await setup(auth_client)
    scope["currency"] = "USD"
    row = await invoice(
        auth_client, db_session, scope, currency="USD", fx_rate="1.20", tax_rate="20"
    )
    snapshot = await preview(auth_client, scope)
    _, rows = await download(auth_client, snapshot)
    assert rows[0]["Net"] == "100.00" and rows[0]["VAT"] == "20.00"
    assert rows[0]["Gross"] == "120.00" and rows[0]["Currency"] == "USD"
    assert rows[0]["Gross EUR"] == "100.00"
    row.total_eur, row.fx_source, row.fx_rate = None, "unknown", None
    await db_session.commit()
    snapshot = await preview(auth_client, scope)
    _, rows = await download(auth_client, snapshot)
    assert rows[0]["Gross EUR"] == ""
    assert rows[0]["Conversion Status"] == "unavailable"
    assert snapshot["missing_fx"] == 1 and snapshot["gross_eur"] is None


async def test_preview_excludes_drafts_other_period_currency_and_unassigned_rows(
    auth_client, db_session
):
    scope = await setup(auth_client)
    await invoice(auth_client, db_session, scope, "INCLUDE")
    await invoice(auth_client, db_session, scope, "DRAFT", approved=False)
    await invoice(auth_client, db_session, scope, "OTHER-MONTH", issue_date="2026-06-01")
    await invoice(auth_client, db_session, scope, "OTHER-CURRENCY", currency="USD", fx_rate="1.20")
    await invoice(auth_client, db_session, scope, "UNASSIGNED", assigned=False)
    snapshot = await preview(auth_client, scope)
    assert snapshot["included"] == 1
    assert snapshot["excluded_unapproved"] == 1
    assert snapshot["excluded_other_currencies"] == 1
    assert snapshot["unassigned_in_period"] == 1
    _, rows = await download(auth_client, snapshot)
    assert [r["Invoice Number"] for r in rows] == ["INCLUDE"]


async def test_list_filters_and_frozen_download_preserve_the_reviewed_population(
    auth_client, db_session
):
    scope = await setup(auth_client)
    row = await invoice(auth_client, db_session, scope, "MATCH")
    await invoice(auth_client, db_session, scope, "OTHER")
    scope["q"] = "MATCH"
    snapshot = await preview(auth_client, scope)
    assert snapshot["included"] == 1 and snapshot["excluded_filters"] == 1
    before, _ = await download(auth_client, snapshot)
    row.invoice_number = "EDITED-AFTER-PREVIEW"
    await db_session.commit()
    after, rows = await download(auth_client, snapshot)
    assert after.content == before.content
    assert rows[0]["Invoice Number"] == "MATCH"


async def test_preview_cannot_export_another_legal_entity(auth_client, db_session):
    scope = await setup(auth_client)
    row = await invoice(auth_client, db_session, scope)
    original = await db_session.get(IssuerProfile, scope["entity_id"])
    another = IssuerProfile(
        org_id=original.org_id,
        legal_name="Other synthetic entity",
        invoice_prefix="OTHER",
        credit_note_prefix="OCN",
    )
    db_session.add(another)
    await db_session.flush()
    row.legal_entity_id = another.id
    await db_session.commit()
    snapshot = await preview(auth_client, scope)
    assert snapshot["included"] == 0 and snapshot["token"] is None


async def test_empty_tampered_and_expired_snapshots_are_safe(auth_client, db_session, monkeypatch):
    scope = await setup(auth_client)
    empty = await preview(auth_client, scope)
    assert empty["included"] == 0 and empty["token"] is None
    await invoice(auth_client, db_session, scope)
    snapshot = await preview(auth_client, scope)
    tampered = await auth_client.post(
        "/api/v1/export/accounting/download", json={"token": "A" + snapshot["token"]}
    )
    assert tampered.status_code == 422
    monkeypatch.setattr(accounting_preview.time, "time", lambda: snapshot["expires_at"] + 1)
    expired = await auth_client.post(
        "/api/v1/export/accounting/download", json={"token": snapshot["token"]}
    )
    assert expired.status_code == 409
    assert expired.json()["code"] == "export_preview_expired"


async def test_adapters_and_legacy_download_explain_their_blockers(auth_client):
    formats = (await auth_client.get("/api/v1/export/formats")).json()
    assert {r["key"]: r["enabled"] for r in formats} == {
        "generic": True,
        "xero": False,
        "quickbooks": False,
    }
    for fmt in ("xero", "quickbooks"):
        response = await auth_client.get(f"/api/v1/export/accounting?fmt={fmt}")
        assert response.status_code == 409
        assert response.json()["code"] == "export_adapter_unconfigured"
    response = await auth_client.get("/api/v1/export/accounting?fmt=generic")
    assert response.status_code == 409 and response.json()["code"] == "export_preview_required"


async def test_formula_injection_is_still_neutralized(auth_client, db_session):
    scope = await setup(auth_client)
    row = await invoice(auth_client, db_session, scope)
    await db_session.refresh(row, attribute_names=["vendor"])
    row.vendor.name = "=SYNTHETIC_FORMULA()"
    await db_session.commit()
    _, rows = await download(auth_client, await preview(auth_client, scope))
    assert rows[0]["Supplier"].startswith("'=")


async def test_snapshot_is_bound_to_its_requester_and_company(auth_client, client, db_session):
    scope = await setup(auth_client)
    await invoice(auth_client, db_session, scope)
    snapshot = await preview(auth_client, scope)
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "organization_name": "Other Company",
            "name": "Other Owner",
            "email": "r002-other@example.com",
            "password": "synthetic-test-password",
        },
    )
    assert response.status_code == 201
    headers = {"Authorization": "Bearer " + response.json()["token"]["access_token"]}
    foreign = await client.post(
        "/api/v1/export/accounting/download", json={"token": snapshot["token"]}, headers=headers
    )
    assert foreign.status_code == 404
    assert "BILL-1" not in foreign.text


async def test_limit_refuses_a_large_population_instead_of_silently_truncating(
    auth_client, db_session
):
    from datetime import date

    from sqlalchemy import insert

    scope = await setup(auth_client)
    template = await invoice(auth_client, db_session, scope)
    await db_session.execute(
        insert(Invoice),
        [
            {
                "org_id": template.org_id,
                "vendor_id": template.vendor_id,
                "invoice_number": f"BOUND-{index}",
                "issue_date": date(2026, 5, 10),
                "currency": "EUR",
                "legal_entity_id": scope["entity_id"],
                "workflow_state": WorkflowState.approved,
            }
            for index in range(1000)
        ],
    )
    await db_session.commit()
    response = await auth_client.post("/api/v1/export/accounting/preview", json=scope)
    assert response.status_code == 422
    assert response.json()["code"] == "export_preview_too_large"
