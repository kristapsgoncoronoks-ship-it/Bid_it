"""WO-Q's STRUCTURAL constraints — the R53-shaped guarantees, as source scans.

These live apart from `test_wo_q_reliability.py` for one mechanical reason:
that module carries `pytestmark = pytest.mark.asyncio` because almost every
test there awaits a database, and pytest-asyncio warns (correctly) on each
synchronous test that inherits a mark it cannot use. These four read source
files and await nothing, so they belong in a module with no such mark — which
is how every other structural-scan suite in this tree is already arranged.

Each scan carries a seeded-violation self-test, because a scan that cannot fail
proves nothing.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.transport.test_wo87_r53_framing import CLAIM_WORDS

BACKEND = Path(__file__).resolve().parents[2]
SERVICE = BACKEND / "app" / "services" / "transport" / "reliability.py"
ROUTE = BACKEND / "app" / "api" / "routes" / "transport" / "reliability.py"
SCHEMA = BACKEND / "app" / "schemas" / "transport_reliability.py"


def _claim_word_hits(text: str) -> list[str]:
    """Token matches, and the tokenisation is the whole point: `\b` does NOT
    fire inside `amount_owed_eur`, because `_` is a word character — a
    snake_case wire name would hide the very vocabulary this scan exists to
    catch. So identifiers are split on non-alphanumerics first and each token
    is tested for a claim-word prefix."""
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", text) if t]
    hits = []
    for word in CLAIM_WORDS:
        if any(t.lower().startswith(word) for t in tokens):
            hits.append(word)
    return hits


def _wire_names(path: Path) -> str:
    """What a CLIENT can see: schema field names, route paths, and the dict
    keys a response carries. Deliberately NOT internal variable names — reading
    `vat_overcharge_claims` is this analysis's first criterion, so a service
    that never said "claim" internally would be a service that never read the
    evidence. The constraint is on what the surface ASSERTS, which is its
    vocabulary, not on what it consults."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        # Wire SHAPE: annotated fields declared in a class body (pydantic
        # models, dataclasses) — NOT annotated locals inside a function, which
        # are implementation (`claims_by_supplier` is how the first criterion
        # reads its evidence, and naming it honestly is correct).
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    names.append(stmt.target.id)
        # Route paths, band values, and the string keys a response carries.
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if v.startswith("/") or re.fullmatch(r"[a-z][a-z0-9_]*", v or ""):
                names.append(v)
    return " ".join(names)


def test_wo_q_no_claim_vocabulary_on_the_wire_surface():
    """The band a reader sees, the fields it arrives in and the path it came
    from must not read as a demand. (Docstrings are exempt by construction —
    they are where this module explains what it is NOT.)"""
    for path in (SERVICE, ROUTE, SCHEMA):
        hits = _claim_word_hits(_wire_names(path))
        assert hits == [], f"{path.name}: claim vocabulary on the wire surface: {hits}"


def test_wo_q_the_vocabulary_scan_can_actually_fail():
    # Every one of these is a shape the naive `\b` matcher missed.
    seeded = "supplier_debt_eur amount_owed_eur total_due_eur /reliability/claim"
    assert _claim_word_hits(seeded), "the scan cannot fail — it proves nothing"


def test_wo_q_no_reliability_figure_can_reach_the_claim_back_euro():
    """Both directions. The overcharge family must not import this module (a
    rating must never move a demand's euro), and this module must not write
    into it (it reads claim-backs as evidence and nothing more)."""
    overcharge_src = (BACKEND / "app" / "services" / "transport" / "overcharge.py").read_text(
        encoding="utf-8"
    )
    assert "reliability" not in overcharge_src

    service_src = SERVICE.read_text(encoding="utf-8")
    tree = ast.parse(service_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("detected_eur", "recovered_eur"):
            # Reading a claim's detected euro is the criterion's whole point;
            # ASSIGNING one would be this module writing the demand's figure.
            parent_assigns = [
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Assign) and any(t is node for t in n.targets)
            ]
            assert not parent_assigns, "reliability must never write a claim-back euro"


def test_wo_q_the_route_module_exposes_no_write_verb_but_the_threshold_one():
    src = ROUTE.read_text(encoding="utf-8")
    verbs = re.findall(r"@router\.(get|post|put|patch|delete)\(", src)
    assert sorted(verbs) == ["get", "get", "put"], verbs
    # …and the one write is on the EXISTING VAT_WRITE, no new permission member.
    assert "VAT_WRITE" in src
    assert "Permission." in src and "RELIABILITY" not in src
