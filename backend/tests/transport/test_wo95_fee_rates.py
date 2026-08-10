"""WO-95 — the contingency-fee FORMULA and the configured RATE (G2.9, R13,
`BA_fleet_fuel.md` C11, R40).

C11, verbatim: *"`% fee` takes priority; if it falls below the per-declaration
minimum, the **minimum** is charged. Returns `(fee, basis)` where basis ∈
{`percent`, `minimum`}. Resolution order for the rate (`fee_for`):
per-(customer, country) override → customer default → (0, 0)."*

Two things here are deliberate deviations from that text and are asserted as
such, not glossed:

1. the terminal `(0, 0)` rung is a **refusal** (`fee_rate_not_configured`) — the
   owner left the percentage open on 2026-08-08 and a fee figure is a live
   charge, so the engine never invents one;
2. an org-level STANDARD rung sits above the customer default, which is R40's
   *"Standard fee is admin-editable; a per-client fee overrides it"* and the
   shape the same decision names.

Every euro figure below is hand-computed and written as a literal `Decimal`, so
a change to the arithmetic fails against an independent number rather than
against the code's own output.
"""

from __future__ import annotations

import ast
import inspect
import json
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.models.audit import AuditEvent
from app.models.transport.fee_rate import ANY_COUNTRY, VatFeeRate
from app.services.transport import fee
from tests.transport.conftest import enable_transport, make_entity, make_org


async def _org_with_entity(db_session, *, fee_rate: bool = False):
    """An org with the vertical on and, by default, NO configured rate — this
    file is where the refusal itself is under test, so the shared helper's
    fixture-raising default is deliberately turned off."""
    org = await make_org(db_session)
    await enable_transport(db_session, org.id, fee_rate=fee_rate)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()
    return org.id, entity.id


async def _fee_events(db_session, org_id: str, action: str) -> list[AuditEvent]:
    rows = await db_session.scalars(
        select(AuditEvent).where(AuditEvent.org_id == org_id, AuditEvent.action == action)
    )
    return list(rows)


def _meta(event: AuditEvent) -> dict:
    """`AuditEvent.meta` is a JSON TEXT column — the `test_g3_3_tie_out.py`
    reader, reused."""
    return json.loads(event.meta) if isinstance(event.meta, str) else event.meta


# --------------------------------------------------------------------------- #
# 1. compute_fee — C11's formula, against hand-computed Decimals
# --------------------------------------------------------------------------- #


def test_wo95_compute_fee_is_the_percentage_when_it_clears_the_minimum():
    """15% of €2,000.00 is €300.00, which is above a €250.00 minimum, so the
    percentage is charged and the basis says so."""
    result = fee.compute_fee(Decimal("2000.00"), Decimal("15.00"), Decimal("250.00"))
    assert result == (Decimal("300.00"), "percent")


def test_wo95_compute_fee_is_the_minimum_when_the_percentage_falls_below_it():
    """15% of €1,000.00 is €150.00, below a €250.00 minimum — C11: *"if it falls
    below the per-declaration minimum, the **minimum** is charged."*"""
    result = fee.compute_fee(Decimal("1000.00"), Decimal("15.00"), Decimal("250.00"))
    assert result == (Decimal("250.00"), "minimum")


def test_wo95_the_exact_boundary_where_the_percentage_equals_the_minimum_is_percent():
    """THE boundary. 15% of €1,000.00 is exactly €150.00. C11 charges the
    minimum when the percentage *"falls below"* it, and an equal figure has not
    fallen below — so the basis is `percent`. The euro figure is the same either
    way; the BASIS is what an invoice line claims to be charging."""
    at_the_line = fee.compute_fee(Decimal("1000.00"), Decimal("15.00"), Decimal("150.00"))
    assert at_the_line == (Decimal("150.00"), "percent")


def test_wo95_one_cent_below_the_boundary_flips_to_the_minimum():
    """The other side, one cent apart (template rule 4 — every gate gets a
    both-sides pair). A minimum of €150.01 is one cent above the same €150.00
    percentage, so the minimum bites."""
    just_over = fee.compute_fee(Decimal("1000.00"), Decimal("15.00"), Decimal("150.01"))
    assert just_over == (Decimal("150.01"), "minimum")


def test_wo95_compute_fee_rounds_half_up_to_cents():
    """§4.9 — ROUND_HALF_UP via `money.q2`, never a bare `round()`. 15% of
    €33.50 is exactly €5.025, which rounds UP to €5.03 (banker's rounding would
    give €5.02 and is what this asserts against)."""
    result = fee.compute_fee(Decimal("33.50"), Decimal("15.00"), Decimal("0.00"))
    assert result == (Decimal("5.03"), "percent")


def test_wo95_compute_fee_is_exact_on_a_third_of_a_cent():
    """A base whose percentage does not land on a cent: 7.5% of €1,234.56 is
    €92.5920, quantized to €92.59. Decimal all the way — a float path would
    produce 92.59200000000001 before rounding."""
    result = fee.compute_fee(Decimal("1234.56"), Decimal("7.50"), Decimal("0.00"))
    assert result == (Decimal("92.59"), "percent")


def test_wo95_a_zero_base_still_charges_the_minimum():
    """C11's `max()` over a zero base: the percentage is €0.00, so the minimum
    is what is charged. Not a special case in the code, and asserted so it
    cannot become one."""
    result = fee.compute_fee(Decimal("0.00"), Decimal("15.00"), Decimal("25.00"))
    assert result == (Decimal("25.00"), "minimum")


def test_wo95_an_explicitly_typed_zero_rate_produces_a_zero_fee():
    """The distinction the whole module turns on: a `(0, 0)` rate is a human's
    DECISION and yields €0.00, while an ABSENT rate refuses (asserted below).
    Absence is not zero."""
    result = fee.compute_fee(Decimal("5000.00"), Decimal("0.00"), Decimal("0.00"))
    assert result == (Decimal("0.00"), "percent")


def test_wo95_the_fee_module_uses_no_float_and_reads_no_local_currency_column():
    """§4.9 and §4.14, over the module's own source (the `client_status.py` AST
    precedent). `vat_local` is denominated in the refunding state's currency and
    `fee_min` is EUR — comparing them would need a recorded conversion this
    module does not have, so it never reads one."""
    tree = ast.parse(inspect.getsource(fee))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "float" not in names, "no float in a money path (§4.9)"
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "vat_local" not in attributes, "the fee is NET EUR only (§4.14)"


def _numeric_module_constants(tree: ast.Module) -> dict[str, str]:
    """Every MODULE-LEVEL name in `tree` bound to a number or to a
    `Decimal("...")` literal, as {name: text}. A default rate, if one were ever
    introduced, would have to live in exactly such a binding."""
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        value = node.value
        if value is None or not names:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)):
            for name in names:
                out[name] = str(value.value)
        elif (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "Decimal"
            and value.args
            and isinstance(value.args[0], ast.Constant)
        ):
            for name in names:
                out[name] = str(value.args[0].value)
    return out


def test_wo95_the_module_hard_codes_no_fee_percentage():
    """The governing constraint, asserted structurally over the module's source.

    A default rate can only enter this codebase as a module-level numeric
    constant (`DEFAULT_FEE_PCT = Decimal("15.00")`, the shape `excise.py`
    legitimately uses for its advisory placeholder). `fee.py` is allowed exactly
    ONE such binding — `_HUNDRED = Decimal("100")`, the percentage divisor — and
    the harvested Appendix B figure must appear in none of them.

    Also asserted: no `Decimal("...")` literal anywhere in the module is
    anything but that divisor, so a rate cannot hide inside a function body
    either."""
    tree = ast.parse(inspect.getsource(fee))
    constants = _numeric_module_constants(tree)
    assert constants == {"_HUNDRED": "100"}, (
        f"fee.py binds a numeric constant that could be a default rate: {constants}"
    )

    decimals = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Decimal"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert decimals == {"100"}, f"fee.py names a Decimal rate: {sorted(decimals)}"


def test_wo95_the_percentage_scan_detects_a_seeded_rate():
    """`WORK_ORDER_TEMPLATE.md` rule 6 — the scan must be able to fail. Both
    shapes a default could take are seeded."""
    seeded = ast.parse(
        '_HUNDRED = Decimal("100")\nDEFAULT_FEE_PCT = Decimal("15.00")\nFALLBACK = 15\n'
    )
    constants = _numeric_module_constants(seeded)
    assert constants == {"_HUNDRED": "100", "DEFAULT_FEE_PCT": "15.00", "FALLBACK": "15"}
    assert constants != {"_HUNDRED": "100"}


# --------------------------------------------------------------------------- #
# 2. The resolution chain — C11's rungs, and the refusal that replaced (0, 0)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo95_resolve_refuses_when_no_rate_is_configured(db_session):
    """The governing constraint. No rung matches, so nothing is returned and
    nothing is guessed — `fee_rate_not_configured`, where C11 had `(0, 0)`."""
    org_id, entity_id = await _org_with_entity(db_session)
    with pytest.raises(AppError) as exc:
        await fee.resolve_fee_rate(db_session, org_id, entity_id=entity_id, country="LV")
    assert exc.value.code == "fee_rate_not_configured"


@pytest.mark.asyncio
async def test_wo95_resolve_falls_back_to_the_org_standard(db_session):
    """R40's *"Standard fee is admin-editable"* — the last rung before the
    refusal."""
    org_id, entity_id = await _org_with_entity(db_session)
    await fee.set_rate(db_session, org_id, fee_pct=Decimal("15.00"), fee_min=Decimal("250.00"))
    await db_session.commit()

    rate = await fee.resolve_fee_rate(db_session, org_id, entity_id=entity_id, country="LV")
    assert (rate.fee_pct, rate.fee_min, rate.scope) == (
        Decimal("15.00"),
        Decimal("250.00"),
        "org",
    )


@pytest.mark.asyncio
async def test_wo95_a_customer_default_overrides_the_org_standard(db_session):
    """R40's second half, verbatim: *"a per-client fee overrides it."*"""
    org_id, entity_id = await _org_with_entity(db_session)
    await fee.set_rate(db_session, org_id, fee_pct=Decimal("15.00"), fee_min=Decimal("250.00"))
    await fee.set_rate(
        db_session, org_id, entity_id=entity_id, fee_pct=Decimal("9.00"), fee_min=Decimal("50.00")
    )
    await db_session.commit()

    rate = await fee.resolve_fee_rate(db_session, org_id, entity_id=entity_id, country="LV")
    assert (rate.fee_pct, rate.fee_min, rate.scope) == (
        Decimal("9.00"),
        Decimal("50.00"),
        "customer",
    )


@pytest.mark.asyncio
async def test_wo95_a_per_country_override_beats_both(db_session):
    """C11's first rung: *"per-(customer, country) override"*. All three rungs
    exist simultaneously and the most specific wins."""
    org_id, entity_id = await _org_with_entity(db_session)
    await fee.set_rate(db_session, org_id, fee_pct=Decimal("15.00"), fee_min=Decimal("250.00"))
    await fee.set_rate(
        db_session, org_id, entity_id=entity_id, fee_pct=Decimal("9.00"), fee_min=Decimal("50.00")
    )
    await fee.set_rate(
        db_session,
        org_id,
        entity_id=entity_id,
        country="LV",
        fee_pct=Decimal("6.50"),
        fee_min=Decimal("10.00"),
    )
    await db_session.commit()

    lv = await fee.resolve_fee_rate(db_session, org_id, entity_id=entity_id, country="LV")
    assert (lv.fee_pct, lv.fee_min, lv.scope) == (
        Decimal("6.50"),
        Decimal("10.00"),
        "customer_country",
    )
    # A DIFFERENT country falls through to the customer default — the override
    # is per country, not a customer-wide replacement.
    ee = await fee.resolve_fee_rate(db_session, org_id, entity_id=entity_id, country="EE")
    assert (ee.fee_pct, ee.scope) == (Decimal("9.00"), "customer")


@pytest.mark.asyncio
async def test_wo95_another_customers_rate_never_resolves_for_this_one(db_session):
    """A per-client rate is per client. Entity B's negotiated rate must not
    reach entity A, which still refuses because nothing of its own exists."""
    org_id, entity_a = await _org_with_entity(db_session)
    entity_b = await make_entity(db_session, org_id, seed=2)
    await db_session.commit()
    await fee.set_rate(
        db_session, org_id, entity_id=entity_b.id, fee_pct=Decimal("5.00"), fee_min=Decimal("0.00")
    )
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await fee.resolve_fee_rate(db_session, org_id, entity_id=entity_a, country="LV")
    assert exc.value.code == "fee_rate_not_configured"


# --------------------------------------------------------------------------- #
# 3. Storage constraints — the shapes that must not be storable
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo95_only_one_org_standard_can_exist(db_session):
    """The partial unique index. A plain UNIQUE would not catch this: SQL treats
    NULLs as distinct, so two `entity_id IS NULL` rows would both store and the
    resolution walk would have to pick one arbitrarily."""
    org_id, _entity_id = await _org_with_entity(db_session)
    await fee.set_rate(db_session, org_id, fee_pct=Decimal("15.00"), fee_min=Decimal("0.00"))
    await db_session.commit()

    db_session.add(
        VatFeeRate(
            org_id=org_id,
            entity_id=None,
            country=ANY_COUNTRY,
            fee_pct=Decimal("9.00"),
            fee_min=Decimal("0.00"),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_wo95_an_org_level_rate_cannot_carry_a_country(db_session):
    """Neither C11 nor R40 describes an org-level PER-COUNTRY rung, so the shape
    is refused by the service AND by the database — a shape the specification
    does not describe is not stored."""
    org_id, _entity_id = await _org_with_entity(db_session)
    with pytest.raises(AppError) as exc:
        await fee.set_rate(
            db_session, org_id, country="LV", fee_pct=Decimal("15.00"), fee_min=Decimal("0.00")
        )
    assert exc.value.code == "org_rate_has_no_country"

    db_session.add(
        VatFeeRate(
            org_id=org_id,
            entity_id=None,
            country="LV",
            fee_pct=Decimal("15.00"),
            fee_min=Decimal("0.00"),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_wo95_a_rate_above_one_hundred_percent_is_refused(db_session):
    """A contingency fee larger than the whole recovery would bill a client more
    than the refund. Refused at the service and at the database."""
    org_id, _entity_id = await _org_with_entity(db_session)
    with pytest.raises(AppError) as exc:
        await fee.set_rate(db_session, org_id, fee_pct=Decimal("101.00"), fee_min=Decimal("0.00"))
    assert exc.value.code == "invalid_fee_pct"

    db_session.add(
        VatFeeRate(
            org_id=org_id,
            entity_id=None,
            country=ANY_COUNTRY,
            fee_pct=Decimal("101.00"),
            fee_min=Decimal("0.00"),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_wo95_a_negative_minimum_is_refused(db_session):
    """A negative floor would mean the minimum of a charge is a refund to the
    client — C11's `max()` cannot express that."""
    org_id, _entity_id = await _org_with_entity(db_session)
    with pytest.raises(AppError) as exc:
        await fee.set_rate(db_session, org_id, fee_pct=Decimal("15.00"), fee_min=Decimal("-1.00"))
    assert exc.value.code == "invalid_fee_min"


@pytest.mark.asyncio
async def test_wo95_a_zero_rate_typed_by_a_human_is_accepted_and_resolves(db_session):
    """The counterpart to the refusal: `(0, 0)` is storable, resolvable and
    audited. The engine refuses ABSENCE, not zero."""
    org_id, entity_id = await _org_with_entity(db_session)
    await fee.set_rate(db_session, org_id, fee_pct=Decimal("0.00"), fee_min=Decimal("0.00"))
    await db_session.commit()

    rate = await fee.resolve_fee_rate(db_session, org_id, entity_id=entity_id, country="LV")
    assert (rate.fee_pct, rate.fee_min) == (Decimal("0.00"), Decimal("0.00"))


# --------------------------------------------------------------------------- #
# 4. The CRUD — gates, audit, idempotence, org scoping
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo95_set_rate_audits_old_to_new(db_session):
    """§4.16 — the pair that decides what a client is billed always carries an
    actor and both sides of the change, in the same transaction."""
    org_id, _entity_id = await _org_with_entity(db_session)
    await fee.set_rate(db_session, org_id, fee_pct=Decimal("15.00"), fee_min=Decimal("250.00"))
    await fee.set_rate(db_session, org_id, fee_pct=Decimal("12.50"), fee_min=Decimal("200.00"))
    await db_session.commit()

    events = await _fee_events(db_session, org_id, "transport.fee_rate_set")
    assert len(events) == 2
    created, changed = (_meta(e) for e in events)
    assert (created["old_fee_pct"], created["new_fee_pct"]) == (None, "15.00")
    assert (changed["old_fee_pct"], changed["new_fee_pct"]) == ("15.00", "12.50")
    assert (changed["old_fee_min"], changed["new_fee_min"]) == ("250.00", "200.00")
    assert changed["scope"] == "org"


@pytest.mark.asyncio
async def test_wo95_set_rate_is_a_no_op_when_nothing_changed(db_session):
    """The `excise.set_rate` / `contract_audit.set_term` convention — a repeat
    write of identical values audits nothing, so the trail records decisions
    rather than saves."""
    org_id, _entity_id = await _org_with_entity(db_session)
    await fee.set_rate(db_session, org_id, fee_pct=Decimal("15.00"), fee_min=Decimal("250.00"))
    await fee.set_rate(db_session, org_id, fee_pct=Decimal("15.00"), fee_min=Decimal("250.00"))
    await db_session.commit()

    assert len(await _fee_events(db_session, org_id, "transport.fee_rate_set")) == 1


@pytest.mark.asyncio
async def test_wo95_remove_rate_audits_and_falls_through_to_the_next_rung(db_session):
    """Removing the per-country override drops resolution to the customer
    default; removing that one drops it to the org standard; removing THAT one
    reaches the refusal."""
    org_id, entity_id = await _org_with_entity(db_session)
    await fee.set_rate(db_session, org_id, fee_pct=Decimal("15.00"), fee_min=Decimal("0.00"))
    await fee.set_rate(
        db_session, org_id, entity_id=entity_id, fee_pct=Decimal("9.00"), fee_min=Decimal("0.00")
    )
    await db_session.commit()

    assert await fee.remove_rate(db_session, org_id, entity_id=entity_id) is True
    await db_session.commit()
    rate = await fee.resolve_fee_rate(db_session, org_id, entity_id=entity_id, country="LV")
    assert rate.scope == "org"

    assert await fee.remove_rate(db_session, org_id) is True
    await db_session.commit()
    with pytest.raises(AppError) as exc:
        await fee.resolve_fee_rate(db_session, org_id, entity_id=entity_id, country="LV")
    assert exc.value.code == "fee_rate_not_configured"

    removals = await _fee_events(db_session, org_id, "transport.fee_rate_remove")
    assert len(removals) == 2
    assert _meta(removals[0])["old_fee_pct"] == "9.00"
    assert _meta(removals[0])["new_fee_pct"] is None


@pytest.mark.asyncio
async def test_wo95_remove_rate_returns_false_when_there_was_nothing_to_remove(db_session):
    org_id, _entity_id = await _org_with_entity(db_session)
    assert await fee.remove_rate(db_session, org_id) is False


@pytest.mark.asyncio
async def test_wo95_a_rate_for_an_unknown_entity_is_an_opaque_404(db_session):
    """§4.4 — a cross-tenant entity id is indistinguishable from an unknown
    one. Tenant B's real entity is passed to tenant A's session."""
    org_a, _entity_a = await _org_with_entity(db_session)
    org_b = await make_org(db_session, name="Other Transport Org")
    await enable_transport(db_session, org_b.id, fee_rate=False)
    entity_b = await make_entity(db_session, org_b.id, seed=3)
    await db_session.commit()

    with pytest.raises(AppError) as exc:
        await fee.set_rate(
            db_session,
            org_a,
            entity_id=entity_b.id,
            fee_pct=Decimal("15.00"),
            fee_min=Decimal("0.00"),
        )
    assert exc.value.code == "entity_not_found"


@pytest.mark.asyncio
async def test_wo95_two_orgs_with_identical_rates_never_see_each_others_rows(db_session):
    """Overlapping data (template rule 3): both orgs configure the SAME numbers,
    so a broken filter cannot pass by accident on distinct values."""
    org_a, entity_a = await _org_with_entity(db_session)
    org_b = await make_org(db_session, name="Other Transport Org")
    await enable_transport(db_session, org_b.id, fee_rate=False)
    entity_b = await make_entity(db_session, org_b.id, seed=4)
    await db_session.commit()

    for org_id in (org_a, org_b.id):
        await fee.set_rate(db_session, org_id, fee_pct=Decimal("15.00"), fee_min=Decimal("250.00"))
    await fee.set_rate(
        db_session,
        org_b.id,
        entity_id=entity_b.id,
        fee_pct=Decimal("1.00"),
        fee_min=Decimal("0.00"),
    )
    await db_session.commit()

    assert len(await fee.list_rates(db_session, org_a)) == 1
    assert len(await fee.list_rates(db_session, org_b.id)) == 2
    # A's entity resolves at A's org standard, never at B's 1% client rate.
    rate = await fee.resolve_fee_rate(db_session, org_a, entity_id=entity_a, country="LV")
    assert (rate.fee_pct, rate.scope) == (Decimal("15.00"), "org")


@pytest.mark.asyncio
async def test_wo95_every_entry_point_is_module_gated(db_session):
    """ADR-P3 rule 3 — an un-entitled org is byte-identical to before the
    vertical existed, and the gate is on the SERVICE, never delegated."""
    org = await make_org(db_session)
    entity = await make_entity(db_session, org.id)
    await db_session.commit()

    for call in (
        lambda: fee.resolve_fee_rate(db_session, org.id, entity_id=entity.id, country="LV"),
        lambda: fee.set_rate(db_session, org.id, fee_pct=Decimal("15.00"), fee_min=Decimal("0.00")),
        lambda: fee.remove_rate(db_session, org.id),
        lambda: fee.list_rates(db_session, org.id),
    ):
        with pytest.raises(AppError) as exc:
            await call()
        assert exc.value.code == "module_not_enabled"
