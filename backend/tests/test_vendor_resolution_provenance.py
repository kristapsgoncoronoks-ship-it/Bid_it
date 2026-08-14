"""The machine must be able to say why it picked THAT supplier (H-3).

Supplier resolution is exact-name and silent, so both of its outcomes are
invisible: the operator cannot see WHY a match was made, and a captured name with
one character wrong quietly forks the master data into two suppliers that will
never agree on a balance.

Two properties are pinned here:

* every resolution carries a reason and a plainly-stated OUTCOME — including
  "a new supplier will be created", which is the consequence an operator most
  needs to see before it happens rather than after;
* on ambiguity the machine ABSTAINS. It never takes first-match. Silently
  attaching an invoice to a supplier because the names nearly agree is exactly
  the failure this exists to prevent.

Trust in automation is bounded by the ability to interrogate one instance of it.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.models.vendor import Vendor
from app.services import vendor_resolution


async def _make_vendor(auth_client, name: str) -> str:
    r = await auth_client.post("/api/v1/vendors", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _resolve(auth_client, name: str) -> dict:
    r = await auth_client.get("/api/v1/vendors/resolve", params={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_an_exact_match_says_why_it_matched(auth_client):
    vid = await _make_vendor(auth_client, "Globex Ltd")

    res = await _resolve(auth_client, "Globex Ltd")

    assert res["basis"] == vendor_resolution.BASIS_EXACT_NAME
    assert res["vendor_id"] == vid
    assert "Globex Ltd" in res["reason"]
    assert res["needs_decision"] is False
    assert res["outcome"], "a match with no stated consequence is not provenance"


@pytest.mark.asyncio
async def test_an_unknown_supplier_warns_that_a_new_one_will_be_created(auth_client):
    """The consequence has to be visible BEFORE the confirm, not discovered
    afterwards in a supplier list that has grown a duplicate."""
    await _make_vendor(auth_client, "Globex Ltd")

    res = await _resolve(auth_client, "Initech Corporation")

    assert res["basis"] == vendor_resolution.BASIS_NONE
    assert res["vendor_id"] is None
    assert res["needs_decision"] is False  # nothing close — no decision to make
    assert "new supplier" in res["outcome"].lower()
    assert "Initech Corporation" in res["outcome"]


@pytest.mark.asyncio
async def test_a_near_miss_abstains_instead_of_guessing(auth_client):
    """THE rule. Never take first-match: picking the near one silently attaches
    the invoice to a supplier nobody chose, and creating silently forks the
    master data. The honest move is to hand the choice back with the evidence."""
    vid = await _make_vendor(auth_client, "Globex Ltd")

    res = await _resolve(auth_client, "Globex Limited")

    assert res["needs_decision"] is True
    assert res["vendor_id"] is None, "the machine picked a supplier on a near match"
    assert [c["vendor_id"] for c in res["candidates"]] == [vid]
    assert res["candidates"][0]["near_kind"] == vendor_resolution.NEAR_LEGAL_FORM
    # A reason, not a similarity score: the operator can agree or disagree with a
    # sentence; they can do nothing with "87% similar".
    assert "company-form" in res["candidates"][0]["reason"]
    assert "NEW supplier" in res["outcome"]


@pytest.mark.asyncio
async def test_case_spacing_and_accent_differences_are_each_explained(auth_client):
    await _make_vendor(auth_client, "Globex Ltd")
    await _make_vendor(auth_client, "Café Ström AB")

    spacing = await _resolve(auth_client, "globex  ltd")
    assert spacing["needs_decision"] is True
    assert spacing["candidates"][0]["near_kind"] == vendor_resolution.NEAR_CASE

    accents = await _resolve(auth_client, "Cafe Strom AB")
    assert accents["needs_decision"] is True
    assert accents["candidates"][0]["near_kind"] == vendor_resolution.NEAR_PUNCTUATION


@pytest.mark.asyncio
async def test_an_unrelated_name_is_not_dressed_up_as_a_near_match(auth_client):
    """A "did you mean" list that includes things you did not mean is noise, and
    noise trains the operator to click past the one that mattered."""
    await _make_vendor(auth_client, "Globex Ltd")

    res = await _resolve(auth_client, "Umbrella Corporation")

    assert res["candidates"] == []
    assert res["needs_decision"] is False


@pytest.mark.asyncio
async def test_resolving_creates_nothing(auth_client, db_session):
    """A provenance endpoint that mutates the master data would be the same class
    of hazard it exists to expose."""
    await _make_vendor(auth_client, "Globex Ltd")

    await _resolve(auth_client, "Something Entirely New OU")

    names = {v.name for v in await db_session.scalars(select(Vendor))}
    assert names == {"Globex Ltd"}


@pytest.mark.asyncio
async def test_the_resolution_matches_what_the_confirm_path_actually_does(auth_client, db_session):
    """An explanation that describes a different rule from the one that runs is
    worse than no explanation. This drives BOTH and compares them."""
    await _make_vendor(auth_client, "Globex Ltd")
    predicted = await _resolve(auth_client, "Globex Ltd")

    created = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Globex Ltd",
            "invoice_number": "INV-PROV-1",
            "issue_date": "2026-06-01",
            "currency": "EUR",
            "line_items": [
                {
                    "description": "Widgets",
                    "quantity": "1",
                    "unit_price": "10.00",
                    "amount": "10.00",
                    "tax_rate": "0",
                }
            ],
        },
    )
    assert created.status_code == 201, created.text

    assert created.json()["vendor_id"] == predicted["vendor_id"]


@pytest.mark.asyncio
async def test_the_automation_decision_is_written_to_the_permanent_record(auth_client, db_session):
    """The audit chain is where "why did the machine do this" belongs: immutable,
    and already the thing someone reads when they ask months later."""
    await _make_vendor(auth_client, "Globex Ltd")

    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Globex Limited",  # near miss → a NEW supplier
            "invoice_number": "INV-PROV-2",
            "issue_date": "2026-06-01",
            "currency": "EUR",
            "line_items": [
                {
                    "description": "Widgets",
                    "quantity": "1",
                    "unit_price": "10.00",
                    "amount": "10.00",
                    "tax_rate": "0",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text

    events = list(
        await db_session.scalars(
            select(AuditEvent).where(AuditEvent.action == "vendor.auto_resolved")
        )
    )
    assert len(events) == 1
    meta = json.loads(events[0].meta)  # audit meta is JSON TEXT — the chain hashes a string
    assert meta["captured_name"] == "Globex Limited"
    assert meta["basis"] == vendor_resolution.BASIS_NONE
    # The record shows the fork happened AND that a close alternative existed —
    # which is what makes it possible to find and merge later.
    assert meta["needs_decision"] is True
    assert meta["near_candidates"] == ["Globex Ltd"]


@pytest.mark.asyncio
async def test_the_record_describes_the_state_before_the_create_not_after(auth_client, db_session):
    """Resolving AFTER the create would describe the row it had just made and
    always report a confident exact match — an audit trail that is always
    reassuring and never true."""
    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_name": "Brand New Supplier OU",
            "invoice_number": "INV-PROV-3",
            "issue_date": "2026-06-01",
            "currency": "EUR",
            "line_items": [
                {
                    "description": "Widgets",
                    "quantity": "1",
                    "unit_price": "10.00",
                    "amount": "10.00",
                    "tax_rate": "0",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text

    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.action == "vendor.auto_resolved")
    )
    meta = json.loads(event.meta)
    assert meta["basis"] == vendor_resolution.BASIS_NONE, (
        "the resolution ran after the create and reported matching the row it made"
    )
    assert meta["matched_vendor_id"] is None


@pytest.mark.asyncio
async def test_choosing_the_supplier_explicitly_records_no_automation_decision(
    auth_client, db_session
):
    """When a human picks the supplier there is no machine decision to explain,
    and inventing an entry would put a fabricated automation event in the chain."""
    vid = await _make_vendor(auth_client, "Globex Ltd")

    r = await auth_client.post(
        "/api/v1/invoices",
        json={
            "vendor_id": vid,
            "invoice_number": "INV-PROV-4",
            "issue_date": "2026-06-01",
            "currency": "EUR",
            "line_items": [
                {
                    "description": "Widgets",
                    "quantity": "1",
                    "unit_price": "10.00",
                    "amount": "10.00",
                    "tax_rate": "0",
                }
            ],
        },
    )
    assert r.status_code == 201, r.text

    events = list(
        await db_session.scalars(
            select(AuditEvent).where(AuditEvent.action == "vendor.auto_resolved")
        )
    )
    assert events == []


@pytest.mark.asyncio
async def test_an_empty_captured_name_says_so_rather_than_guessing(auth_client):
    res = await _resolve(auth_client, "   ")

    assert res["basis"] == vendor_resolution.BASIS_NONE
    assert res["vendor_id"] is None
    assert res["candidates"] == []
    assert "captured" in res["reason"].lower()


def test_the_near_match_rules_do_not_collapse_genuinely_different_names():
    """The suggestion rules must not be so loose that two real, distinct
    suppliers look like the same one — a false "did you mean" that gets accepted
    merges two companies' ledgers."""
    assert vendor_resolution._near_reason("Globex Ltd", "Globex Ltf") is None
    assert vendor_resolution._near_reason("Globex Ltd", "Initech") is None
    assert vendor_resolution._near_reason("Nordic Fuel AB", "Nordic Fuels AB") is None
    # Two companies distinguished ONLY by their legal form are genuinely
    # ambiguous, so they SHOULD be offered — but as a question, never as a match.
    assert (
        vendor_resolution._near_reason("Nordic Fuel AB", "Nordic Fuel OY")
        == vendor_resolution.NEAR_LEGAL_FORM
    )
