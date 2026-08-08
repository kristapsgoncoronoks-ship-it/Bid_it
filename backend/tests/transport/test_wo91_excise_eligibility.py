"""WO-91 — the eligibility limitation, proven STRUCTURAL rather than editorial.

`BA_fleet_fuel.md` §3.L, verbatim: *"`excise.py` | **Asserts NO eligibility**
(vehicle >=7.5t / carrier registration not modelled); rates are indicative
defaults."* R42 repeats it as a requirement — *"the figure **asserts NO
eligibility** — surfaced loudly"* — with the acceptance *"The UI shows the
indicative-rate and eligibility caveats on **every surface that shows the
number**"*. §9.2 item 14 files the underlying question as an OPEN owner
decision: *"**Who confirms eligibility for excise** (vehicle >= 7.5 t, carrier
registration)? It is deliberately not modelled."*

WHY THIS FILE EXISTS AT ALL
-----------------------------
The excise figure is the most misreadable number this platform produces. It
looks exactly like recovered cash — a euro amount, per legal entity, per
country, over invoices the client already holds — and it is not. A haulier that
does not meet the weight threshold or is not a registered carrier is entitled to
none of it, and this product does not know which haulier that is. Put on a
customs packet as an entitlement, it is an overstatement to a public authority
under the client's own name.

R42's acceptance line would be satisfied by a caveat string somewhere on a
screen. That is not enough, for the same reason WO-87 gave for R53: a caveat is
a sentence a redesign drops, a translation shortens, or a summary card omits.
So this file asserts something stronger and harder to erode, in four
independent ways — the WO-87 pattern applied to a stricter claim:

1. the LIMITATION IS ONE CONSTANT, it names both conditions the spec names, and
   it denies the entitlement reading in words;
2. that constant, its rate twin and R53's framing are REQUIRED fields on every
   result shape and every response schema, alongside a boolean
   `eligibility_asserted` that survives a surface which truncates prose;
3. the VOCABULARY of everything this surface emits is free of claim words —
   there is no `recoverable_eur` for a client to render as an entitlement;
4. the excise family shares NO code path with the claim-back family, in either
   direction, so no excise euro can reach the number a 30-day demand prints.

Assertions 2-4 are ABSENCES, and each scanner ships a seeded-violation
self-test (`WORK_ORDER_TEMPLATE.md` rule 6). "We simply did not write one" is a
fact about today's file; an asserted absence is a fact about tomorrow's.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

from pydantic import BaseModel

from app.api.routes.transport import excise as excise_route
from app.schemas import transport_excise
from app.services.transport import contract_audit, excise, savings

# The claim vocabulary is WO-87's list, IMPORTED rather than re-typed: the two
# surfaces answer to the same rule (R53) and a word added to one must bind the
# other. Re-declaring it here is exactly the drift this import prevents.
from tests.transport.test_wo87_r53_framing import CLAIM_WORDS

BACKEND = Path(__file__).resolve().parents[2]
SERVICES = BACKEND / "app" / "services" / "transport"
ROUTES = BACKEND / "app" / "api" / "routes" / "transport"

# The two conditions the spec NAMES as unmodelled, and the only two. §3.L and
# §9.2 item 14 both give exactly this pair.
UNMODELLED_CONDITIONS = ("7.5", "carrier registration")


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
# 1. The limitation is ONE constant, and it says what the spec says
# --------------------------------------------------------------------------- #


def test_wo91_the_eligibility_statement_names_both_unmodelled_conditions():
    """§3.L names two conditions and §9.2 item 14 names the same two. If a
    future edit dropped one, a reader would be told the figure is qualified
    without being told by what."""
    statement = excise.ELIGIBILITY_STATEMENT
    for condition in UNMODELLED_CONDITIONS:
        assert condition in statement, f"the eligibility statement lost {condition!r}"
    assert "not modelled" in statement


def test_wo91_the_eligibility_statement_denies_the_entitlement_reading():
    """The whole point: it must say the figure is NOT money owed, not merely
    that it is approximate. "Indicative" alone would leave a reader believing
    the entitlement exists and only the amount is uncertain."""
    statement = excise.ELIGIBILITY_STATEMENT
    assert "asserts NO eligibility" in statement
    assert "if you qualify" in statement.lower()
    assert "never as excise you are owed" in statement.lower()
    # And it says who confirms it, so the reader has somewhere to go.
    assert "customs" in statement.lower()


def test_wo91_the_rate_caveat_states_the_harvested_placeholder_and_its_band():
    """§2.4 / Appendix B call the default an explicit PLACEHOLDER in a reported
    EUR 25-33 band, and §6.1 records that the duty is not harmonised and the
    rates are revised. All three clauses must survive in the caveat, because
    together they are the reason `is_override` exists."""
    caveat = excise.RATE_CAVEAT
    assert "placeholder" in caveat.lower()
    assert "30.00" in caveat and "25-33" in caveat
    assert "not harmonised" in caveat
    assert "verify" in caveat.lower()


def test_wo91_the_addressee_is_customs_and_the_regime_is_named_separate():
    """§2.4 *"Separate regime, filed with CUSTOMS"*; §1.2 lists customs
    authorities as their own external party. A surface that lost this would
    invite the figure onto a VAT filing, where it does not belong."""
    assert "ustoms" in excise.FILED_WITH
    assert "separate regime" in excise.FILED_WITH.lower()
    assert "VAT refund" in excise.FILED_WITH


# --------------------------------------------------------------------------- #
# 2. The constants are REQUIRED on every shape that carries the number
# --------------------------------------------------------------------------- #

# Every shape a client can receive that carries a euro or a rate — R42's
# *"every surface that shows the number"*, enumerated so a new one cannot be
# added without a decision.
NUMBER_BEARING_SHAPES = (
    (excise.ExciseReport, ("eligibility", "eligibility_asserted", "rate_caveat", "legal_framing")),
    (
        transport_excise.ExciseReportOut,
        ("eligibility", "eligibility_asserted", "rate_caveat", "legal_framing"),
    ),
    (
        transport_excise.ExciseRatesOut,
        ("eligibility", "eligibility_asserted", "rate_caveat", "legal_framing"),
    ),
)


def _missing_constants(shape: type, required: tuple[str, ...]) -> list[str]:
    names = set(_field_names(shape))
    return [field for field in required if field not in names]


def test_wo91_every_number_bearing_shape_carries_the_caveats():
    """R42's acceptance, as a property of the DATA rather than of a screen: the
    number is un-renderable without the denial next to it."""
    offenders = []
    for shape, required in NUMBER_BEARING_SHAPES:
        missing = _missing_constants(shape, required)
        if missing:
            offenders.append(f"{shape.__name__} is missing {missing}")
    assert offenders == [], offenders


def test_wo91_the_constant_scan_would_catch_a_shape_missing_the_statement():
    """`WORK_ORDER_TEMPLATE.md` rule 6 — a coverage test that cannot fail proves
    nothing. A shape carrying the euro and none of the caveats is detected."""

    @dataclasses.dataclass(frozen=True)
    class _Seeded:
        indicative_excise_eur: int

    missing = _missing_constants(_Seeded, ("eligibility", "eligibility_asserted"))
    assert missing == ["eligibility", "eligibility_asserted"]


def test_wo91_eligibility_asserted_is_a_literal_false_everywhere_it_is_produced():
    """The boolean exists so the denial survives a UI that truncates prose. It
    is `False` at the SERVICE (the report) and at the ROUTE (the rate view),
    which are the only two places it is produced — asserted by reading the
    sources, because a value nothing sets to `True` today could be set
    tomorrow."""
    for path in (SERVICES / "excise.py", ROUTES / "excise.py"):
        source = path.read_text(encoding="utf-8")
        assert "eligibility_asserted=True" not in source, f"{path} asserts eligibility"
        assert "eligibility_asserted=False" in source, f"{path} never denies eligibility"


# --------------------------------------------------------------------------- #
# 3. The vocabulary — every field name a client can render
# --------------------------------------------------------------------------- #


def test_wo91_the_excise_surface_carries_no_claim_back_vocabulary():
    """An excise figure is not money anyone owes, so nothing this surface EMITS
    may be named as though it were. A `recoverable_eur` field would be read as
    an entitlement the moment a client rendered it, regardless of the caveat
    string beside it.

    Both layers are scanned — the service result shapes and the wire schemas —
    because a rename in either would reach a screen.
    """
    offenders: list[str] = []
    for module in (excise, transport_excise):
        for shape in _public_types(module):
            for field in _field_names(shape):
                lowered = field.lower()
                for word in CLAIM_WORDS:
                    if word in lowered:
                        offenders.append(f"{module.__name__}.{shape.__name__}.{field} ~ {word!r}")
    assert offenders == [], (
        f"R53/R42: the excise surface must not use contractual-claim vocabulary — {offenders}"
    )


def test_wo91_the_euro_field_carries_its_own_qualification():
    """The positive half of the same rule. The name itself must say the figure
    is indicative, because a renderer can lose a caveat string and cannot lose
    an identifier."""
    for shape in (excise.ExciseCell, excise.ExciseReport, transport_excise.ExciseReportOut):
        assert "indicative_excise_eur" in _field_names(shape), shape.__name__


def test_wo91_the_scan_would_catch_a_claim_shaped_field_name():
    """`WORK_ORDER_TEMPLATE.md` rule 6 — the vocabulary scanner's self-test."""

    @dataclasses.dataclass(frozen=True)
    class _Seeded:
        recoverable_excise_eur: int

    hits = [f for f in _field_names(_Seeded) if any(w in f.lower() for w in CLAIM_WORDS)]
    assert hits == ["recoverable_excise_eur"]


def test_wo91_the_excise_route_paths_carry_no_claim_vocabulary():
    """The URL is vocabulary too — `/transport/excise/...`, never
    `/transport/excise-claims/...`."""
    paths = [r.path for r in excise_route.router.routes]  # type: ignore[attr-defined]
    assert paths, "the excise router registered no route"
    for path in paths:
        for word in CLAIM_WORDS:
            assert word not in path.lower(), f"{path} ~ {word!r}"


# --------------------------------------------------------------------------- #
# 4. R53's THREE framings stay distinct, and the families stay unconnected
# --------------------------------------------------------------------------- #


def test_wo91_the_third_framing_is_the_specs_own_and_differs_from_the_other_two():
    """R53: *"contract breach = 'money the supplier owes'; same-day overpay =
    'negotiation evidence, NOT a contractual claim-back'; peer/excise/estimate =
    'indicative, verify'."* Three analyses, three framings, and §2.4's framing
    table gives this one its words."""
    assert excise.LEGAL_FRAMING == "Indicative / advisory — verify before relying"
    assert excise.LEGAL_FRAMING != contract_audit.LEGAL_FRAMING
    assert excise.LEGAL_FRAMING != savings.LEGAL_FRAMING
    # It asserts nothing about a debt, in either direction's vocabulary.
    for word in ("owes", "owed", "claim"):
        assert word not in excise.LEGAL_FRAMING.lower()


def _imported_module_names(path: Path) -> set[str]:
    """Every module name this file imports, at any depth of the dotted path,
    plus the leaf names it imports FROM a package."""
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


def test_wo91_the_excise_figure_is_not_reachable_from_the_claim_letter_path():
    """The structural half. `overcharge_pack.build_claim_letter` prints
    `overcharge.detected_eur` inside a 30-day credit/refund demand. If any
    expression could carry an excise euro into that number, the letter would be
    demanding payment of a state's excise duty from a fuel supplier.

    An import scan in BOTH directions over the SERVICE and ROUTE modules of each
    family — the WO-87 proof, extended to the third framing.
    """
    excise_imports = _imported_module_names(SERVICES / "excise.py") | _imported_module_names(
        ROUTES / "excise.py"
    )
    leaked = sorted(m for m in CLAIM_BACK_MODULES if m in excise_imports)
    assert leaked == [], f"the excise surface imports the claim-back family: {leaked}"

    for name in CLAIM_BACK_MODULES:
        for base in (SERVICES, ROUTES):
            path = base / f"{name}.py"
            if not path.exists():
                continue
            assert "excise" not in _imported_module_names(path), (
                f"{path} imports the excise analysis — an excise euro must never be able to "
                "reach overcharge.detected_eur (R53)"
            )
    # `overcharges.py` is the route module for the claim-back family; its file
    # name differs from its service, so it is named explicitly.
    assert "excise" not in _imported_module_names(ROUTES / "overcharges.py")


def test_wo91_the_import_scan_sees_a_seeded_import(tmp_path):
    """The scanner's own self-test (`WORK_ORDER_TEMPLATE.md` rule 6)."""
    seeded = tmp_path / "seeded.py"
    seeded.write_text(
        "from app.services.transport import overcharge_pack\n"
        "import app.services.transport.excise\n",
        encoding="utf-8",
    )
    names = _imported_module_names(seeded)
    assert "overcharge_pack" in names
    assert "excise" in names
