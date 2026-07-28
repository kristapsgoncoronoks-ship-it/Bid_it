"""Duplicate-invoice candidate detection: the E1.4 scored (amount ± tolerance,
date proximity, same vendor) signal, layered on top of the existing exact-number
`candidates()` path."""

import pytest


def _invoice_payload(number, *, vendor="AWS", unit_price="100.00", issue_date="2026-01-15"):
    return {
        "vendor_name": vendor,
        "invoice_number": number,
        "issue_date": issue_date,
        "currency": "EUR",
        "status": "pending",
        "line_items": [
            {
                "description": "Compute",
                "category": "cloud",
                "quantity": "1",
                "unit_price": unit_price,
                "tax_rate": "0",
            },
        ],
    }


@pytest.mark.asyncio
async def test_scored_candidates_same_amount_same_date_different_number(auth_client):
    first = await auth_client.post("/api/v1/invoices", json=_invoice_payload("INV-1042"))
    assert first.status_code == 201, first.text
    inv1 = first.json()

    second = await auth_client.post("/api/v1/invoices", json=_invoice_payload("INV-1042-R"))
    assert second.status_code == 201, second.text
    inv2 = second.json()

    dupes = await auth_client.get(
        "/api/v1/invoices/duplicate-candidates",
        params={
            "invoice_number": inv2["invoice_number"],
            "vendor_id": inv2["vendor_id"],
            "total": inv2["total"],
            "currency": inv2["currency"],
            "issue_date": "2026-01-15",
        },
    )
    assert dupes.status_code == 200, dupes.text
    scored = dupes.json()["scored"]
    assert len(scored) == 1
    hit = scored[0]
    assert hit["invoice_id"] == inv1["id"]
    assert hit["score"] > 0
    assert hit["reason"]  # non-empty, human-readable
    assert "vendor" in hit["reason"].lower()


@pytest.mark.asyncio
async def test_scored_candidates_excludes_other_vendor(auth_client):
    await auth_client.post("/api/v1/invoices", json=_invoice_payload("INV-1", vendor="AWS"))
    other = await auth_client.post(
        "/api/v1/invoices", json=_invoice_payload("INV-2", vendor="Azure")
    )
    inv2 = other.json()

    dupes = await auth_client.get(
        "/api/v1/invoices/duplicate-candidates",
        params={
            "invoice_number": inv2["invoice_number"],
            "vendor_id": inv2["vendor_id"],
            "total": inv2["total"],
            "currency": inv2["currency"],
            "issue_date": "2026-01-15",
        },
    )
    assert dupes.status_code == 200, dupes.text
    assert dupes.json()["scored"] == []


@pytest.mark.asyncio
async def test_scored_candidates_excludes_other_currency(auth_client):
    await auth_client.post(
        "/api/v1/invoices",
        json={**_invoice_payload("INV-1"), "currency": "USD"},
    )
    same_ccy = await auth_client.post("/api/v1/invoices", json=_invoice_payload("INV-2"))
    inv2 = same_ccy.json()

    dupes = await auth_client.get(
        "/api/v1/invoices/duplicate-candidates",
        params={
            "invoice_number": inv2["invoice_number"],
            "vendor_id": inv2["vendor_id"],
            "total": inv2["total"],
            "currency": inv2["currency"],  # EUR — the USD invoice must never appear
            "issue_date": "2026-01-15",
        },
    )
    assert dupes.status_code == 200, dupes.text
    assert dupes.json()["scored"] == []


@pytest.mark.asyncio
async def test_scored_candidates_outside_tolerance_is_dropped(auth_client):
    await auth_client.post("/api/v1/invoices", json=_invoice_payload("INV-1", unit_price="100.00"))
    far = await auth_client.post(
        "/api/v1/invoices",
        json=_invoice_payload("INV-2", unit_price="500.00", issue_date="2026-06-01"),
    )
    inv2 = far.json()

    dupes = await auth_client.get(
        "/api/v1/invoices/duplicate-candidates",
        params={
            "invoice_number": inv2["invoice_number"],
            "vendor_id": inv2["vendor_id"],
            "total": inv2["total"],
            "currency": inv2["currency"],
            "issue_date": "2026-06-01",
        },
    )
    assert dupes.status_code == 200, dupes.text
    assert dupes.json()["scored"] == []


@pytest.mark.asyncio
async def test_scored_candidates_excludes_same_number(auth_client):
    # Two invoices, same vendor/amount/date, SAME number — already an `exact`
    # match; `scored` must not double-surface it.
    await auth_client.post("/api/v1/invoices", json=_invoice_payload("INV-DUP"))
    second = await auth_client.post(
        "/api/v1/invoices",
        json={**_invoice_payload("INV-DUP"), "vendor_name": "AWS"},
    )
    inv2 = second.json()

    dupes = await auth_client.get(
        "/api/v1/invoices/duplicate-candidates",
        params={
            "invoice_number": inv2["invoice_number"],
            "vendor_id": inv2["vendor_id"],
            "total": inv2["total"],
            "currency": inv2["currency"],
            "issue_date": "2026-01-15",
            "exclude_invoice_id": inv2["id"],
        },
    )
    assert dupes.status_code == 200, dupes.text
    assert len(dupes.json()["exact"]) == 1  # the same-number path still fires
    assert dupes.json()["scored"] == []  # but scored doesn't double-count it


@pytest.mark.asyncio
async def test_scored_candidates_ranks_closer_match_higher(auth_client):
    target = await auth_client.post(
        "/api/v1/invoices", json=_invoice_payload("INV-TARGET", unit_price="100.00")
    )
    inv_t = target.json()

    close = await auth_client.post(
        "/api/v1/invoices",
        json=_invoice_payload("INV-CLOSE", unit_price="100.00", issue_date="2026-01-16"),
    )
    far = await auth_client.post(
        "/api/v1/invoices",
        json=_invoice_payload("INV-FAR", unit_price="100.50", issue_date="2026-01-25"),
    )
    inv_close, inv_far = close.json(), far.json()

    dupes = await auth_client.get(
        "/api/v1/invoices/duplicate-candidates",
        params={
            "invoice_number": inv_t["invoice_number"],
            "vendor_id": inv_t["vendor_id"],
            "total": inv_t["total"],
            "currency": inv_t["currency"],
            "issue_date": "2026-01-15",
        },
    )
    assert dupes.status_code == 200, dupes.text
    scored = dupes.json()["scored"]
    ids = [c["invoice_id"] for c in scored]
    assert ids.index(inv_close["id"]) < ids.index(inv_far["id"])
    by_id = {c["invoice_id"]: c["score"] for c in scored}
    assert by_id[inv_close["id"]] > by_id[inv_far["id"]]


@pytest.mark.asyncio
async def test_duplicate_candidates_scored_requires_all_four_params(auth_client):
    r = await auth_client.post("/api/v1/invoices", json=_invoice_payload("INV-ONLY"))
    inv = r.json()
    # Only invoice_number — today's only caller-supplied shape (pre-E1.4) —
    # must still work exactly as before: scored is empty, not an error.
    dupes = await auth_client.get(
        "/api/v1/invoices/duplicate-candidates",
        params={"invoice_number": inv["invoice_number"]},
    )
    assert dupes.status_code == 200, dupes.text
    assert dupes.json()["scored"] == []
