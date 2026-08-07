"""WO-87 — R53's two framings, proven STRUCTURALLY separate.

`BA_fleet_fuel.md` R53, verbatim: *"Legal framing per analysis must not be
flattened: contract breach = 'money the supplier owes' (claim letter); same-day
overpay = 'negotiation evidence, NOT a contractual claim-back' (printed on every
sheet); peer/excise/estimate = 'indicative, verify'."*

WHY THIS FILE EXISTS AT ALL
-----------------------------
WO-83 made the FIRST framing client-reachable: a formal PDF letter on our
client's letterhead demanding a credit or refund within 30 days, quoting a euro
figure. WO-87 adds the SECOND analysis. If the two ever read alike, the platform
would be helping a client assert to a counterparty that an overpay figure is a
debt — and it is not one. Nobody agreed that a supplier would match the cheapest
rival network on the day; there is no term, so there is no breach, so nothing is
owed. That is a false assertion with a real addressee, not a wording preference.

R53's own acceptance line is *"each workbook carries its framing text"*, which a
single string comparison would satisfy. This file asserts something stronger and
harder to erode, in four independent ways:

1. the two framing CONSTANTS are different strings and each says what it means;
2. the overpay surface's VOCABULARY — every dataclass field, every schema field,
   every route path — is free of claim-back words, so there is no
   `recoverable_eur` for a client to render as a demand;
3. the two families share NO code path: neither imports the other, in either
   direction, so no overpay euro can reach `overcharge.detected_eur` (the figure
   the demand letter prints) even by a future accident;
4. the overpay route module declares NO write verb — there is nothing to open,
   freeze, package or send, which is what makes "this is not a claim" a property
   of the surface rather than a sentence on it.

Assertions 2-4 are ABSENCES. "We simply did not write one" is a fact about
today's file; an asserted absence is a fact about tomorrow's.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

from pydantic import BaseModel

from app.api.routes.transport import savings as savings_route
from app.schemas import transport_savings
from app.services.transport import contract_audit, overcharge, overcharge_pack, savings

BACKEND = Path(__file__).resolve().parents[2]
SERVICES = BACKEND / "app" / "services" / "transport"
ROUTES = BACKEND / "app" / "api" / "routes" / "transport"

# The vocabulary of a CONTRACTUAL claim — the words that turn a figure into an
# assertion that money is owed. None of them may appear in anything the overpay
# surface emits as DATA (a field name a client renders), which is a different
# and stricter question than what the prose may say.
CLAIM_WORDS = ("recover", "owed", "owes", "claim", "demand", "due", "debt", "payable")


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


# --------------------------------------------------------------------------- #
# 1. The two framings are different, and each says what it means
# --------------------------------------------------------------------------- #


def test_wo87_the_two_legal_framings_are_different_strings():
    """R53's two framings, side by side. The contract-breach one asserts a debt;
    the overpay one denies being one. A future edit that softened either into
    the other's vocabulary fails here."""
    assert savings.LEGAL_FRAMING == "Negotiation evidence, NOT a contractual claim-back"
    assert savings.LEGAL_FRAMING != contract_audit.LEGAL_FRAMING

    # The claim-back family asserts money is owed …
    assert "owes" in contract_audit.LEGAL_FRAMING
    # … and the overpay family explicitly denies being that.
    assert "NOT a contractual claim-back" in savings.LEGAL_FRAMING
    assert "owes" not in savings.LEGAL_FRAMING
    assert "owed" not in savings.LEGAL_FRAMING


def test_wo87_the_framing_and_the_basis_ride_every_overpay_result():
    """R53's acceptance is *"each workbook carries its framing text"* and §3.G
    G1's is *"this basis must be stated on any new report surface"*. Both are
    REQUIRED fields on all three result shapes and all three response schemas,
    so no surface can render a number without them."""
    for shape in (
        savings.SameDayOverpayResult,
        savings.InternalBenchmarkResult,
        savings.ExpectedRebateResult,
    ):
        names = _field_names(shape)
        assert "legal_framing" in names, shape.__name__
        assert "price_basis" in names, shape.__name__
        assert "currency" in names, shape.__name__

    for out in (
        transport_savings.SameDayOverpayOut,
        transport_savings.InternalBenchmarkOut,
        transport_savings.ExpectedRebateOut,
    ):
        names = _field_names(out)
        assert "legal_framing" in names, out.__name__
        assert "price_basis" in names, out.__name__


def test_wo87_r52_grains_are_labelled_distinctly():
    """R53's sibling rule. R52: *"Both figures appear with their grain in the
    label; no surface implies they should match."* The two grain strings are
    R52's own descriptions and the two euro fields are named differently, so a
    consumer cannot add them without writing two different attribute names."""
    assert savings.GRAIN_SAME_DAY != savings.GRAIN_COUNTRY_MONTH
    assert "same-day" in savings.GRAIN_SAME_DAY
    assert "month" in savings.GRAIN_COUNTRY_MONTH

    same_day = set(_field_names(savings.SameDayOverpayResult))
    monthly = set(_field_names(savings.InternalBenchmarkResult))
    assert "avoidable_eur" in same_day and "avoidable_eur" not in monthly
    assert "benchmark_gap_eur" in monthly and "benchmark_gap_eur" not in same_day


# --------------------------------------------------------------------------- #
# 2. The vocabulary — every field name a client can render
# --------------------------------------------------------------------------- #


def test_wo87_the_overpay_surface_carries_no_claim_back_vocabulary():
    """An overpay figure is not a debt, so nothing this surface EMITS may be
    named as though it were. A `recoverable_eur` field would be read as a demand
    the moment a client rendered it, regardless of what the framing string next
    to it said.

    Both layers are scanned — the service result shapes and the wire schemas —
    because a rename in either would reach a screen.
    """
    offenders: list[str] = []
    for module in (savings, transport_savings):
        for shape in _public_types(module):
            for field in _field_names(shape):
                lowered = field.lower()
                for word in CLAIM_WORDS:
                    if word in lowered:
                        offenders.append(f"{module.__name__}.{shape.__name__}.{field} ~ {word!r}")
    assert offenders == [], (
        f"R53: the overpay surface must not use contractual-claim vocabulary — {offenders}"
    )


def test_wo87_the_scan_would_catch_a_claim_shaped_field_name():
    """`WORK_ORDER_TEMPLATE.md` rule 6 — a coverage test that cannot fail proves
    nothing. A deliberately claim-shaped field name is detected."""
    import dataclasses as dc

    @dc.dataclass(frozen=True)
    class _Seeded:
        recoverable_eur: int

    hits = [f for f in _field_names(_Seeded) if any(w in f.lower() for w in CLAIM_WORDS)]
    assert hits == ["recoverable_eur"]


def test_wo87_the_overpay_route_paths_carry_no_claim_vocabulary():
    """The URL is vocabulary too — `/transport/savings/...`, never
    `/transport/overpay-claims/...`."""
    paths = [r.path for r in savings_route.router.routes]  # type: ignore[attr-defined]
    assert paths, "the savings router registered no route"
    for path in paths:
        for word in CLAIM_WORDS:
            assert word not in path.lower(), f"{path} ~ {word!r}"


# --------------------------------------------------------------------------- #
# 3. No shared code path with the claim-back family, in EITHER direction
# --------------------------------------------------------------------------- #


def _imported_module_names(path: Path) -> set[str]:
    """Every module name this file imports, at any depth of the dotted path,
    plus the leaf names it imports FROM a package (so `from app.services.
    transport import overcharge` is seen)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.update(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.update(node.module.split("."))
            for alias in node.names:
                names.add(alias.name)
    return names


CLAIM_BACK_MODULES = ("contract_audit", "overcharge", "overcharge_pack")


def test_wo87_the_overpay_analysis_is_not_reachable_from_the_claim_letter_path():
    """The structural half of R53, and the assertion this order was written
    around.

    `overcharge.open_claim` FREEZES `detected_eur` — the euro
    `overcharge_pack.build_claim_letter` prints inside a 30-day credit/refund
    demand. If any expression could carry an overpay figure into that euro, the
    letter would be demanding payment for something no contract promised.

    The proof is an import scan in BOTH directions over the SERVICE and ROUTE
    modules of each family. Direction one: `savings` cannot compute anything
    from the claim-back modules. Direction two — the dangerous one — no
    claim-back module can read `savings`.
    """
    savings_imports = _imported_module_names(SERVICES / "savings.py") | _imported_module_names(
        ROUTES / "savings.py"
    )
    leaked = sorted(m for m in CLAIM_BACK_MODULES if m in savings_imports)
    assert leaked == [], f"the overpay surface imports the claim-back family: {leaked}"

    for name in CLAIM_BACK_MODULES:
        for base in (SERVICES, ROUTES):
            path = base / f"{name}.py"
            if not path.exists():
                continue
            assert "savings" not in _imported_module_names(path), (
                f"{path} imports the overpay analysis — an overpay euro must never be "
                "able to reach overcharge.detected_eur (R53)"
            )
    # `overcharges.py` is the route module for the claim-back family; its file
    # name differs from its service, so it is named explicitly.
    assert "savings" not in _imported_module_names(ROUTES / "overcharges.py")


def test_wo87_the_import_scan_sees_a_seeded_import(tmp_path):
    """The scanner's own self-test (`WORK_ORDER_TEMPLATE.md` rule 6)."""
    seeded = tmp_path / "seeded.py"
    seeded.write_text(
        "from app.services.transport import overcharge_pack\nimport app.services.transport.savings\n",
        encoding="utf-8",
    )
    names = _imported_module_names(seeded)
    assert "overcharge_pack" in names
    assert "savings" in names


def test_wo87_detected_eur_still_comes_only_from_the_contract_audit():
    """The positive half of the same guarantee: the claim-back euro is computed
    where it always was. `overcharge` reads `contract_audit` and nothing else
    that computes a euro — so R53's first framing keeps its one source, and this
    order changed nothing about it."""
    source = (SERVICES / "overcharge.py").read_text(encoding="utf-8")
    assert "contract_audit" in source
    assert "savings" not in source
    assert overcharge.__name__.endswith("overcharge")
    assert overcharge_pack.__name__.endswith("overcharge_pack")


# --------------------------------------------------------------------------- #
# 4. No lifecycle, no artifact, no write verb
# --------------------------------------------------------------------------- #


def test_wo87_the_savings_route_module_exposes_no_write_verb():
    """`overcharges.py` has `VAT_WRITE` overrides because a contractual
    claim-back has a lifecycle to advance and a letter to issue. This surface has
    neither — there is nothing to open, freeze, package or send — so every route
    is a GET. Asserted, not merely true today."""
    methods: set[str] = set()
    for route in savings_route.router.routes:
        methods |= set(getattr(route, "methods", set()))
    assert methods == {"GET"}, f"the overpay surface declares a write verb: {sorted(methods)}"


def test_wo87_the_savings_service_persists_nothing():
    """§4.19, asserted structurally rather than only behaviourally (the
    behavioural proof — every transaction column compared before and after — is
    `test_wo87_savings.py::test_wo87_the_analyses_write_nothing`).

    No `db.add`, no `db.delete`, no `db.flush`, no `db.commit`, and no audit
    event: an advisory analysis that emitted an audit row would be claiming to
    have changed something.
    """
    tree = ast.parse((SERVICES / "savings.py").read_text(encoding="utf-8"))
    # Only calls whose RECEIVER is the session are relevant: `days[key].add(...)`
    # on a local `set` is ordinary Python, while `db.add(...)` is a write.
    on_session: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "db"
        ):
            on_session.add(node.func.attr)
    assert on_session == {"scalars"}, f"the savings service touches the session with {on_session}"

    # No attribute assignment onto a loaded ORM row either — that is how
    # `rebate.merge_period` legitimately writes `net_eur_eff`, and it must not
    # happen here.
    assigned = {
        t.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Attribute)
    }
    assert assigned == set(), f"the savings service assigns to a loaded row: {sorted(assigned)}"

    # An advisory analysis that emitted an audit row would be claiming to have
    # changed something.
    assert "audit" not in _imported_module_names(SERVICES / "savings.py")
