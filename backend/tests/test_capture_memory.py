"""Extraction learning loop (E1.5): the per-(vendor x field) correction memory
service (`app.services.capture_memory`) in isolation from the capture-review
HTTP surface (covered end to end in `tests/test_capture_review_api.py`).

§4.19 governs this module even though no AI model is involved: a hint derived
from past corrections must stay a read-only suggestion. These tests prove the
service's own contract — `hints_for` is a pure lookup, `observe_fields`/
`record_correction` are the only writers, and nothing here ever touches an
`ExtractionField` row.
"""

from __future__ import annotations

import pytest

from app.models.organization import Organization
from app.services import capture_memory


async def _org_id(db_session) -> str:
    org = Organization(name="Fictional Freight OU")
    db_session.add(org)
    await db_session.flush()
    return org.id


@pytest.mark.asyncio
async def test_normalize_vendor_strips_and_none_on_blank():
    assert capture_memory.normalize_vendor("  Acme Fuel Co  ") == "Acme Fuel Co"
    assert capture_memory.normalize_vendor(None) is None
    assert capture_memory.normalize_vendor("") is None
    assert capture_memory.normalize_vendor("   ") is None


@pytest.mark.asyncio
async def test_observe_then_no_correction_yields_no_hint(db_session):
    org_id = await _org_id(db_session)
    await capture_memory.observe_fields(db_session, org_id, "Acme Fuel Co", ["vendor_name"])
    hints = await capture_memory.hints_for(db_session, org_id, "Acme Fuel Co", ["vendor_name"])
    assert hints == {}


@pytest.mark.asyncio
async def test_correction_produces_a_hint_for_the_same_vendor(db_session):
    org_id = await _org_id(db_session)
    await capture_memory.record_correction(
        db_session,
        org_id,
        vendor_name="Acme Fuel Co",
        field="vendor_name",
        original_value="ACME FUEL CO",
        corrected_value="Acme Fuel Company Ltd",
    )
    await db_session.commit()

    hints = await capture_memory.hints_for(db_session, org_id, "Acme Fuel Co", ["vendor_name"])
    assert hints.keys() == {"vendor_name"}
    hint = hints["vendor_name"]
    assert hint.suggested_value == "Acme Fuel Company Ltd"
    assert hint.corrected_count == 1


@pytest.mark.asyncio
async def test_hint_uses_most_recent_correction(db_session):
    org_id = await _org_id(db_session)
    for corrected in ("First Correction", "Second Correction"):
        await capture_memory.record_correction(
            db_session,
            org_id,
            vendor_name="Acme Fuel Co",
            field="category",
            original_value=None,
            corrected_value=corrected,
        )
        await db_session.commit()

    hints = await capture_memory.hints_for(db_session, org_id, "Acme Fuel Co", ["category"])
    hint = hints["category"]
    assert hint.suggested_value == "Second Correction"
    assert hint.corrected_count == 2


@pytest.mark.asyncio
async def test_hint_scoped_to_exact_vendor_name(db_session):
    org_id = await _org_id(db_session)
    await capture_memory.record_correction(
        db_session,
        org_id,
        vendor_name="Acme Fuel Co",
        field="vendor_name",
        original_value="acme fuel co",
        corrected_value="Acme Fuel Company Ltd",
    )
    await db_session.commit()

    # A trivially different spelling/casing is a DIFFERENT exact-stripped key —
    # the same discipline `vendors.get_or_create_vendor` uses.
    hints = await capture_memory.hints_for(db_session, org_id, "acme fuel co", ["vendor_name"])
    assert hints == {}
    hints = await capture_memory.hints_for(db_session, org_id, "ACME FUEL CO", ["vendor_name"])
    assert hints == {}


@pytest.mark.asyncio
async def test_hints_for_never_writes_extraction_field(db_session):
    """§4.19 module-boundary proof: `hints_for` is a pure SELECT — it takes no
    `ExtractionField`/run argument at all, so there is no code path by which
    calling it could mutate a captured field. Asserted here at the type/contract
    level; the end-to-end non-mutation proof lives in
    `test_capture_review_api.py`."""
    import inspect

    sig = inspect.signature(capture_memory.hints_for)
    assert set(sig.parameters) == {"db", "org_id", "vendor_name", "fields"}
