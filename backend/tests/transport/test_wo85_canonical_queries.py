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
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.money import q2
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.vat_claim import VatRefundClaimLine
from app.services.transport import (
    claim_gates,
    contract_audit,
    document_gate,
    freeze,
    fuel,
    fuel_ingest,
    queries,
    tie_out,
)
from app.services.transport.checklist import submission_checklist
from app.services.transport.claim import get_or_create_claim
from app.services.transport.claim_lines import build_claim_lines
from tests.factories.transport import synthetic_vehicle_ref
from tests.transport.conftest import enable_transport, make_entity, make_org

BACKEND = Path(__file__).resolve().parents[2]
SERVICES = BACKEND / "app" / "services" / "transport"
ROUTES = BACKEND / "app" / "api" / "routes" / "transport"
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
    itself must reach `fuel_transactions`/`vat_claim_lines` through it.

    The transport ROUTE package is scanned too. A route has no business holding
    a query at all (`engineering-rules` §3), but the layering test only forbids
    a service importing the web layer — it says nothing about a controller
    typing a `select()`, and a fork there would evade a services-only scan."""
    violations: list[str] = []
    for root in (SERVICES, ROUTES):
        for path in sorted(root.rglob("*.py")):
            if path.name == "queries.py":
                continue
            violations += _forks(
                path.read_text(encoding="utf-8"), filename=str(path.relative_to(root.parent))
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
    least one module under `app/services/transport/`.

    `CANONICAL_MODELS` is the one deliberate exception, and it is exempt for a
    stated reason rather than by oversight: it is the SCANNER's target list,
    consumed by `_forks` above so that the guard and the registry it guards
    cannot name different tables. Its consumer is this file, asserted directly.
    """
    assert CANONICAL_NAMES == {"FuelTransaction", "VatRefundClaimLine"}

    sources = {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted(SERVICES.rglob("*.py"))
        if p.name != "queries.py"
    }
    orphans = []
    for name in queries.__all__:
        if name == "CANONICAL_MODELS":
            continue
        needle = f"queries.{name}" if name.isupper() else f"queries.{name}("
        if not [f for f, s in sources.items() if needle in s]:
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


# --------------------------------------------------------------------------- #
# Per-consumer equivalence — hand-computed Decimals over a fixture whose
# NEAR-MISS rows a forked predicate would let through
# --------------------------------------------------------------------------- #

PERIOD = "2026-05"
REF_PERIOD = "2026-Q2"
COUNTRY = "LV"


async def _ingest(
    db_session,
    org_id: str,
    entity_id: str,
    *,
    line_seq: int,
    supplier: str = "Q8",
    period: str = PERIOD,
    country: str = COUNTRY,
    qty: Decimal = Decimal("100.000"),
    net_eur: Decimal = Decimal("150.00"),
    net_eur_eff: Decimal | None = None,
    currency: str = "EUR",
):
    """One validated fuel line. VAT is a flat 21% of net so every expectation
    below is an exact cent figure a reviewer can recompute by hand."""
    vat_eur = q2(net_eur * Decimal("0.21"))
    return await fuel_ingest.ingest_transaction(
        db_session,
        org_id,
        entity_id=entity_id,
        supplier=supplier,
        period=period,
        line_seq=line_seq,
        country=country,
        vehicle_ref=synthetic_vehicle_ref(),
        txn_date=date(2026, 5, 12),
        station="Demo Station Riga",
        product="DIESEL",
        qty=qty,
        currency=currency,
        net_local=net_eur,
        vat_local=vat_eur,
        gross_local=net_eur + vat_eur,
        net_eur=net_eur,
        vat_eur=vat_eur,
        net_eur_eff=net_eur_eff if net_eur_eff is not None else net_eur,
    )


async def _org_entity(db_session, *, name: str, seed: int):
    org = await make_org(db_session, name=name)
    await enable_transport(db_session, org.id)
    entity = await make_entity(db_session, org.id, country=COUNTRY, seed=seed)
    await db_session.commit()
    return org.id, entity.id


@pytest.mark.asyncio
async def test_wo85_the_claim_scope_cut_excludes_every_near_miss_row(db_session):
    """Sites 1+2 — the one genuine fork this order closed. The scope is
    `(entity, refund_country, months of the reference period)`; a row that
    misses on ANY of the three must contribute zero euros. Before the registry,
    `build_claim_lines` and `checklist._unresolved_suppliers` each carried this
    predicate separately, so nothing stopped one of them acquiring a fifth."""
    org_id, entity_id = await _org_entity(db_session, name="WO85 Scope Org", seed=8501)
    other_entity = await make_entity(db_session, org_id, country=COUNTRY, seed=8502)
    await db_session.commit()

    # In scope: 150.00 + 250.00 net -> VAT 31.50 + 52.50 = 84.00
    await _ingest(db_session, org_id, entity_id, line_seq=1, net_eur=Decimal("150.00"))
    await _ingest(db_session, org_id, entity_id, line_seq=2, net_eur=Decimal("250.00"))
    # Near misses — each one dimension off, and each from a DIFFERENT supplier
    # so the checklist's own named supplier set proves the cut too, not just
    # the euro total.
    await _ingest(
        db_session,
        org_id,
        other_entity.id,
        line_seq=3,
        supplier="DKV",
        net_eur=Decimal("900.00"),
    )
    await _ingest(
        db_session,
        org_id,
        entity_id,
        line_seq=4,
        supplier="E100",
        country="EE",
        net_eur=Decimal("900.00"),
    )
    await _ingest(
        db_session,
        org_id,
        entity_id,
        line_seq=5,
        supplier="BP",
        period="2026-07",
        net_eur=Decimal("900.00"),
    )
    await db_session.commit()

    claim = await get_or_create_claim(
        db_session, org_id, entity_id=entity_id, refund_country=COUNTRY, ref_period=REF_PERIOD
    )
    await db_session.commit()

    lines = await build_claim_lines(db_session, org_id, claim.id)
    await db_session.commit()

    # One UNMATCHED line (no registered invoice), carrying exactly the in-scope
    # euros and none of the 900.00 near misses.
    assert sum((ln.net_eur for ln in lines), Decimal("0")) == Decimal("400.00")
    assert sum((ln.vat_eur for ln in lines), Decimal("0")) == Decimal("84.00")

    # ...and the checklist, reading the SAME cut, names the same supplier set.
    items = {i.key: i for i in await submission_checklist(db_session, org_id, claim.id)}
    assert items["receipt_control"].ok is False
    # Exactly ONE supplier: DKV (wrong entity), E100 (wrong country) and BP
    # (adjacent period) are all outside the claim's scope. A widened cut would
    # name them here even though their euros never reach a claim line.
    assert items["receipt_control"].reason == "Missing invoices from: Q8"


@pytest.mark.asyncio
async def test_wo85_a_second_tenants_identical_rows_are_never_in_scope(db_session):
    """§4.4 with overlapping data: tenant B holds the same supplier, period,
    country and amount. A dropped `org_id` returns those rows with a 200, so
    the assertion is on the euros, not on a status code."""
    org_a, entity_a = await _org_entity(db_session, name="WO85 Tenant A", seed=8503)
    org_b, entity_b = await _org_entity(db_session, name="WO85 Tenant B", seed=8504)

    await _ingest(db_session, org_a, entity_a, line_seq=1, net_eur=Decimal("150.00"))
    await _ingest(db_session, org_b, entity_b, line_seq=1, net_eur=Decimal("150.00"))
    await db_session.commit()

    claim = await get_or_create_claim(
        db_session, org_a, entity_id=entity_a, refund_country=COUNTRY, ref_period=REF_PERIOD
    )
    await db_session.commit()
    lines = await build_claim_lines(db_session, org_a, claim.id)
    await db_session.commit()
    assert sum((ln.net_eur for ln in lines), Decimal("0")) == Decimal("150.00")

    # And the period-wide cuts see one tenant's rows only.
    rows, total = await fuel.list_fuel_transactions(
        db_session, org_a, entity_id=entity_a, period=PERIOD
    )
    assert total == 1 and len(rows) == 1
    result = await contract_audit.audit(db_session, org_a, period=PERIOD)
    assert result.lines_without_terms == 1


@pytest.mark.asyncio
async def test_wo85_contract_audit_recover_eur_is_unchanged_by_the_migration(db_session):
    """Site 3 — the money-demand path (a WO-83 claim letter quotes this figure).
    200 L at 300.00 net = 1.50 EUR/L doc; `net_eur_eff` 280.00 = 1.40 EUR/L eff,
    so the rebate APPLIED is 0.10 EUR/L against an expected 0.15. Gap 0.05 x
    200 L = **10.00 EUR**, hand-computed from `BA_fleet_fuel.md` §2.5."""
    org_id, entity_id = await _org_entity(db_session, name="WO85 Audit Org", seed=8505)
    await _ingest(
        db_session,
        org_id,
        entity_id,
        line_seq=1,
        qty=Decimal("200.000"),
        net_eur=Decimal("300.00"),
        net_eur_eff=Decimal("280.00"),
    )
    await contract_audit.set_term(
        db_session,
        org_id,
        supplier="Q8",
        country=COUNTRY,
        product_group="Diesel",
        expected_discount_eur_l=Decimal("0.15"),
    )
    await db_session.commit()

    result = await contract_audit.audit(db_session, org_id, period=PERIOD)
    assert result.recover_eur == Decimal("10.00")
    assert [b.flag for b in result.breaches] == ["short discount"]
    assert result.lines_audited == 1

    # The optional-supplier branch: `""` still means "every supplier" at THIS
    # call site, exactly as it did before the registry (the `or None` bridge).
    for supplier, expected in (("", Decimal("10.00")), ("Q8", Decimal("10.00"))):
        got = await contract_audit.audit(db_session, org_id, period=PERIOD, supplier=supplier)
        assert got.recover_eur == expected, supplier
    other = await contract_audit.audit(db_session, org_id, period=PERIOD, supplier="DKV")
    assert other.recover_eur == Decimal("0.00")
    assert other.lines_audited == 0


@pytest.mark.asyncio
async def test_wo85_tie_out_still_ties_out_per_currency(db_session):
    """Site 9 — §4.14. One period, one supplier, two currencies. The registry
    carries `currency` as a first-class dimension precisely so this cut cannot
    be dropped: each expectation sees ONLY its own currency's lines. A raw
    cross-currency sum would read 363.00 against each 1-line expectation."""
    org_id, entity_id = await _org_entity(db_session, name="WO85 TieOut Org", seed=8506)
    await _ingest(db_session, org_id, entity_id, line_seq=1, net_eur=Decimal("100.00"))
    await _ingest(
        db_session, org_id, entity_id, line_seq=2, currency="SEK", net_eur=Decimal("200.00")
    )
    for currency, gross in (("EUR", Decimal("121.00")), ("SEK", Decimal("242.00"))):
        await tie_out.set_expectation(
            db_session,
            org_id,
            entity_id=entity_id,
            supplier="Q8",
            period=PERIOD,
            currency=currency,
            expected_lines=1,
            expected_gross_local=gross,
        )
    await db_session.commit()

    results = {r.currency: r for r in await tie_out.check_period(db_session, org_id, PERIOD)}
    assert set(results) == {"EUR", "SEK"}
    for currency, gross in (("EUR", Decimal("121.00")), ("SEK", Decimal("242.00"))):
        checks = {c.metric: c for c in results[currency].checks}
        assert results[currency].ok, results[currency].failures
        assert checks["lines"].actual == Decimal("1")
        assert checks["gross_local"].actual == gross


@pytest.mark.asyncio
async def test_wo85_the_fuel_page_total_describes_the_same_set_as_the_page(db_session):
    """Sites 10+11 — the reason `fuel.py` carried its own `_filtered` helper
    ("`total` can never describe a different set than `items`"). Both now come
    off ONE registry `Select`, so the invariant is structural."""
    org_id, entity_id = await _org_entity(db_session, name="WO85 Fuel Org", seed=8507)
    for seq in range(1, 6):
        await _ingest(db_session, org_id, entity_id, line_seq=seq)
    await _ingest(db_session, org_id, entity_id, line_seq=6, supplier="DKV")
    await _ingest(db_session, org_id, entity_id, line_seq=7, country="EE")
    await db_session.commit()

    rows, total = await fuel.list_fuel_transactions(
        db_session, org_id, entity_id=entity_id, period=PERIOD, page_size=2
    )
    assert total == 7 and len(rows) == 2

    _rows, total = await fuel.list_fuel_transactions(
        db_session, org_id, entity_id=entity_id, period=PERIOD, supplier="Q8"
    )
    assert total == 6  # five Q8/LV + the one Q8/EE row

    # `country` is still upper-cased AT THIS CALL SITE, not in the registry —
    # a registry that normalised would change what every other consumer matches.
    _rows, total = await fuel.list_fuel_transactions(
        db_session, org_id, entity_id=entity_id, period=PERIOD, country="ee"
    )
    assert total == 1

    # An empty-string filter still means "every", the pre-registry convention.
    _rows, total = await fuel.list_fuel_transactions(
        db_session, org_id, entity_id=entity_id, period=PERIOD, supplier="", country=""
    )
    assert total == 7


@pytest.mark.asyncio
async def test_wo85_the_unfrozen_cut_is_one_set_for_the_freeze_and_both_gates(db_session):
    """Sites 16+17+18 — R3's synthetic-ref gate, R10's document gate and G2.5's
    freeze/preview must scan IDENTICAL rows: a gate that passes on a set the
    freeze does not freeze proved nothing. One claim, one UNMATCHED (synthetic,
    unresolved) line: the preview sums it, the R3 gate names it, and the R10
    document gate sees zero resolved invoices to check."""
    org_id, entity_id = await _org_entity(db_session, name="WO85 Unfrozen Org", seed=8508)
    await _ingest(db_session, org_id, entity_id, line_seq=1, net_eur=Decimal("150.00"))
    await db_session.commit()

    claim = await get_or_create_claim(
        db_session, org_id, entity_id=entity_id, refund_country=COUNTRY, ref_period=REF_PERIOD
    )
    await db_session.commit()
    await build_claim_lines(db_session, org_id, claim.id)
    await db_session.commit()

    vat_eur, _vat_local, currency = await freeze.preview_vat_base(db_session, org_id, claim.id)
    assert vat_eur == Decimal("31.50")  # 150.00 x 21%, hand-computed
    assert currency == "EUR"
    assert await claim_gates.unfrozen_synthetic_refs(db_session, org_id, claim.id) == ["UNMATCHED"]
    assert await document_gate.missing_document_invoice_ids(db_session, org_id, claim.id) == []

    # Freezing moves the whole set out of the unfrozen cut, for all three.
    await freeze.freeze_claim_lines(db_session, org_id, claim)
    await db_session.commit()
    assert await freeze.preview_vat_base(db_session, org_id, claim.id) == (
        Decimal("0.00"),
        Decimal("0.00"),
        None,
    )
    assert await claim_gates.unfrozen_synthetic_refs(db_session, org_id, claim.id) == []
    # ...and the FROZEN cut now holds exactly that one line.
    frozen = list(await db_session.scalars(queries.vat_claim_lines(org_id, claim.id, frozen=True)))
    assert [ln.vat_eur for ln in frozen] == [Decimal("31.50")]
