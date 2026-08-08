"""WO-93 — R39's absences, proven STRUCTURALLY.

`BA_fleet_fuel.md` R39, verbatim: *"**Client-facing claim-status portal** with
plain-language stages only (prep -> ready -> filed -> awaiting -> refunded, +
needs attention). **No internal codes, no actions, no fees** are exposed to the
client role."* Its acceptance line is entirely an absence: *"A client-role
session cannot see a status code or a fee anywhere."*

WHY THIS FILE EXISTS AT ALL
-----------------------------
"We simply did not put the code on the wire" is a fact about today's file. An
asserted absence is a fact about tomorrow's. This surface is one `status_code`
field away from showing a client `3B` ("Rejection") with no way to act on it,
one `fee_eur` away from putting our service fee on the client's refund page,
and one button away from offering a control the server would refuse.

Three families are therefore banned from the wire shape, and two positive
properties are asserted beside them because a page can satisfy every ban by
rendering nothing:

1. no string VALUE in a real response equals any internal workflow code;
2. no FIELD NAME carries code, fee or action vocabulary;
3. no string the SERVICE OWNS (the labels and sentences it puts on the wire)
   carries action vocabulary — this surface offers nothing to do;
4. the stage vocabulary is the spec's own, verbatim, and all six are always
   present;
5. the plain-language labels are SERVER-owned, so the SPA cannot re-word them.

Every scan ships with a seeded-violation self-test (`WORK_ORDER_TEMPLATE.md`
rule 6: a coverage test that cannot fail proves nothing).
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.api.routes.transport import claim_status as claim_status_route
from app.schemas import transport_client_status
from app.services.transport import client_status, status
from tests.transport.conftest import enable_transport, make_entity, make_org
from tests.transport.test_wo93_client_status import (
    PERIOD_ENDED,
    _clean_claim,
    _make_claim,
    _org_with_entity,
    _submitted_claim,
)

BACKEND = Path(__file__).resolve().parents[2]

# The internal workflow vocabulary, IMPORTED rather than re-typed, so a code
# added to `status.py` is covered here without touching this file. `"2"` is
# added explicitly: `MANUAL_CODES` excludes it because only `lock.submit_claim`
# stamps it (see `test_wo93_every_internal_code_maps_to_exactly_one_client_stage`).
INTERNAL_CODES: tuple[str, ...] = (*status.AUTO_CODES, *status.MANUAL_CODES, "2")

# Vocabulary that must not appear in a FIELD NAME this surface emits. A field
# name is read as a fact the moment a client renders it, whatever the prose
# beside it says.
CODE_WORDS = ("code",)
FEE_WORDS = ("fee", "commission", "payout", "billed")
# The application's ACTION vocabulary — the verbs that would turn a read-only
# view into an offer to do something. Note what is NOT here: `filed` and
# `refunded` are the spec's own STAGE names, and this list names the app's
# actions, not the English language.
ACTION_WORDS = (
    "submit",
    "withdraw",
    "approve",
    "freeze",
    "lock",
    "waive",
    "override",
    "package",
    "send",
    "confirm",
)
FORBIDDEN_FIELD_WORDS = CODE_WORDS + FEE_WORDS + ACTION_WORDS


def _field_names(obj: type) -> list[str]:
    if dataclasses.is_dataclass(obj):
        return [f.name for f in dataclasses.fields(obj)]
    if isinstance(obj, type) and issubclass(obj, BaseModel):
        return list(obj.model_fields)
    return []


def _public_types(module) -> list[type]:
    return [
        obj
        for _name, obj in inspect.getmembers(module, inspect.isclass)
        if obj.__module__ == module.__name__
    ]


def _leaf_strings(value) -> list[str]:
    """Every string anywhere in a serialized response, at any depth."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for key, item in value.items():
            out.append(key)
            out.extend(_leaf_strings(item))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_leaf_strings(item))
        return out
    return []


def _offending_field_names(names: list[str], words: tuple[str, ...]) -> list[str]:
    return [n for n in names if any(w in n.lower() for w in words)]


def _action_words_in(text: str) -> list[str]:
    """Word-boundary matches only: "sending" counts, "descending" does not."""
    lowered = text.lower()
    return [w for w in ACTION_WORDS if re.search(rf"\b{w}", lowered)]


# --------------------------------------------------------------------------- #
# 1. No internal status code reaches the wire
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo93_no_internal_status_code_reaches_the_wire(db_session):
    """Over a portfolio carrying EVERY manual code plus a submitted and a draft
    claim: no string anywhere in the response — value or key, at any depth —
    EQUALS an internal workflow code.

    Equality, not containment: `"2"` is a code and `"2026-Q2"` is a period, so a
    substring test would either be meaningless or ban the period the client
    needs to see."""
    org_id, entity_id = await _org_with_entity(db_session, activate=True)
    submitted = await _submitted_claim(db_session, org_id, entity_id)
    assert submitted.status_code == "2", "the fixture must really carry a code"

    # One claim per manual code, so every value the column can hold is in scope.
    # Each needs its OWN refund country: the claim grain is (entity, country,
    # period) and `get_or_create_claim` is idempotent on it (R1), so reusing a
    # country would silently overwrite the previous code instead of adding one.
    countries = ("LT", "EE", "PL", "DE", "FR", "ES", "IT", "NL", "BE", "SE")
    assert len(countries) >= len(status.MANUAL_CODES)
    for offset, code in enumerate(status.MANUAL_CODES):
        c = await _make_claim(
            db_session,
            org_id,
            entity_id,
            ref_period="2026-Q3",
            refund_country=countries[offset],
        )
        c.status = "paid"
        c.status_code = code
        c.vat_eur = Decimal("100.00")
    await _clean_claim(
        db_session,
        org_id,
        entity_id,
        supplier="BP",
        number="INV-D",
        sha="f" * 64,
        ref_period="2026-Q4",
        month="2026-11",
        txn_date=date(2026, 11, 3),
    )
    await db_session.commit()

    view = await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_ENDED)
    payload = transport_client_status.ClientClaimStatusOut(
        year=view.year,
        currency=view.currency,
        total_claims=view.total_claims,
        stages=[
            transport_client_status.ClientStageOut(
                stage=s.stage,
                label=s.label,
                description=s.description,
                claims=s.claims,
                vat_eur=s.vat_eur,
            )
            for s in view.stages
        ],
        claims=[
            transport_client_status.ClientClaimOut(
                entity_name=c.entity_name,
                refund_country=c.refund_country,
                period=c.period,
                stage=c.stage,
                vat_eur=c.vat_eur,
            )
            for c in view.claims
        ],
        not_shown_claims=view.not_shown_claims,
    ).model_dump(mode="json")

    assert payload["total_claims"] >= len(status.MANUAL_CODES), "the fixture must be real"
    leaked = sorted({s for s in _leaf_strings(payload) if s in INTERNAL_CODES})
    assert leaked == [], f"R39: an internal workflow code reached the client wire — {leaked}"


def test_wo93_the_code_scan_detects_a_seeded_code_value():
    """The scanner's own self-test. A response shape that carried a code — at
    any depth, as a value or as a key — must be caught."""
    seeded = {"stages": [{"stage": "filed", "status_code": "3B"}], "year": 2026}
    hits = sorted({s for s in _leaf_strings(seeded) if s in INTERNAL_CODES})
    assert hits == ["3B"]


# --------------------------------------------------------------------------- #
# 2. No field name carries code, fee or action vocabulary
# --------------------------------------------------------------------------- #


def test_wo93_no_field_name_carries_code_fee_or_action_vocabulary():
    """Both layers are scanned — the service result shapes and the wire schemas
    — because a rename in either would reach a screen. A `status_code` field or
    a `fee_eur` field is read as such by a client the moment it is rendered."""
    offenders: list[str] = []
    for module in (client_status, transport_client_status):
        for shape in _public_types(module):
            for field in _field_names(shape):
                for word in _offending_field_names([field], FORBIDDEN_FIELD_WORDS):
                    offenders.append(f"{module.__name__}.{shape.__name__}.{word}")
    assert offenders == [], (
        f"R39: the client surface must not name a code, a fee or an action — {offenders}"
    )


def test_wo93_the_field_name_scan_detects_a_seeded_violation():
    """`WORK_ORDER_TEMPLATE.md` rule 6 — the scan must be able to fail. One
    seeded name per banned family."""
    import dataclasses as dc

    @dc.dataclass(frozen=True)
    class _Seeded:
        status_code: str
        fee_eur: int
        can_submit: bool

    hits = _offending_field_names(_field_names(_Seeded), FORBIDDEN_FIELD_WORDS)
    assert sorted(hits) == ["can_submit", "fee_eur", "status_code"]


def test_wo93_the_service_never_reads_a_fee_or_currency_ambiguous_column():
    """The absence, over the module's CODE. `fee_pct`/`fee_min`/`fee_eur` are
    R39's own subject; `vat_local`/`currency`/`paid_amount` are per-refund-state
    or currency-ambiguous (§4.14), exactly as `recovery.py` records. No
    attribute access to any of them exists in this module."""
    tree = ast.parse(inspect.getsource(client_status))
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    banned = {"fee_pct", "fee_min", "fee_eur", "vat_local", "paid_amount"}
    assert not attributes & banned, (
        f"a forbidden column is read here: {sorted(attributes & banned)}"
    )
    called = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "float" not in called, "no float in a money path (§4.9)"


# --------------------------------------------------------------------------- #
# 3. Nothing the service says reads as an instruction
# --------------------------------------------------------------------------- #


def test_wo93_no_service_owned_string_carries_action_vocabulary():
    """The labels and sentences this module puts on the wire are the only prose
    the client reads. A read-only view must not tell anyone to do anything —
    and must not describe an action they cannot take."""
    offenders: list[str] = []
    for stage, text in {**client_status.STAGE_LABELS, **client_status.STAGE_DESCRIPTIONS}.items():
        for word in _action_words_in(text):
            offenders.append(f"{stage}: {text!r} ~ {word!r}")
    assert offenders == [], f"R39 (no actions): {offenders}"


def test_wo93_the_action_word_scan_detects_a_seeded_violation():
    assert _action_words_in("Ready to submit — confirm when you are happy") == [
        "submit",
        "confirm",
    ]
    assert _action_words_in("Filed with the refunding country's tax authority.") == []


def test_wo93_the_route_path_and_query_carry_no_internal_vocabulary():
    """The URL is vocabulary too — `/transport/claim-status`, never
    `/transport/claims/status-codes`."""
    paths = [r.path for r in claim_status_route.router.routes]  # type: ignore[attr-defined]
    assert paths == ["/transport/claim-status"], paths  # the router's own prefix
    for path in paths:
        assert not _offending_field_names([path], FEE_WORDS + ACTION_WORDS), path
        assert "status_code" not in path


# --------------------------------------------------------------------------- #
# 4-5. The positive half: the spec's vocabulary, server-owned
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo93_all_six_stages_are_always_present_with_their_server_owned_wording(db_session):
    """A stage that vanished at zero would make "you have nothing in
    preparation" and "the page forgot to tell you" identical — the same reason
    `recovery.py` always sends all six readiness buckets."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    await make_entity(db_session, org.id)
    await db_session.commit()

    view = await client_status.client_claim_status(db_session, org.id, 2026)
    assert [s.stage for s in view.stages] == list(client_status.CLIENT_STAGES)
    for s in view.stages:
        assert s.label == client_status.STAGE_LABELS[s.stage]
        assert s.description == client_status.STAGE_DESCRIPTIONS[s.stage]
        assert s.claims == 0


def test_wo93_the_stage_labels_are_server_owned_and_readable():
    """The client reads a sentence, not a slug — and the SPA cannot re-word it,
    because it never holds the words (a frontend spec asserts the other half:
    the label text appears in no file under `frontend/src/`)."""
    for stage, label in client_status.STAGE_LABELS.items():
        assert label != stage, f"{stage} is rendered as its own slug"
        assert "_" not in label, f"{label!r} is slug-shaped"
        assert label[0].isupper()
    assert len(set(client_status.STAGE_LABELS.values())) == len(client_status.CLIENT_STAGES)


def test_wo93_the_schema_exposes_no_claim_identifier():
    """No claim id crosses this wire. There is nothing to open — a read-only
    view offers no drill-down — and the R1 grain (entity x country x period)
    already identifies each row for a reader."""
    fields = set(_field_names(transport_client_status.ClientClaimOut))
    assert fields == {"entity_name", "refund_country", "period", "stage", "vat_eur"}
    assert "id" not in fields and "claim_id" not in fields


def test_wo93_the_frontend_holds_no_stage_label_of_its_own():
    """The backend half of the SPA assertion: every label this module owns must
    be findable HERE, so the test that greps `frontend/src/` for them has a
    subject. Pinned as literals so a re-wording is a deliberate act."""
    assert client_status.STAGE_LABELS == {
        "prep": "In preparation",
        "ready": "Ready for filing",
        "filed": "Filed with the tax authority",
        "awaiting": "Decision received, money on its way",
        "refunded": "Refunded",
        "needs_attention": "Needs attention",
    }
    source = (BACKEND / "app" / "services" / "transport" / "client_status.py").read_text(
        encoding="utf-8"
    )
    for label in client_status.STAGE_LABELS.values():
        assert label in source
