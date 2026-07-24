"""INV-5: per-legal-entity numbering (multi-issuer registry), viewed tracking,
payment instructions, and the concurrency + PDF-snapshot guarantees."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.issued_invoice import IssuedInvoice


def _instant(ts: str) -> datetime:
    """Parse an ISO timestamp to a tz-aware UTC datetime (naive → assume UTC)."""
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


ISSUER = {
    "legal_name": "InvoiceIQ Demo BV",
    "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "iban": "NL91ABNA0417164300",
    "bic": "ABNANL2A",
    "email": "billing@invoiceiq.test",
}

# A complete SECOND legal entity with its OWN numbering prefix.
ENTITY_B = {
    **ISSUER,
    "name": "Acme France SARL",
    "legal_name": "Acme France SARL",
    "vat_number": "FR40303265045",
    "country": "FR",
    "invoice_prefix": "ACME-",
    "payment_instructions": "Virement sous 14 jours; référence = numéro de facture.",
}


async def _activate(auth_client):
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})


def _inv(**over):
    body = {
        "buyer_name": "Globex",
        "issue_date": "2026-05-10",
        "lines": [{"description": "x", "quantity": "1", "unit_price": "100.00", "vat_rate": "0"}],
    }
    body.update(over)
    return body


# --- INV-5a: per-legal-entity numbering series ----------------------------------


@pytest.mark.asyncio
async def test_registry_crud_and_default(auth_client):
    await _activate(auth_client)
    reg = (await auth_client.get("/api/v1/issuer/registry")).json()
    assert len(reg) == 1 and reg[0]["is_default"] is True  # the default seeded from PUT /issuer

    made = await auth_client.post("/api/v1/issuer/registry", json=ENTITY_B)
    assert made.status_code == 201, made.text
    bid = made.json()["id"]
    assert made.json()["is_default"] is False and made.json()["invoice_prefix"] == "ACME-"

    # Promote B to default, then the list leads with it.
    assert (await auth_client.post(f"/api/v1/issuer/registry/{bid}/default")).status_code == 200
    reg2 = (await auth_client.get("/api/v1/issuer/registry")).json()
    assert reg2[0]["id"] == bid and reg2[0]["is_default"] is True


@pytest.mark.asyncio
async def test_each_entity_has_its_own_gap_free_series(auth_client):
    await _activate(auth_client)
    bid = (await auth_client.post("/api/v1/issuer/registry", json=ENTITY_B)).json()["id"]

    # Two invoices from the DEFAULT entity, two from entity B — independent series.
    a1 = (await auth_client.post("/api/v1/issued", json=_inv())).json()
    b1 = (await auth_client.post("/api/v1/issued", json=_inv(issuer_id=bid))).json()
    a2 = (await auth_client.post("/api/v1/issued", json=_inv())).json()
    b2 = (await auth_client.post("/api/v1/issued", json=_inv(issuer_id=bid))).json()

    assert a1["number"] == "INV-2026-0001" and a2["number"] == "INV-2026-0002"
    assert b1["number"] == "ACME-2026-0001" and b2["number"] == "ACME-2026-0002"
    assert a1["issuer_id"] != b1["issuer_id"]
    # B's invoice snapshots B's seller identity (read the PDF-source seller).
    assert b1["issuer_id"] == bid


@pytest.mark.asyncio
async def test_cannot_delete_default_or_referenced_issuer(auth_client):
    await _activate(auth_client)
    default_id = (await auth_client.get("/api/v1/issuer/registry")).json()[0]["id"]
    bid = (await auth_client.post("/api/v1/issuer/registry", json=ENTITY_B)).json()["id"]

    # Default can't be deleted.
    assert (await auth_client.delete(f"/api/v1/issuer/registry/{default_id}")).status_code == 409
    # An issuer with invoices can't be deleted.
    await auth_client.post("/api/v1/issued", json=_inv(issuer_id=bid))
    assert (await auth_client.delete(f"/api/v1/issuer/registry/{bid}")).status_code == 409
    # An unused non-default issuer can be deleted.
    cid = (
        await auth_client.post("/api/v1/issuer/registry", json={**ENTITY_B, "invoice_prefix": "C-"})
    ).json()["id"]
    assert (await auth_client.delete(f"/api/v1/issuer/registry/{cid}")).status_code == 204


@pytest.mark.asyncio
async def test_unknown_issuer_is_rejected(auth_client):
    await _activate(auth_client)
    r = await auth_client.post(
        "/api/v1/issued", json=_inv(issuer_id="00000000-0000-0000-0000-000000000000")
    )
    assert r.status_code == 404


# --- INV-5d: concurrency-safe numbering, PER ENTITY -----------------------------


@pytest.mark.asyncio
async def test_per_issuer_numbering_is_monotonic_and_unique(auth_client):
    # INTERLEAVE issues across two entities — each keeps its OWN gap-free,
    # duplicate-free series regardless of the other's activity.
    await _activate(auth_client)
    bid = (await auth_client.post("/api/v1/issuer/registry", json=ENTITY_B)).json()["id"]
    seq = []
    for _ in range(4):
        seq.append((await auth_client.post("/api/v1/issued", json=_inv())).json()["number"])
        seq.append(
            (await auth_client.post("/api/v1/issued", json=_inv(issuer_id=bid))).json()["number"]
        )
    a = [n for n in seq if n.startswith("INV-")]
    b = [n for n in seq if n.startswith("ACME-")]
    assert a == [f"INV-2026-{i:04d}" for i in range(1, 5)]
    assert b == [f"ACME-2026-{i:04d}" for i in range(1, 5)]


@pytest.mark.asyncio
async def test_per_issuer_duplicate_number_rejected_by_db(auth_client, db_session):
    """The concurrency backstop holds PER ENTITY: even if two racing transactions on
    a non-default issuer's series produced the same number, the unique(org, number)
    constraint refuses the second. (True parallelism isn't simulable on the SQLite
    StaticPool harness; this asserts the DB-level guarantee that makes it safe.)"""
    await _activate(auth_client)
    bid = (await auth_client.post("/api/v1/issuer/registry", json=ENTITY_B)).json()["id"]
    inv = (await auth_client.post("/api/v1/issued", json=_inv(issuer_id=bid))).json()
    orig = await db_session.get(IssuedInvoice, inv["id"])
    dup = IssuedInvoice(
        org_id=orig.org_id,
        issuer_id=orig.issuer_id,
        doc_type="invoice",
        number=orig.number,  # same number in the same org → unique-constraint violation
        issue_date=orig.issue_date,
        currency=orig.currency,
        buyer_name="Dup",
        seller_json=orig.seller_json,
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


# --- INV-5b: viewed tracking ----------------------------------------------------


@pytest.mark.asyncio
async def test_mark_viewed_requires_sent_and_is_idempotent(auth_client):
    await _activate(auth_client)
    # Future due date so the delivery state (viewed) isn't outranked by 'overdue'.
    inv = (
        await auth_client.post(
            "/api/v1/issued", json=_inv(buyer_email="ap@globex.io", due_date="2099-01-01")
        )
    ).json()
    iid = inv["id"]

    # Not sent yet → refused.
    assert (await auth_client.post(f"/api/v1/issued/{iid}/mark-viewed")).status_code == 409

    await auth_client.post(f"/api/v1/issued/{iid}/send", json={})
    v1 = await auth_client.post(f"/api/v1/issued/{iid}/mark-viewed")
    assert v1.status_code == 200
    assert v1.json()["status"] == "viewed" and v1.json()["viewed_at"] is not None
    first_ts = v1.json()["viewed_at"]

    # Idempotent: the first view timestamp stands. (Compare the INSTANT, not the raw
    # string — the SQLite test harness drops tz on round-trip, so the second response's
    # DB-read value is naive while the first is tz-aware; both denote the same UTC moment.)
    v2 = await auth_client.post(f"/api/v1/issued/{iid}/mark-viewed")
    assert _instant(v2.json()["viewed_at"]) == _instant(first_ts)


# --- INV-5c: payment instructions on the PDF ------------------------------------


@pytest.mark.asyncio
async def test_payment_instructions_render_on_pdf(auth_client):
    pytest.importorskip("reportlab")
    pytest.importorskip("pypdf")
    import io

    from pypdf import PdfReader

    await _activate(auth_client)
    bid = (await auth_client.post("/api/v1/issuer/registry", json=ENTITY_B)).json()["id"]
    inv = (await auth_client.post("/api/v1/issued", json=_inv(issuer_id=bid))).json()

    pdf = await auth_client.get(f"/api/v1/issued/{inv['id']}/pdf")
    assert pdf.status_code == 200
    text = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "Virement sous 14 jours" in text  # entity B's payment instructions


# --- INV-5d: PDF-snapshot (content matches stored values) -----------------------


@pytest.mark.asyncio
async def test_pdf_content_matches_stored_invoice_values(auth_client):
    pytest.importorskip("reportlab")
    pytest.importorskip("pypdf")
    import io

    from pypdf import PdfReader

    await _activate(auth_client)
    inv = (
        await auth_client.post(
            "/api/v1/issued",
            json=_inv(
                buyer_name="Globex SARL",
                lines=[
                    {
                        "description": "Consulting",
                        "quantity": "2",
                        "unit_price": "100.00",
                        "vat_rate": "21",
                    }
                ],
            ),
        )
    ).json()
    assert inv["total"] == "242.00"

    pdf = await auth_client.get(f"/api/v1/issued/{inv['id']}/pdf")
    text = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf.content)).pages)
    # The rendered PDF reflects the STORED number, buyer and total.
    assert inv["number"] in text
    assert "Globex SARL" in text
    assert "242.00" in text
