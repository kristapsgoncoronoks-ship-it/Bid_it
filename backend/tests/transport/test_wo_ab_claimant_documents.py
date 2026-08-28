"""WO-AB (G2.10 slice 2) — the four checklist rules §3.E names and slice 1
could not evaluate: `nace` (a DATA check needing a column) plus `contract`,
`trade_register` and `power_of_attorney` (DOCUMENT checks needing a store with
an EXPIRY).

WHAT THESE TESTS ARE FOR
-------------------------
The order's own certification, in this order:

1. A claim BLOCKED on a missing NACE code, and RELEASED when it is supplied —
   the release half matters as much as the block, because a gate that never
   clears is indistinguishable from a broken one.
2. The document rules behaving like the existing document gate rather than a
   second mechanism: absent fails, present passes, EXPIRED fails again, and
   the reason says WHICH of the two failures it is.
3. Country scope: a power of attorney held for one refund country does not
   satisfy a claim filed in another. This is the one that would silently pass
   if `submission_checklist` ignored `claim.refund_country`, and a
   count-the-items assertion could never see it.
4. R45's acceptance test, now over the four new rules: DEACTIVATE one and it
   disappears from the gate entirely.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

import pytest

from app.core.errors import NotFoundError, ValidationError
from app.models.transport.claimant_document import DOC_KINDS
from app.services.transport import checklist, claimant_documents, status, tax_authority
from app.services.transport import claim as claim_svc
from app.services.transport.capture_review import COUNTRIES
from tests.transport.conftest import enable_transport, make_entity, make_org

TODAY = date(2026, 7, 1)
REFUND_COUNTRY = "LV"


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


async def _setup(db_session, *, documents: bool = True):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id, documents=documents)
    claim = await claim_svc.get_or_create_claim(
        db_session,
        org.id,
        entity_id=entity.id,
        refund_country=REFUND_COUNTRY,
        ref_period="2026-Q2",
    )
    await db_session.commit()
    return org, entity, claim


async def _items(db_session, org, claim, *, today: date = TODAY):
    rows = await checklist.submission_checklist(db_session, org.id, claim.id, today=today)
    return {i.key: i for i in rows}


# --------------------------------------------------------------------------- #
# 1. The NACE rule — blocked, then released
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo_ab_a_missing_nace_code_blocks_and_supplying_it_releases(db_session):
    """The order's headline certification. Both halves in one test on purpose:
    asserting only the block would pass just as happily against a rule that is
    hard-wired to fail."""
    org, entity, claim = await _setup(db_session)

    entity.nace_code = None
    await db_session.flush()
    blocked = await _items(db_session, org, claim)
    assert blocked["nace"].ok is False
    assert "NACE" in blocked["nace"].reason

    entity.nace_code = "H49.41"
    await db_session.flush()
    released = await _items(db_session, org, claim)
    assert released["nace"].ok is True
    assert released["nace"].reason is None


@pytest.mark.asyncio
async def test_wo_ab_a_blank_nace_code_is_missing_not_present(db_session):
    """`_field_ok`'s convention, applied to the new field: whitespace is not a
    business-activity code. A stored `"   "` passing would mean the rule could
    be satisfied by pressing space."""
    org, entity, claim = await _setup(db_session)
    entity.nace_code = "   "
    await db_session.flush()
    items = await _items(db_session, org, claim)
    assert items["nace"].ok is False


@pytest.mark.asyncio
async def test_wo_ab_nace_accepts_every_national_shape(db_session):
    """No format gate, deliberately: PKD `49.41.Z`, `H49.41` and the bare
    `49.41` are all real codes for the same activity in different member
    states. A shape check here would refuse valid data and report it as a
    missing business activity."""
    org, entity, claim = await _setup(db_session)
    for code in ("49.41", "H49.41", "49.41.Z"):
        entity.nace_code = code
        await db_session.flush()
        items = await _items(db_session, org, claim)
        assert items["nace"].ok is True, code


# --------------------------------------------------------------------------- #
# 2. The document rules — absent / present / expired
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo_ab_a_claimant_with_no_documents_fails_all_three(db_session):
    org, entity, claim = await _setup(db_session, documents=False)
    items = await _items(db_session, org, claim)
    for key in ("contract", "trade_register", "power_of_attorney"):
        assert items[key].ok is False, key
        assert "No document on file" in items[key].reason, key


@pytest.mark.asyncio
async def test_wo_ab_recording_a_document_releases_its_rule_only(db_session):
    """One document releases ONE rule. The others stay blocked — a store that
    answered "yes" for every kind once anything was on file would make the
    checklist an upload counter."""
    org, entity, claim = await _setup(db_session, documents=False)

    await claimant_documents.record(
        db_session,
        org.id,
        entity.id,
        kind="trade_registry",
        sha256=_sha("register"),
        size=100,
    )
    await db_session.flush()

    items = await _items(db_session, org, claim)
    assert items["trade_register"].ok is True
    assert items["contract"].ok is False
    assert items["power_of_attorney"].ok is False


@pytest.mark.asyncio
async def test_wo_ab_an_expired_document_fails_and_says_when(db_session):
    """§3.E, verbatim: *"an expired PoA fails `_has_doc` and the claim drops
    back to 1A."* The reason names the date, because "not held" and "lapsed on
    2026-06-30" are different jobs for whoever reads the checklist."""
    org, entity, claim = await _setup(db_session, documents=False)
    lapsed = TODAY - timedelta(days=1)

    await claimant_documents.record(
        db_session,
        org.id,
        entity.id,
        kind="power_of_attorney",
        country=REFUND_COUNTRY,
        sha256=_sha("poa-expired"),
        size=100,
        valid_until=lapsed,
    )
    await db_session.flush()

    items = await _items(db_session, org, claim)
    assert items["power_of_attorney"].ok is False
    assert lapsed.isoformat() in items["power_of_attorney"].reason
    assert "No document on file" not in items["power_of_attorney"].reason


@pytest.mark.asyncio
async def test_wo_ab_validity_is_inclusive_of_its_last_day(db_session):
    """A power of attorney valid until 2026-07-01 is valid ON 2026-07-01. The
    off-by-one here would refuse a claim on the last day the instrument
    actually covers."""
    org, entity, claim = await _setup(db_session, documents=False)
    await claimant_documents.record(
        db_session,
        org.id,
        entity.id,
        kind="power_of_attorney",
        country=REFUND_COUNTRY,
        sha256=_sha("poa-today"),
        size=100,
        valid_until=TODAY,
    )
    await db_session.flush()

    assert (await _items(db_session, org, claim))["power_of_attorney"].ok is True
    tomorrow = TODAY + timedelta(days=1)
    assert (await _items(db_session, org, claim, today=tomorrow))["power_of_attorney"].ok is False


@pytest.mark.asyncio
async def test_wo_ab_a_document_with_no_stated_expiry_never_lapses(db_session):
    """NULL `valid_until` is a FACT ("this does not expire"), not an absence.
    Failing closed on it would block every claimant holding a trade-register
    extract, which normally carries no expiry at all."""
    org, entity, claim = await _setup(db_session, documents=False)
    await claimant_documents.record(
        db_session, org.id, entity.id, kind="signed_contract", sha256=_sha("c"), size=10
    )
    await db_session.flush()

    far_future = date(2099, 1, 1)
    assert (await _items(db_session, org, claim, today=far_future))["contract"].ok is True


@pytest.mark.asyncio
async def test_wo_ab_a_renewal_beside_a_lapsed_document_passes(db_session):
    """The unique key carries `sha256` precisely so a renewal is a NEW row.
    Both are on file; the claimant holds a valid PoA, and the lapsed one stays
    visible rather than being overwritten out of the record."""
    org, entity, claim = await _setup(db_session, documents=False)
    for sha, until in ((_sha("old"), TODAY - timedelta(days=30)), (_sha("new"), None)):
        await claimant_documents.record(
            db_session,
            org.id,
            entity.id,
            kind="power_of_attorney",
            country=REFUND_COUNTRY,
            sha256=sha,
            size=10,
            valid_until=until,
        )
    await db_session.flush()

    assert (await _items(db_session, org, claim))["power_of_attorney"].ok is True
    held = await claimant_documents.list_documents(db_session, org.id, entity.id)
    assert len(held) == 2, "the lapsed document must survive its own renewal"


@pytest.mark.asyncio
async def test_wo_ab_re_recording_the_same_bytes_rewrites_the_window(db_session):
    """A corrected expiry must not leave two rows disagreeing about one
    document — same bytes, same (kind, country) is the SAME document."""
    org, entity, _claim = await _setup(db_session, documents=False)
    sha = _sha("poa")
    await claimant_documents.record(
        db_session,
        org.id,
        entity.id,
        kind="power_of_attorney",
        country=REFUND_COUNTRY,
        sha256=sha,
        size=10,
        valid_until=date(2026, 1, 1),
    )
    await claimant_documents.record(
        db_session,
        org.id,
        entity.id,
        kind="power_of_attorney",
        country=REFUND_COUNTRY,
        sha256=sha,
        size=10,
        valid_until=date(2027, 1, 1),
    )
    await db_session.flush()

    held = await claimant_documents.list_documents(db_session, org.id, entity.id)
    assert len(held) == 1
    assert held[0].valid_until == date(2027, 1, 1)


# --------------------------------------------------------------------------- #
# 3. Country scope
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo_ab_a_poa_for_another_country_does_not_satisfy_this_claim(db_session):
    """The country-scope rule reads THIS claim's refund country. A PoA held for
    FR is a real, valid, unexpired document — and irrelevant to an LV filing."""
    org, entity, claim = await _setup(db_session, documents=False)
    await claimant_documents.record(
        db_session,
        org.id,
        entity.id,
        kind="power_of_attorney",
        country="FR",
        sha256=_sha("poa-fr"),
        size=10,
    )
    await db_session.flush()

    items = await _items(db_session, org, claim)
    assert items["power_of_attorney"].ok is False
    assert items["power_of_attorney"].scope == "country"

    # The other half, in the same test on purpose: a rule that ignored
    # `claim.refund_country` entirely would also refuse the FR document, and a
    # one-sided assertion could not tell that apart from working correctly.
    await claimant_documents.record(
        db_session,
        org.id,
        entity.id,
        kind="power_of_attorney",
        country=REFUND_COUNTRY,
        sha256=_sha("poa-lv"),
        size=10,
    )
    await db_session.flush()
    assert (await _items(db_session, org, claim))["power_of_attorney"].ok is True


@pytest.mark.asyncio
async def test_wo_ab_a_customer_scope_document_is_not_matched_by_country(db_session):
    """The mirror: a contract recorded WITH a country would not answer the
    customer-scope rule, which asks for `country = ''`. The two namespaces stay
    separate, which is what makes `''` a real value rather than a NULL."""
    org, entity, claim = await _setup(db_session, documents=False)
    await claimant_documents.record(
        db_session,
        org.id,
        entity.id,
        kind="signed_contract",
        country=REFUND_COUNTRY,
        sha256=_sha("contract-lv"),
        size=10,
    )
    await db_session.flush()

    assert (await _items(db_session, org, claim))["contract"].ok is False


@pytest.mark.asyncio
async def test_wo_ab_the_poa_reason_names_the_national_authority(db_session):
    """§3.F F5's map, put where an operator reads it: knowing a PoA is missing
    is not the same as knowing who it has to be addressed to."""
    org, _entity, claim = await _setup(db_session, documents=False)
    reason = (await _items(db_session, org, claim))["power_of_attorney"].reason
    assert tax_authority.TAX_AUTHORITY[REFUND_COUNTRY] in reason


@pytest.mark.asyncio
async def test_wo_ab_an_unknown_country_adds_no_guess_to_the_reason(db_session):
    """F5, verbatim: *"an unknown country yields `""` — the merge never
    substitutes a guess."* The sentence simply stays as it was."""
    org, entity, _claim = await _setup(db_session, documents=False)
    claim = await claim_svc.get_or_create_claim(
        db_session, org.id, entity_id=entity.id, refund_country="NO", ref_period="2026-Q2"
    )
    await db_session.flush()

    reason = (await _items(db_session, org, claim))["power_of_attorney"].reason
    assert reason == "No document on file"


def test_wo_ab_the_authority_map_covers_the_one_country_list():
    """One country list, never two drifting copies — `capture_review.COUNTRIES`
    is this codebase's single set, and the authority map is pinned to it."""
    assert set(tax_authority.TAX_AUTHORITY) == COUNTRIES
    assert all(v.strip() for v in tax_authority.TAX_AUTHORITY.values())


def test_wo_ab_authority_for_is_case_and_blank_tolerant():
    assert tax_authority.authority_for("lv") == tax_authority.TAX_AUTHORITY["LV"]
    assert tax_authority.authority_for(None) == ""
    assert tax_authority.authority_for("  ") == ""
    assert tax_authority.authority_for("ZZ") == ""


# --------------------------------------------------------------------------- #
# 4. R45's acceptance test, over the new rules
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo_ab_deactivating_a_new_rule_removes_it_from_the_gate(db_session):
    """R45's verbatim acceptance line, now proven for a DOCUMENT rule: a
    workspace that does not use powers of attorney turns the rule off and the
    checklist stops asking. This is what makes seeding all six safe."""
    org, _entity, claim = await _setup(db_session, documents=False)
    assert (await _items(db_session, org, claim))["power_of_attorney"].ok is False

    await checklist.set_active(db_session, org.id, "power_of_attorney", False)
    await db_session.flush()

    assert "power_of_attorney" not in await _items(db_session, org, claim)


@pytest.mark.asyncio
async def test_wo_ab_a_claimant_missing_documents_previews_as_1a(db_session):
    """G2.7's read-only preview follows the checklist, exactly as §3.E says it
    should. Supplying every document moves it off `1A` — the same block/release
    pair as the NACE test, at the stage grain."""
    org, entity, claim = await _setup(db_session, documents=False)
    assert await status.derive_stage(db_session, org.id, claim.id, today=TODAY) == "1A"

    from tests.transport.conftest import give_claimant_documents

    await give_claimant_documents(db_session, org.id, entity.id, countries={REFUND_COUNTRY})
    await db_session.flush()

    after = await status.derive_stage(db_session, org.id, claim.id, today=TODAY)
    assert after != "1A", "every checklist rule now passes; the preview must move"


# --------------------------------------------------------------------------- #
# The store's own refusals
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo_ab_an_unknown_document_kind_is_refused(db_session):
    org, entity, _claim = await _setup(db_session, documents=False)
    with pytest.raises(ValidationError) as exc:
        await claimant_documents.record(
            db_session, org.id, entity.id, kind="notarised_wish", sha256=_sha("x"), size=1
        )
    assert exc.value.code == "unknown_document_kind"


@pytest.mark.asyncio
async def test_wo_ab_a_one_letter_country_is_refused_before_the_constraint(db_session):
    org, entity, _claim = await _setup(db_session, documents=False)
    with pytest.raises(ValidationError) as exc:
        await claimant_documents.record(
            db_session,
            org.id,
            entity.id,
            kind="power_of_attorney",
            country="L",
            sha256=_sha("x"),
            size=1,
        )
    assert exc.value.code == "invalid_country"


@pytest.mark.asyncio
async def test_wo_ab_a_window_that_ends_before_it_starts_is_refused(db_session):
    org, entity, _claim = await _setup(db_session, documents=False)
    with pytest.raises(ValidationError) as exc:
        await claimant_documents.record(
            db_session,
            org.id,
            entity.id,
            kind="signed_contract",
            sha256=_sha("x"),
            size=1,
            valid_from=date(2026, 6, 1),
            valid_until=date(2026, 5, 1),
        )
    assert exc.value.code == "invalid_validity_window"


@pytest.mark.asyncio
async def test_wo_ab_an_unknown_entity_is_refused(db_session):
    org, _entity, _claim = await _setup(db_session, documents=False)
    with pytest.raises(NotFoundError) as exc:
        await claimant_documents.record(
            db_session,
            org.id,
            "00000000-0000-0000-0000-000000000000",
            kind="signed_contract",
            sha256=_sha("x"),
            size=1,
        )
    assert exc.value.code == "entity_not_found"


@pytest.mark.asyncio
async def test_wo_ab_removing_an_unknown_document_is_an_opaque_404(db_session):
    org, _entity, _claim = await _setup(db_session, documents=False)
    with pytest.raises(NotFoundError) as exc:
        await claimant_documents.remove(db_session, org.id, "00000000-0000-0000-0000-000000000000")
    assert exc.value.code == "claimant_document_not_found"


# --------------------------------------------------------------------------- #
# The expiry chase board (§3.E's `expiring_documents(within_days=60)`)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo_ab_the_chase_board_keeps_already_lapsed_documents(db_session):
    """A chase list that dropped a document the day it expired would go quiet
    at exactly the moment the claims it covers start being refused."""
    org, entity, _claim = await _setup(db_session, documents=False)
    lapsed, soon, far = (
        TODAY - timedelta(days=10),
        TODAY + timedelta(days=30),
        TODAY + timedelta(days=400),
    )
    for kind, country, until in (
        ("power_of_attorney", "LV", lapsed),
        ("power_of_attorney", "EE", soon),
        ("power_of_attorney", "FR", far),
        ("signed_contract", "", None),
    ):
        await claimant_documents.record(
            db_session,
            org.id,
            entity.id,
            kind=kind,
            country=country,
            sha256=_sha(f"{kind}{country}"),
            size=10,
            valid_until=until,
        )
    await db_session.flush()

    rows = await claimant_documents.expiring(db_session, org.id, today=TODAY)
    got = {(r.kind, r.country) for r in rows}
    assert ("power_of_attorney", "LV") in got, "a lapsed document must stay on the board"
    assert ("power_of_attorney", "EE") in got
    assert ("power_of_attorney", "FR") not in got, "beyond the horizon"
    assert ("signed_contract", "") not in got, "no stated expiry is not 'expiring'"


def test_wo_ab_every_seeded_document_rule_names_a_real_kind():
    """The seed and the CHECK constraint cannot drift: a `DEFAULT_RULES` row
    whose `reference` is not in `DOC_KINDS` would raise
    `unsupported_check_type` for every claim in the workspace."""
    for key, _label, _scope, check_type, reference, _sort in checklist.DEFAULT_RULES:
        if check_type == "document":
            assert reference in DOC_KINDS, key
        else:
            assert reference in checklist.DATA_VERIFIERS, key
