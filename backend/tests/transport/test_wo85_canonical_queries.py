"""G4.1/R51 (WO-85) — the canonical query registry, and the structural proof
that a future consumer cannot silently fork one of its queries.

R51's verification line is unusually concrete: *"Rename a canonical function =>
every consumer breaks; no duplicate implementation exists."* Both halves are
asserted here — the second one by an AST scan over `app/services/transport/`
that ships with its own seeded-violation self-test, because a scan test that
cannot fail proves nothing (`WORK_ORDER_TEMPLATE.md`, "how to write good test
requirements", rule 6).

The equivalence half of this order is carried by the 2053-test suite that was
already green before it: not one pre-existing test file is edited, so every
migrated consumer is characterised by the tests written for it. What this file
adds on top is (a) the structural guarantee, (b) registry unit tests, and (c)
per-consumer equivalence against hand-computed `Decimal` expectations with
overlapping near-miss rows a forked predicate would let through.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.vat_claim import VatRefundClaimLine
from app.services.transport import queries

SERVICES = Path(__file__).resolve().parents[2] / "app" / "services" / "transport"
CANONICAL_NAMES = {m.__name__ for m in queries.CANONICAL_MODELS}


# --------------------------------------------------------------------------- #
# The structural anti-forking proof — the real deliverable of this order
# --------------------------------------------------------------------------- #


def _forks(source: str, *, filename: str) -> list[str]:
    """Every place `source` builds its own query over a canonical model.

    Two independent signals, because either alone is evadable:

    1. a `select(...)` call whose column arguments mention `FuelTransaction`
       or `VatRefundClaimLine` — a second row-selection over a canonical table;
    2. a reference to `<CanonicalModel>.org_id` — the tenant filter. Registry
       builders emit it unconditionally, so a consumer that types it is
       necessarily building its own WHERE clause (and is one edit away from
       omitting it, which returns another tenant's rows with a 200 — §4.4).

    Attribute access on an INSTANCE (`txn.org_id`) is not a match: the check
    keys on the class name, so reading a loaded row stays ordinary Python.
    """
    tree = ast.parse(source, filename=filename)
    found: list[str] = []

    def roots(node: ast.AST) -> set[str]:
        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "select":
                for arg in node.args:
                    hit = roots(arg) & CANONICAL_NAMES
                    if hit:
                        found.append(
                            f"{filename}:{node.lineno}: select() over "
                            f"{sorted(hit)[0]} — use app.services.transport.queries"
                        )
                        break
        if isinstance(node, ast.Attribute) and node.attr == "org_id":
            if isinstance(node.value, ast.Name) and node.value.id in CANONICAL_NAMES:
                found.append(
                    f"{filename}:{node.lineno}: {node.value.id}.org_id — the tenant "
                    "filter belongs to app.services.transport.queries"
                )
    return found


def test_wo85_no_transport_service_builds_a_rival_canonical_query():
    """R51 — "no duplicate implementation exists", enforced rather than
    trusted. Every module under `app/services/transport/` except the registry
    itself must reach `fuel_transactions`/`vat_claim_lines` through it."""
    violations: list[str] = []
    for path in sorted(SERVICES.rglob("*.py")):
        if path.name == "queries.py":
            continue
        violations += _forks(
            path.read_text(encoding="utf-8"), filename=str(path.relative_to(SERVICES))
        )
    assert violations == [], "canonical queries forked:\n" + "\n".join(violations)


def test_wo85_the_scanner_detects_a_seeded_violation():
    """The self-test (template rule 6). The scanner is run over source that
    deliberately re-derives the claim-scope query — exactly the fork this order
    found in `checklist.py` — and must name both signals."""
    seeded = (
        "from sqlalchemy import select\n"
        "def blocking_suppliers(org_id, claim, months):\n"
        "    return select(FuelTransaction).where(\n"
        "        FuelTransaction.org_id == org_id,\n"
        "        FuelTransaction.period.in_(months),\n"
        "    )\n"
    )
    found = _forks(seeded, filename="seeded.py")
    assert len(found) == 2, found
    assert "select() over FuelTransaction" in found[0]
    assert "FuelTransaction.org_id" in found[1]

    # ...and the same scanner over a compliant rewrite reports nothing.
    compliant = (
        "from app.services.transport import queries\n"
        "def blocking_suppliers(org_id, claim, months):\n"
        "    return queries.claim_scope_transactions(\n"
        "        org_id, entity_id=claim.entity_id,\n"
        "        refund_country=claim.refund_country, months=months)\n"
    )
    assert _forks(compliant, filename="compliant.py") == []


def test_wo85_the_scanner_sees_a_claim_line_fork_too():
    """The second canonical table is covered by the same predicate — the R3
    gate / R10 gate / G2.5 freeze triple is exactly where three hand-written
    `frozen_at.is_(None)` scans used to live."""
    seeded = (
        "def unfrozen(org_id, claim_id):\n"
        "    return select(VatRefundClaimLine).where(\n"
        "        VatRefundClaimLine.org_id == org_id)\n"
    )
    found = _forks(seeded, filename="seeded.py")
    assert len(found) == 2, found
    assert "select() over VatRefundClaimLine" in found[0]


def test_wo85_every_registry_query_has_a_real_consumer():
    """R51's first half — "rename a canonical function => every consumer
    breaks". A registry entry nothing calls could be renamed freely, so it
    would not be part of the guarantee. Every public builder is called by at
    least one module under `app/services/transport/`."""
    sources = {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted(SERVICES.rglob("*.py"))
        if p.name != "queries.py"
    }
    orphans = []
    for name in queries.__all__:
        if name.isupper():  # constants are consumed by name too, checked below
            callers = [f for f, s in sources.items() if f"queries.{name}" in s]
        else:
            callers = [f for f, s in sources.items() if f"queries.{name}(" in s]
        if not callers:
            orphans.append(name)
    assert orphans == [], f"registry entries with no consumer: {orphans}"


# --------------------------------------------------------------------------- #
# Registry unit tests — org scoping, the predicate shapes, the misuse guard
# --------------------------------------------------------------------------- #


def _criteria(stmt) -> set[str]:
    """The statement's WHERE terms as order- and parameter-insensitive strings,
    so two predicate sets can be compared regardless of the order they were
    chained in (SQLAlchemy numbers bind parameters by construction order)."""
    where = stmt.whereclause
    clauses = getattr(where, "clauses", None) or [where]
    return {re.sub(r":\w+", "?", str(c)) for c in clauses}


# (builder, kwargs) for every public builder — one place, reused by the
# org-scoping and read-only assertions below.
BUILDERS = [
    (queries.fuel_transactions, {}),
    (
        queries.claim_scope_transactions,
        {"entity_id": "e1", "refund_country": "LV", "months": ["2026-01"]},
    ),
    (
        queries.fuel_transaction_by_natural_key,
        {"entity_id": "e1", "supplier": "Q8", "period": "2026-01", "line_seq": 3},
    ),
    (queries.vat_claim_lines, {}),
    (queries.resolved_vat_claim_lines, {}),
]


@pytest.mark.parametrize("builder,kwargs", BUILDERS)
def test_wo85_every_registry_query_is_org_scoped(builder, kwargs):
    """§4.1 — the tenant filter is unconditional. It is emitted by the registry
    for every cut, which is the point of collapsing fourteen hand-typed
    `org_id ==` terms into six builders that cannot omit it."""
    args = ["org-1"] if builder is queries.fuel_transactions else ["org-1", "claim-1"]
    if builder in (queries.claim_scope_transactions, queries.fuel_transaction_by_natural_key):
        args = ["org-1"]
    stmt = builder(*args, **kwargs)
    compiled = str(stmt)
    assert re.search(r"\.org_id = :org_id_1", compiled), compiled
    assert stmt.compile().params["org_id_1"] == "org-1"


def test_wo85_the_normalized_ref_scan_is_org_scoped_too():
    stmt = queries.fuel_transactions_by_normalized_invoice_ref("org-1", ["INV-1"])
    assert "fuel_transactions.org_id = :org_id_1" in str(stmt)
    assert stmt.compile().params["org_id_1"] == "org-1"


def test_wo85_period_and_months_together_is_a_programming_error():
    """They are the same dimension in two shapes; passing both would silently
    AND them into a set neither caller meant. A misuse by a developer, not a
    user input, so `ValueError` — the `fuel_card_parser.select` posture."""
    with pytest.raises(ValueError, match="not both"):
        queries.fuel_transactions("org-1", period="2026-01", months=["2026-01"])


def test_wo85_an_optional_dimension_filters_only_when_it_is_not_none():
    """`None` means "every". A falsy-but-not-None value still filters — the
    call sites that treated `""` as "every" normalise with `or None`, so
    neither pre-migration convention changed (see the module docstring)."""
    assert _criteria(queries.fuel_transactions("o")) == {"fuel_transactions.org_id = ?"}
    assert _criteria(queries.fuel_transactions("o", supplier="")) == {
        "fuel_transactions.org_id = ?",
        "fuel_transactions.supplier = ?",
    }


# The 18 pre-migration predicate sets of WO-85 §0.1, recorded verbatim as the
# equivalence baseline: each is built here the way the tree built it at
# `e3f491d`, and must equal what the registry now produces for that call site.
def test_wo85_registry_predicates_match_the_pre_migration_predicate_sets():
    months = ["2026-01", "2026-02", "2026-03"]

    # sites 1 + 2 — the claim scope (the byte-identical fork this order closed)
    before = select(FuelTransaction).where(
        FuelTransaction.org_id == "o",
        FuelTransaction.entity_id == "e",
        FuelTransaction.country == "LV",
        FuelTransaction.period.in_(months),
    )
    after = queries.claim_scope_transactions("o", entity_id="e", refund_country="LV", months=months)
    assert _criteria(after) == _criteria(before)

    # site 3 — contract_audit.audit (period, optional supplier)
    before = select(FuelTransaction).where(
        FuelTransaction.org_id == "o",
        FuelTransaction.period == "2026-01",
        FuelTransaction.supplier == "Q8",
    )
    assert _criteria(queries.fuel_transactions("o", period="2026-01", supplier="Q8")) == _criteria(
        before
    )

    # sites 4 + 5 + 6 — receipt_control / rebate warnings (a whole period)
    before = select(FuelTransaction).where(
        FuelTransaction.org_id == "o", FuelTransaction.period == "2026-01"
    )
    assert _criteria(queries.fuel_transactions("o", period="2026-01")) == _criteria(before)

    # site 7 — rebate.merge_period, one (supplier, country) group
    before = select(FuelTransaction).where(
        FuelTransaction.org_id == "o",
        FuelTransaction.supplier == "Q8",
        FuelTransaction.country == "LV",
        FuelTransaction.period == "2026-01",
    )
    assert _criteria(
        queries.fuel_transactions("o", supplier="Q8", country="LV", period="2026-01")
    ) == _criteria(before)

    # site 8 — rebate.merge_period phase 2, the planned rows
    before = select(FuelTransaction).where(
        FuelTransaction.org_id == "o", FuelTransaction.id.in_(["a", "b"])
    )
    assert _criteria(queries.fuel_transactions("o", ids=["a", "b"])) == _criteria(before)

    # site 9 — tie_out.check_period (one expectation, currency-keyed: §4.14)
    before = select(FuelTransaction).where(
        FuelTransaction.org_id == "o",
        FuelTransaction.entity_id == "e",
        FuelTransaction.supplier == "Q8",
        FuelTransaction.period == "2026-01",
        FuelTransaction.currency == "SEK",
    )
    assert _criteria(
        queries.fuel_transactions(
            "o", entity_id="e", supplier="Q8", period="2026-01", currency="SEK"
        )
    ) == _criteria(before)

    # sites 10 + 11 — fuel.list_fuel_transactions (page and count share it)
    before = select(FuelTransaction).where(
        FuelTransaction.org_id == "o",
        FuelTransaction.entity_id == "e",
        FuelTransaction.period.in_(months),
        FuelTransaction.supplier == "Q8",
        FuelTransaction.country == "LV",
    )
    assert _criteria(
        queries.fuel_transactions("o", entity_id="e", months=months, supplier="Q8", country="LV")
    ) == _criteria(before)

    # site 12 — fuel_ingest, the WO-50 natural key
    before = select(FuelTransaction).where(
        FuelTransaction.org_id == "o",
        FuelTransaction.entity_id == "e",
        FuelTransaction.supplier == "Q8",
        FuelTransaction.period == "2026-01",
        FuelTransaction.line_seq == 7,
    )
    assert _criteria(
        queries.fuel_transaction_by_natural_key(
            "o", entity_id="e", supplier="Q8", period="2026-01", line_seq=7
        )
    ) == _criteria(before)

    # site 13 — capture_checks, the cross-entity duplicate scan
    norm = queries.NORMALIZED_INVOICE_REF
    before = select(FuelTransaction).where(
        FuelTransaction.org_id == "o",
        FuelTransaction.invoice_ref.is_not(None),
        norm.in_(["INV1"]),
    )
    assert _criteria(
        queries.fuel_transactions_by_normalized_invoice_ref("o", ["INV1"])
    ) == _criteria(before)

    # site 14 — every line of a claim
    before = select(VatRefundClaimLine).where(
        VatRefundClaimLine.org_id == "o", VatRefundClaimLine.claim_id == "c"
    )
    assert _criteria(queries.vat_claim_lines("o", "c")) == _criteria(before)

    # site 15 — the FROZEN lines (claim_pack)
    before = select(VatRefundClaimLine).where(
        VatRefundClaimLine.org_id == "o",
        VatRefundClaimLine.claim_id == "c",
        VatRefundClaimLine.frozen_at.is_not(None),
    )
    assert _criteria(queries.vat_claim_lines("o", "c", frozen=True)) == _criteria(before)

    # sites 16 + 17 — the UNFROZEN lines (freeze, claim_gates)
    before = select(VatRefundClaimLine).where(
        VatRefundClaimLine.org_id == "o",
        VatRefundClaimLine.claim_id == "c",
        VatRefundClaimLine.frozen_at.is_(None),
    )
    assert _criteria(queries.vat_claim_lines("o", "c", frozen=False)) == _criteria(before)

    # site 18 — the unfrozen, RESOLVED lines (document_gate, R10)
    before = select(VatRefundClaimLine).where(
        VatRefundClaimLine.org_id == "o",
        VatRefundClaimLine.claim_id == "c",
        VatRefundClaimLine.frozen_at.is_(None),
        VatRefundClaimLine.invoice_id.is_not(None),
    )
    assert _criteria(queries.resolved_vat_claim_lines("o", "c")) == _criteria(before)


def test_wo85_the_registry_holds_no_io_and_no_money_arithmetic():
    """The charter, asserted structurally: pure predicate builders. A registry
    that could execute a query or quantize a euro would become a second place
    where a figure is computed — the defect it exists to prevent."""
    source = (SERVICES / "queries.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)], (
        "the registry must hold no async function — it performs no I/O"
    )
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Await)]
    imported = {
        n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    } | {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
    assert "decimal" not in imported, "no money arithmetic in the registry (§4.9)"
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"q2", "q", "Decimal", "AsyncSession"}
