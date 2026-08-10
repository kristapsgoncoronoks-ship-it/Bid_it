"""WO-95 — R39's fee-free guarantee, re-proven with fees ACTUALLY POPULATED.

WO-93 built `/claim-status` with a structural guarantee that no fee figure
reaches the client wire (`BA_fleet_fuel.md` R39: *"**No internal codes, no
actions, no fees** are exposed to the client role"*; acceptance: *"A client-role
session cannot see a status code or a fee anywhere"*). WO-95 is the order that
finally puts a number in `fee_pct`/`fee_min`/`fee_eur`, so this file exists to
re-prove the guarantee against real data rather than against three NULL columns.

WHAT WO-93 ALREADY PROVES, AND WHAT IT DOES NOT
--------------------------------------------------
Two of WO-93's three scans are STRUCTURAL and hold regardless of the data:

* `test_wo93_the_service_never_reads_a_fee_or_currency_ambiguous_column` is an
  AST walk over `client_status.py` for attribute access to `fee_pct`/`fee_min`/
  `fee_eur`/`vat_local`/`paid_amount`;
* `test_wo93_no_field_name_carries_code_fee_or_action_vocabulary` scans the
  dataclass and Pydantic field names against `FEE_WORDS`.

The third — `test_wo93_no_internal_status_code_reaches_the_wire` — serialises a
REAL response, and every fee column in its portfolio was NULL at `076355f`. It
was not wrong, but it had never met a populated fee.

It does now, and WITHOUT a line of WO-93 being edited: `tests/transport/
conftest.py::enable_transport` configures the org's standard contingency rate,
so every submission fixture in the tree — including WO-93's own
`_submitted_claim` — freezes a real `fee_pct`/`fee_min`/`fee_eur` through
`lock.submit_claim`. The first test below proves exactly that, so if the fixture
raise is ever removed the vacuity is reported here rather than silently
restored.

The remaining tests then assert the ABSENCE over that populated portfolio: no
fee figure, in any representation, anywhere in the serialized response.
"""

from __future__ import annotations

import ast
import inspect
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.transport.vat_claim import VatRefundClaim
from app.schemas import transport_client_status
from app.services.transport import client_status
from tests.transport.test_wo93_client_status import (
    PERIOD_ENDED,
    _org_with_entity,
    _submitted_claim,
)


def _payload(view: client_status.ClientClaimStatus) -> dict:
    """The exact wire shape the route emits — WO-93's own serialisation, reused
    so the two files can never diverge on what "the wire" means."""
    return transport_client_status.ClientClaimStatusOut(
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


def _leaf_strings(value) -> list[str]:
    """Every string anywhere in a serialized response, at any depth — WO-93's
    walker, reused."""
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


@pytest.mark.asyncio
async def test_wo95_a_submitted_claim_really_carries_a_frozen_fee(db_session):
    """The de-vacuuming assertion. WO-93's `_submitted_claim` goes through the
    real `lock.submit_claim`, which since WO-95 freezes a fee — so the fixture
    that WO-93's wire scan runs over now genuinely carries one. If the shared
    conftest rate seeding is ever removed, THIS test fails and says why, instead
    of WO-93's scan quietly going back to proving nothing."""
    org_id, entity_id = await _org_with_entity(db_session, activate=True)
    claim = await _submitted_claim(db_session, org_id, entity_id)

    assert claim.fee_pct is not None, "the fixture must really freeze a fee"
    assert claim.fee_min is not None
    assert claim.fee_eur is not None
    assert claim.fee_eur > Decimal("0")


@pytest.mark.asyncio
async def test_wo95_no_fee_figure_reaches_the_client_wire(db_session):
    """R39's acceptance line, over a portfolio whose fees are REAL.

    Every frozen fee value is checked in the two representations it could take
    on the wire — the plain decimal string a Pydantic `Decimal` serialises to,
    and the bare number — against every leaf of the response. The base
    (`vat_eur`) IS shown, deliberately: it is the client's own reclaimable VAT,
    not our charge."""
    org_id, entity_id = await _org_with_entity(db_session, activate=True)
    claim = await _submitted_claim(db_session, org_id, entity_id)
    frozen = {claim.fee_pct, claim.fee_min, claim.fee_eur}
    assert None not in frozen, "the fixture must really freeze a fee"
    await db_session.commit()

    view = await client_status.client_claim_status(db_session, org_id, 2026, today=PERIOD_ENDED)
    payload = _payload(view)
    assert payload["total_claims"] == 1, "the fixture must be real"

    forbidden = {str(v) for v in frozen} | {str(Decimal(v).normalize()) for v in frozen}
    leaked = sorted({s for s in _leaf_strings(payload) if s in forbidden})
    assert leaked == [], f"R39: a fee figure reached the client wire — {leaked}"

    # And the base the fee was computed from IS on the wire — proving the scan
    # above is not passing because the response is empty.
    assert str(claim.vat_eur) in _leaf_strings(payload)


@pytest.mark.asyncio
async def test_wo95_the_fee_free_scan_would_catch_a_leak(db_session):
    """`WORK_ORDER_TEMPLATE.md` rule 6 — the scan must be able to fail. The same
    walker, over a response shape that carries the fee at depth."""
    org_id, entity_id = await _org_with_entity(db_session, activate=True)
    claim = await _submitted_claim(db_session, org_id, entity_id)

    seeded = {"claims": [{"stage": "filed", "service_fee": str(claim.fee_eur)}]}
    forbidden = {str(claim.fee_eur)}
    assert sorted({s for s in _leaf_strings(seeded) if s in forbidden}) == [str(claim.fee_eur)]


def test_wo95_the_client_service_still_reads_no_fee_column():
    """WO-93's structural assertion, re-run here against the module AS IT NOW
    STANDS. Kept as a WO-95 test as well as a WO-93 one because this order is
    what created the incentive to read those columns — the fee finally holds a
    number worth showing, and this surface is the one place it must not be."""
    tree = ast.parse(inspect.getsource(client_status))
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not attributes & {"fee_pct", "fee_min", "fee_eur"}, (
        "R39: the client surface reads a fee column"
    )


def test_wo95_the_client_service_never_imports_the_fee_module():
    """One level up from the column scan: the fee SERVICE is not reachable from
    the client surface at all, so no future refactor can route a figure in
    through `fee.compute_fee` while satisfying the attribute scan."""
    tree = ast.parse(inspect.getsource(client_status))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert not any("fee" in name.split(".") for name in imported), (
        f"R39: the client surface imports the fee module — {sorted(imported)}"
    )


@pytest.mark.asyncio
async def test_wo95_the_frozen_fee_is_still_visible_to_the_operator(db_session):
    """The other half of R39: the fee is hidden from the CLIENT surface, not
    from the platform. The claim row itself carries it, and the operator-facing
    claim schema has exposed the three fields since WO-49 — so this order needed
    no route change to make the frozen fee readable."""
    org_id, entity_id = await _org_with_entity(db_session, activate=True)
    claim = await _submitted_claim(db_session, org_id, entity_id)
    await db_session.commit()

    fresh = await db_session.scalar(select(VatRefundClaim).where(VatRefundClaim.id == claim.id))
    assert fresh.fee_eur is not None

    from app.schemas import transport_claim

    assert {"fee_pct", "fee_min", "fee_eur"} <= set(transport_claim.ClaimOut.model_fields)
