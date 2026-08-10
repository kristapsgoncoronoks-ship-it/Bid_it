"""WO-76 — transport routes slice 1: the claim lifecycle over HTTP.

Proves the nine `api/routes/transport/claims.py` endpoints end to end: the
R1 idempotent create, list/detail reads, R2-grain line materialization +
read, the advisory checklist/stage reads (§4.19 — reading changes NOTHING),
the D5 submit gate chain surfacing the SERVICE's refusal codes with nothing
mutated, the R5 withdraw, the structural permission matrix live on the wire
(EMPLOYEE denied read; ACCOUNTANT granted write, denied submit — the WO-49
VAT_WRITE/VAT_SUBMIT split), the module entitlement (`module_not_enabled`
on every route, ADR-P3 rule 3) and the §4.4 opaque cross-tenant 404.

Fixture strategy: orgs/tokens ride the real HTTP register flow (the
tenancy-parity convention); transport enablement is direct service setup
(`modules.set_enabled` — the module is deliberately in no billing plan yet,
so the HTTP toggle would 402); entities/transactions/invoices seed through
the same conftest helpers the service suites use. Every assertion about
what the API DID goes through the API; DB reads only verify the
nothing-mutated / lock-released internals a client cannot see.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.models.transport.lock import VatClaimedInvoice
from app.models.transport.vat_claim import VatRefundClaimLine
from app.services import modules
from app.services.transport import fuel_ingest
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import (
    activate_entity,
    make_entity,
    register_documented_invoice,
    seed_fee_rate,
)

V = "/api/v1"


# --------------------------------------------------------------------------- #
# HTTP org / member helpers
# --------------------------------------------------------------------------- #


async def _register_org(client: AsyncClient):
    """A fresh org over the real register flow -> (headers, org_id)."""
    suffix = uuid.uuid4().hex[:8]
    r = await client.post(
        f"{V}/auth/register",
        json={
            "organization_name": f"WO76 Org {suffix}",
            "name": "Owner",
            "email": f"owner-{suffix}@wo76.example.io",
            "password": "supersecret",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    headers = {"Authorization": f"Bearer {body['token']['access_token']}"}
    org_id = body["organization"]["id"]
    return headers, org_id


async def _enable_transport(db_session, org_id: str) -> None:
    # Direct service enablement: `transport` is deliberately in no PLANS set
    # yet (a commercial decision, docs/DECISIONS-NEEDED.md), so the HTTP
    # module toggle would 402. Setup only — the paths under test stay HTTP.
    await modules.set_enabled(db_session, org_id, "transport", True)
    # WO-95 (G2.9/R13): raised past the fee gate — `submit_claim` now freezes
    # a contingency fee and REFUSES (`fee_rate_not_configured`) when no rate
    # is configured, so a submit route test would otherwise 409 on a
    # commercial setting rather than exercise the path under test. Seeded as
    # a row, not through `fee.set_rate`, so no audit event enters the window
    # this file's trail assertions read (the `activate_entity` convention).
    await seed_fee_rate(db_session, org_id)


async def _member_with_role(client: AsyncClient, owner_headers, db_session, stored_role: str):
    """Invite a member into the OWNER's org, then downgrade the stored role
    (the `role_client` conftest pattern — registration/invites only mint
    admin-tier accounts). Same org, so the module entitlement and seeded
    claims are shared; only the permission set differs."""
    from app.models.user import User, UserRole

    email = f"{stored_role}-{uuid.uuid4().hex[:8]}@wo76.example.io"
    inv = await client.post(
        f"{V}/team/invites", headers=owner_headers, json={"email": email, "role": "admin"}
    )
    assert inv.status_code == 201, inv.text
    acc = await client.post(
        f"{V}/auth/accept-invite",
        json={"token": inv.json()["token"], "name": "Member", "password": "supersecret"},
    )
    assert acc.status_code == 201, acc.text
    body = acc.json()
    await db_session.execute(
        update(User)
        .where(User.id == body["user"]["id"])
        .values(role=UserRole(stored_role), is_expense_approver=False)
    )
    await db_session.commit()
    return {"Authorization": f"Bearer {body['token']['access_token']}"}


# --------------------------------------------------------------------------- #
# Domain seeding helpers (service-level, the WO-75 suite's shapes)
# --------------------------------------------------------------------------- #


async def _make_txn(
    db_session,
    org_id: str,
    entity_id: str,
    *,
    supplier: str = "Q8",
    invoice_ref: str | None = "INV-0001",
    period: str = "2026-05",
    txn_date: date = date(2026, 5, 15),
    line_seq: int = 1,
    net_eur: Decimal = Decimal("2000.00"),
    vat_eur: Decimal = Decimal("420.00"),
):
    return await fuel_ingest.ingest_transaction(
        db_session,
        org_id,
        entity_id=entity_id,
        supplier=supplier,
        period=period,
        line_seq=line_seq,
        country="LV",
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=txn_date,
        station="Demo Station Riga",
        product="DIESEL",
        qty=Decimal("100.000"),
        currency="EUR",
        net_local=net_eur,
        vat_local=vat_eur,
        gross_local=net_eur + vat_eur,
        net_eur=net_eur,
        vat_eur=vat_eur,
        invoice_ref=invoice_ref,
    )


async def _create_claim_http(
    client, headers, entity_id: str, *, refund_country: str = "LV", ref_period: str = "2026-Q2"
) -> dict:
    r = await client.post(
        f"{V}/transport/claims",
        headers=headers,
        json={"entity_id": entity_id, "refund_country": refund_country, "ref_period": ref_period},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _submit_ready_claim(client, db_session, headers, org_id, *, activated: bool = True):
    """Everything the D5 chain needs to PASS: an activated entity (R44), a
    registered + vaulted invoice (R3/R10), one resolvable transaction above
    the Art. 17 minimum (R8), lines built over HTTP. Returns (claim, txn)."""
    entity = await make_entity(db_session, org_id)
    if activated:
        await activate_entity(db_session, org_id, entity.id, "LV")
    await register_documented_invoice(
        db_session, org_id, supplier="Q8", invoice_number="INV-0001", country="LV"
    )
    txn = await _make_txn(db_session, org_id, entity.id)
    txn_id = txn.id  # captured NOW — expire_all() later would detach lazy access
    await db_session.commit()
    claim = await _create_claim_http(client, headers, entity.id)
    built = await client.post(f"{V}/transport/claims/{claim['id']}/lines", headers=headers)
    assert built.status_code == 200, built.text
    return claim, txn_id


def _submit_body(
    txn_id: str, *, supplier: str = "Q8", invoice_ref: str = "INV-0001", **extra
) -> dict:
    return {
        "invoices": [
            {"supplier": supplier, "invoice_ref": invoice_ref, "fuel_transaction_id": txn_id}
        ],
        **extra,
    }


async def _assert_nothing_mutated(db_session, client, headers, claim_id: str) -> None:
    """The D5 proof over HTTP + the internals a client cannot see: status
    still draft on the wire; zero lock rows, zero frozen lines, no frozen
    base in the DB."""
    detail = await client.get(f"{V}/transport/claims/{claim_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "draft"
    assert body["status_code"] is None
    assert body["vat_eur"] is None
    db_session.expire_all()
    locks = list(
        await db_session.scalars(
            select(VatClaimedInvoice).where(VatClaimedInvoice.claim_id == claim_id)
        )
    )
    assert locks == []
    frozen = list(
        await db_session.scalars(
            select(VatRefundClaimLine).where(
                VatRefundClaimLine.claim_id == claim_id,
                VatRefundClaimLine.frozen_at.is_not(None),
            )
        )
    )
    assert frozen == []


# --------------------------------------------------------------------------- #
# Create / list / detail
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo76_create_claim_is_idempotent_on_the_grain(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id)
    await db_session.commit()

    first = await _create_claim_http(client, headers, entity.id)
    second = await _create_claim_http(client, headers, entity.id)
    assert first["id"] == second["id"]  # R1 — same key, same row, never a duplicate
    assert first["status"] == "draft"
    assert first["refund_country"] == "LV" and first["ref_period"] == "2026-Q2"

    listing = await client.get(f"{V}/transport/claims", headers=headers)
    assert listing.status_code == 200, listing.text
    assert [c["id"] for c in listing.json()] == [first["id"]]


@pytest.mark.asyncio
async def test_wo76_create_claim_unknown_entity_is_404_and_bad_period_is_422(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id)
    await db_session.commit()

    r = await client.post(
        f"{V}/transport/claims",
        headers=headers,
        json={"entity_id": str(uuid.uuid4()), "refund_country": "LV", "ref_period": "2026-Q2"},
    )
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "entity_not_found"

    r = await client.post(
        f"{V}/transport/claims",
        headers=headers,
        json={"entity_id": entity.id, "refund_country": "LV", "ref_period": "2026-13"},
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "invalid_period"


@pytest.mark.asyncio
async def test_wo76_detail_reads_back_the_claim(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id)
    await db_session.commit()
    claim = await _create_claim_http(client, headers, entity.id)

    detail = await client.get(f"{V}/transport/claims/{claim['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["id"] == claim["id"]
    assert body["entity_id"] == entity.id
    assert body["status"] == "draft" and body["vat_eur"] is None


# --------------------------------------------------------------------------- #
# Line materialization + read
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo76_build_lines_then_read_them(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    claim, _txn_id = await _submit_ready_claim(client, db_session, headers, org_id)

    built = await client.post(f"{V}/transport/claims/{claim['id']}/lines", headers=headers)
    assert built.status_code == 200, built.text
    lines = built.json()
    assert len(lines) == 1
    line = lines[0]
    # R2 grain + R11 code + §4.9 Decimal-as-string on the wire (never float).
    assert line["invoice_ref"] == "INV-0001"
    assert line["product_group"] == "Diesel"
    assert line["goods_code"] == "1"
    assert line["net_eur"] == "2000.00"
    assert line["vat_eur"] == "420.00"
    assert line["frozen_at"] is None

    read = await client.get(f"{V}/transport/claims/{claim['id']}/lines", headers=headers)
    assert read.status_code == 200, read.text
    assert [ln["id"] for ln in read.json()] == [line["id"]]


# --------------------------------------------------------------------------- #
# Checklist + stage (advisory reads)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo76_checklist_and_stage_read_are_advisory_and_mutate_nothing(client, db_session):
    """§4.19 live on the wire: a failing checklist is NAMED, and reading it
    (or the stage) changes nothing about the claim."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id)
    # No registered invoice: the Q8 transaction stays unresolved -> the
    # receipt-control item fails (Q8 is waivable — it has no registered
    # invoice at all for LV).
    txn = await _make_txn(db_session, org_id, entity.id)
    assert txn is not None
    await db_session.commit()
    claim = await _create_claim_http(client, headers, entity.id)

    cl = await client.get(f"{V}/transport/claims/{claim['id']}/checklist", headers=headers)
    assert cl.status_code == 200, cl.text
    items = {i["key"]: i for i in cl.json()}
    assert {"customer_data", "bank_account", "receipt_control", "unresolved_refs"} <= set(items)
    assert items["receipt_control"]["ok"] is False
    assert "Q8" in (items["receipt_control"]["reason"] or "")

    stage = await client.get(f"{V}/transport/claims/{claim['id']}/stage", headers=headers)
    assert stage.status_code == 200, stage.text
    assert stage.json() == {"stage": "1A"}  # a non-period item fails -> 1A (D3)

    # The advisory reads changed NOTHING about the claim.
    await _assert_nothing_mutated(db_session, client, headers, claim["id"])


@pytest.mark.asyncio
async def test_wo76_stage_reaches_1e_when_ready(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    claim, _txn_id = await _submit_ready_claim(client, db_session, headers, org_id)

    stage = await client.get(f"{V}/transport/claims/{claim['id']}/stage", headers=headers)
    assert stage.status_code == 200, stage.text
    assert stage.json() == {"stage": "1E"}


# --------------------------------------------------------------------------- #
# Submit — the D5 chain over HTTP
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo76_submit_happy_path_freezes_locks_and_flips_status(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    claim, txn_id = await _submit_ready_claim(client, db_session, headers, org_id)

    r = await client.post(
        f"{V}/transport/claims/{claim['id']}/submit", headers=headers, json=_submit_body(txn_id)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "submitted"
    assert body["status_code"] == "2"  # the ONLY manually-reachable first code (R17)
    assert body["vat_eur"] == "420.00"  # frozen over exactly the claim's lines (G2.5)

    db_session.expire_all()
    locks = list(
        await db_session.scalars(
            select(VatClaimedInvoice).where(VatClaimedInvoice.claim_id == claim["id"])
        )
    )
    assert [(lk.supplier, lk.invoice_ref) for lk in locks] == [("Q8", "INV-0001")]
    frozen = list(
        await db_session.scalars(
            select(VatRefundClaimLine).where(
                VatRefundClaimLine.claim_id == claim["id"],
                VatRefundClaimLine.frozen_at.is_not(None),
            )
        )
    )
    assert len(frozen) == 1


@pytest.mark.asyncio
async def test_wo76_submit_refusal_period_not_ended_mutates_nothing(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id)
    await db_session.commit()
    claim = await _create_claim_http(client, headers, entity.id, ref_period="2027-Q4")

    r = await client.post(
        f"{V}/transport/claims/{claim['id']}/submit",
        headers=headers,
        json={
            "invoices": [{"supplier": "Q8", "invoice_ref": "INV-0001", "fuel_transaction_id": "x"}]
        },
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "period_not_ended"
    await _assert_nothing_mutated(db_session, client, headers, claim["id"])


@pytest.mark.asyncio
async def test_wo76_submit_refusal_customer_not_active(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    claim, txn_id = await _submit_ready_claim(client, db_session, headers, org_id, activated=False)

    r = await client.post(
        f"{V}/transport/claims/{claim['id']}/submit", headers=headers, json=_submit_body(txn_id)
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "customer_not_active"  # R44 fails CLOSED on absence
    await _assert_nothing_mutated(db_session, client, headers, claim["id"])


@pytest.mark.asyncio
async def test_wo76_submit_refusal_unresolved_invoice_refs(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id)
    await activate_entity(db_session, org_id, entity.id, "LV")
    # NO registered invoice: the built line is UNMATCHED (R3's subject).
    txn = await _make_txn(db_session, org_id, entity.id)
    txn_id = txn.id
    await db_session.commit()
    claim = await _create_claim_http(client, headers, entity.id)
    built = await client.post(f"{V}/transport/claims/{claim['id']}/lines", headers=headers)
    assert built.status_code == 200, built.text
    assert built.json()[0]["invoice_ref"] == "UNMATCHED"

    r = await client.post(
        f"{V}/transport/claims/{claim['id']}/submit", headers=headers, json=_submit_body(txn_id)
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "unresolved_invoice_refs"
    assert "UNMATCHED" in r.json()["detail"]
    await _assert_nothing_mutated(db_session, client, headers, claim["id"])


@pytest.mark.asyncio
async def test_wo76_submit_refusal_below_minimum_then_explicit_override_succeeds(
    client, db_session
):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id)
    await activate_entity(db_session, org_id, entity.id, "LV")
    await register_documented_invoice(
        db_session, org_id, supplier="Q8", invoice_number="INV-0001", country="LV"
    )
    txn = await _make_txn(
        db_session, org_id, entity.id, net_eur=Decimal("10.00"), vat_eur=Decimal("1.00")
    )
    txn_id = txn.id
    await db_session.commit()
    claim = await _create_claim_http(client, headers, entity.id)
    built = await client.post(f"{V}/transport/claims/{claim['id']}/lines", headers=headers)
    assert built.status_code == 200, built.text

    r = await client.post(
        f"{V}/transport/claims/{claim['id']}/submit", headers=headers, json=_submit_body(txn_id)
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "below_minimum"  # EUR 1.00 < the EUR 400 quarterly floor
    await _assert_nothing_mutated(db_session, client, headers, claim["id"])

    # R8's explicit admin override — never silent: recorded in status_note.
    r2 = await client.post(
        f"{V}/transport/claims/{claim['id']}/submit",
        headers=headers,
        json=_submit_body(txn_id, override_minimum=True),
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "submitted"
    assert "Art. 17 minimum override" in (r2.json()["status_note"] or "")


@pytest.mark.asyncio
async def test_wo76_submit_refusal_invoice_document_missing(client, db_session):
    """A registered-but-undocumented invoice: the line RESOLVES (past R3) and
    then fails R10 — proving the doc gate's code surfaces distinctly."""
    from app.models.invoice import Invoice
    from app.models.vendor import Vendor

    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id)
    await activate_entity(db_session, org_id, entity.id, "LV")
    vendor = Vendor(org_id=org_id, name="Q8", country="LV")
    db_session.add(vendor)
    await db_session.flush()
    db_session.add(
        Invoice(
            org_id=org_id,
            vendor_id=vendor.id,
            invoice_number="INV-0002",
            issue_date=date(2026, 5, 1),
            currency="EUR",
            subtotal=Decimal("2000.00"),
            tax_amount=Decimal("420.00"),
            total=Decimal("2420.00"),
        )
    )
    txn = await _make_txn(db_session, org_id, entity.id, invoice_ref="INV-0002")
    txn_id = txn.id
    await db_session.commit()
    claim = await _create_claim_http(client, headers, entity.id)
    built = await client.post(f"{V}/transport/claims/{claim['id']}/lines", headers=headers)
    assert built.status_code == 200, built.text
    assert built.json()[0]["invoice_ref"] == "INV-0002"  # resolved, not UNMATCHED

    r = await client.post(
        f"{V}/transport/claims/{claim['id']}/submit",
        headers=headers,
        json=_submit_body(txn_id, invoice_ref="INV-0002"),
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "invoice_document_missing"
    await _assert_nothing_mutated(db_session, client, headers, claim["id"])


@pytest.mark.asyncio
async def test_wo76_submit_refusal_duplicate_invoice_lock(client, db_session):
    """R6 over HTTP: a quarterly claim overlapping an invoice another claim
    already locked refuses the WHOLE submission."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id)
    await activate_entity(db_session, org_id, entity.id, "LV")
    await register_documented_invoice(
        db_session, org_id, supplier="Q8", invoice_number="INV-0001", country="LV"
    )
    txn_q1 = await _make_txn(
        db_session, org_id, entity.id, period="2026-02", txn_date=date(2026, 2, 10)
    )
    txn_q2 = await _make_txn(
        db_session, org_id, entity.id, period="2026-05", txn_date=date(2026, 5, 15), line_seq=2
    )
    txn_q1_id, txn_q2_id = txn_q1.id, txn_q2.id
    await db_session.commit()

    claim_q1 = await _create_claim_http(client, headers, entity.id, ref_period="2026-Q1")
    built = await client.post(f"{V}/transport/claims/{claim_q1['id']}/lines", headers=headers)
    assert built.status_code == 200, built.text
    first = await client.post(
        f"{V}/transport/claims/{claim_q1['id']}/submit",
        headers=headers,
        json=_submit_body(txn_q1_id),
    )
    assert first.status_code == 200, first.text

    claim_q2 = await _create_claim_http(client, headers, entity.id, ref_period="2026-Q2")
    built = await client.post(f"{V}/transport/claims/{claim_q2['id']}/lines", headers=headers)
    assert built.status_code == 200, built.text
    r = await client.post(
        f"{V}/transport/claims/{claim_q2['id']}/submit",
        headers=headers,
        json=_submit_body(txn_q2_id),
    )
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "duplicate_invoice_lock"
    await _assert_nothing_mutated(db_session, client, headers, claim_q2["id"])


@pytest.mark.asyncio
async def test_wo76_submit_empty_invoice_set_is_422_at_the_boundary(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id)
    await db_session.commit()
    claim = await _create_claim_http(client, headers, entity.id)

    r = await client.post(
        f"{V}/transport/claims/{claim['id']}/submit", headers=headers, json={"invoices": []}
    )
    assert r.status_code == 422, r.text  # schema min_length=1 (shape at the boundary)


# --------------------------------------------------------------------------- #
# Withdraw
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo76_withdraw_releases_the_locks(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    claim, txn_id = await _submit_ready_claim(client, db_session, headers, org_id)
    sub = await client.post(
        f"{V}/transport/claims/{claim['id']}/submit", headers=headers, json=_submit_body(txn_id)
    )
    assert sub.status_code == 200, sub.text

    r = await client.post(f"{V}/transport/claims/{claim['id']}/withdraw", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "withdrawn"
    db_session.expire_all()
    locks = list(
        await db_session.scalars(
            select(VatClaimedInvoice).where(VatClaimedInvoice.claim_id == claim["id"])
        )
    )
    assert locks == []  # R5 — withdraw is the ONE lock-releasing transition


@pytest.mark.asyncio
async def test_wo76_withdraw_on_a_draft_is_409_claim_not_locked(client, db_session):
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id)
    await db_session.commit()
    claim = await _create_claim_http(client, headers, entity.id)

    r = await client.post(f"{V}/transport/claims/{claim['id']}/withdraw", headers=headers)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "claim_not_locked"


# --------------------------------------------------------------------------- #
# Module gate / authorization / tenancy
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo76_module_disabled_is_403_module_not_enabled_on_every_route(client, db_session):
    """ADR-P3 rule 3: an org that has not activated the vertical gets 403
    with the SERVICE's `module_not_enabled` code on all nine routes — the
    owner holds every permission, so this is purely the entitlement."""
    headers, _org_id = await _register_org(client)  # transport NOT enabled
    some_id = str(uuid.uuid4())
    submit_body = {
        "invoices": [{"supplier": "Q8", "invoice_ref": "INV-0001", "fuel_transaction_id": "x"}]
    }
    calls = [
        ("GET", f"{V}/transport/claims", None),
        (
            "POST",
            f"{V}/transport/claims",
            {"entity_id": some_id, "refund_country": "LV", "ref_period": "2026-Q2"},
        ),
        ("GET", f"{V}/transport/claims/{some_id}", None),
        ("GET", f"{V}/transport/claims/{some_id}/lines", None),
        ("POST", f"{V}/transport/claims/{some_id}/lines", None),
        ("GET", f"{V}/transport/claims/{some_id}/checklist", None),
        ("GET", f"{V}/transport/claims/{some_id}/stage", None),
        ("POST", f"{V}/transport/claims/{some_id}/submit", submit_body),
        ("POST", f"{V}/transport/claims/{some_id}/withdraw", None),
    ]
    for method, path, body in calls:
        if method == "GET":
            r = await client.get(path, headers=headers)
        else:
            r = await client.post(path, headers=headers, json=body)
        assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text}"
        assert r.json()["code"] == "module_not_enabled", f"{method} {path}: {r.text}"


@pytest.mark.asyncio
async def test_wo76_missing_permission_is_403_structural(client, db_session, role_client):
    """Deny-by-default live on the wire. EMPLOYEE (no VAT_READ) is refused
    the reads; ACCOUNTANT (VAT_WRITE without VAT_SUBMIT — the WO-49 split)
    creates a claim but cannot submit or withdraw it."""
    headers, org_id = await _register_org(client)
    await _enable_transport(db_session, org_id)
    entity = await make_entity(db_session, org_id)
    await db_session.commit()
    claim = await _create_claim_http(client, headers, entity.id)

    # EMPLOYEE (legacy stored role "user") — denied the router-level VAT_READ.
    employee = await role_client("user")
    r = await employee.get(f"{V}/transport/claims")
    assert r.status_code == 403, r.text

    # ACCOUNTANT in the SAME org: granted read+write, denied submit/withdraw.
    acct = await _member_with_role(client, headers, db_session, "accountant")
    r = await client.get(f"{V}/transport/claims", headers=acct)
    assert r.status_code == 200, r.text
    r = await client.post(
        f"{V}/transport/claims",
        headers=acct,
        json={"entity_id": entity.id, "refund_country": "LV", "ref_period": "2026-Q3"},
    )
    assert r.status_code == 200, r.text  # VAT_WRITE granted
    for path in (f"{V}/transport/claims/{claim['id']}/submit",):
        r = await client.post(
            path,
            headers=acct,
            json={
                "invoices": [
                    {"supplier": "Q8", "invoice_ref": "INV-0001", "fuel_transaction_id": "x"}
                ]
            },
        )
        assert r.status_code == 403, r.text  # VAT_SUBMIT denied BEFORE any service work
    r = await client.post(f"{V}/transport/claims/{claim['id']}/withdraw", headers=acct)
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_wo76_cross_tenant_claim_id_is_an_opaque_404_on_every_route(client, db_session):
    """§4.4: org B (transport-enabled, full permissions) probing org A's
    claim id gets 404 — never 403 — on every by-id route."""
    headers_a, org_a = await _register_org(client)
    await _enable_transport(db_session, org_a)
    entity_a = await make_entity(db_session, org_a)
    await db_session.commit()
    claim_a = await _create_claim_http(client, headers_a, entity_a.id)

    headers_b, org_b = await _register_org(client)
    await _enable_transport(db_session, org_b)

    cid = claim_a["id"]
    submit_body = {
        "invoices": [{"supplier": "Q8", "invoice_ref": "INV-0001", "fuel_transaction_id": "x"}]
    }
    calls = [
        ("GET", f"{V}/transport/claims/{cid}", None),
        ("GET", f"{V}/transport/claims/{cid}/lines", None),
        ("POST", f"{V}/transport/claims/{cid}/lines", None),
        ("GET", f"{V}/transport/claims/{cid}/checklist", None),
        ("GET", f"{V}/transport/claims/{cid}/stage", None),
        ("POST", f"{V}/transport/claims/{cid}/submit", submit_body),
        ("POST", f"{V}/transport/claims/{cid}/withdraw", None),
    ]
    for method, path, body in calls:
        if method == "GET":
            r = await client.get(path, headers=headers_b)
        else:
            r = await client.post(path, headers=headers_b, json=body)
        assert r.status_code != 403, f"{method} {path}: 403 confirms the object exists (§4.4)"
        assert r.status_code == 404, f"{method} {path}: {r.status_code} {r.text}"
        assert r.json()["code"] == "claim_not_found", f"{method} {path}: {r.text}"

    # And org B's list shows ZERO of A's claims.
    listing = await client.get(f"{V}/transport/claims", headers=headers_b)
    assert listing.status_code == 200, listing.text
    assert listing.json() == []

    # A's own view is untouched by B's probing.
    detail = await client.get(f"{V}/transport/claims/{cid}", headers=headers_a)
    assert detail.status_code == 200 and detail.json()["status"] == "draft"


@pytest.mark.asyncio
async def test_wo76_list_claims_is_org_scoped_with_overlapping_data(client, db_session):
    """Two orgs, IDENTICAL claim bodies (the parity overlap discipline) —
    each list shows exactly its own row."""
    ids = {}
    for key in ("a", "b"):
        headers, org_id = await _register_org(client)
        await _enable_transport(db_session, org_id)
        entity = await make_entity(db_session, org_id)
        await db_session.commit()
        claim = await _create_claim_http(client, headers, entity.id)
        ids[key] = (headers, claim["id"])
    for mine, other in (("a", "b"), ("b", "a")):
        headers, my_id = ids[mine]
        _, other_id = ids[other]
        listing = await client.get(f"{V}/transport/claims", headers=headers)
        seen = {c["id"] for c in listing.json()}
        assert my_id in seen
        assert other_id not in seen
