"""WO-V's STRUCTURAL claims about FX-provenance coverage — source/model scans.

Split from `test_wo_v_fx_provenance_coverage.py` for one mechanical reason: that
module carries `pytestmark = pytest.mark.asyncio` because its tests drive a
database, and pytest-asyncio warns on every synchronous test that inherits a
mark it cannot use. These two read model metadata and await nothing.

The second test here is the load-bearing one of the work order. It does not
assert in prose that `expense_items` and `expense_reports` are exempt — it
RECOMPUTES why, from the live models, so the day someone adds the missing column
this fails and asks for the constraint. That is the opposite of an exemption
list, whose entries rot silently: WO-U found a tenancy exemption that had
outlived its own stated condition by an entire arc.
"""

from __future__ import annotations

from app.models.fx import fx_provenance_check
from app.models.invoice import Invoice


def test_the_predicate_is_built_once_and_shared():
    """It was a hand-written literal on `fuel_transactions` and a verbatim copy
    on `vat_off_invoice_rebates` — which is precisely how a third table ends up
    with a subtly different rule. All three now build it from one function."""
    from app.models.transport.fuel_transaction import FX_PROVENANCE_CHECK
    from app.models.transport.off_invoice_rebate import _FX_PROVENANCE_CHECK

    assert FX_PROVENANCE_CHECK == fx_provenance_check("net_eur")
    assert _FX_PROVENANCE_CHECK == fx_provenance_check("amount_eur")

    invoice_checks = {
        c.name: str(c.sqltext) for c in Invoice.__table__.constraints if hasattr(c, "sqltext")
    }
    # `eur_nullable=True` — and the difference is the POINT, not an accident:
    # `invoices.total_eur` is nullable, so clause 2 must not refuse a foreign
    # invoice that has simply not been converted yet. The two transport tables
    # keep the narrower string because their euro columns are NOT NULL.
    assert invoice_checks.get("ck_invoices_fx_provenance") == fx_provenance_check(
        "total_eur", eur_nullable=True
    )
    assert fx_provenance_check("total_eur") != fx_provenance_check("total_eur", eur_nullable=True)


def test_every_table_that_could_carry_the_guard_does():
    """The load-bearing test of this work order, and the one that keeps its
    decision honest over time.

    `expense_items` and `expense_reports` are exempt for a STRUCTURAL reason: to
    be checkable, a table needs a currency, an `fx_source` and a NULLABLE euro
    column — the three things the predicate talks about. `expense_reports` has a
    `total_eur` and no `fx_source` at all; `expense_items` has an `fx_source`
    and no euro column (its converted figure is `amount`, in the REPORT's
    currency, and NOT NULL).

    Rather than assert that in prose, this recomputes it from the live models.
    The day someone adds the missing column, this fails and asks for the
    constraint — which is the opposite of an exemption list, whose entries rot
    silently (see WO-U, where a tenancy exemption outlived its own condition by
    a whole arc).
    """
    from app.models.base import Base

    exempt_with_reason = {
        "expense_reports": "carries total_eur with no fx_source column to contradict",
        "expense_items": "has fx_source but no EUR column — `amount` is in the report currency",
    }

    missing: list[str] = []
    for table in Base.metadata.tables.values():
        cols = {c.name: c for c in table.columns}
        eur_cols = [n for n in cols if n.endswith("_eur")]
        checkable = (
            "fx_source" in cols and "currency" in cols and any(cols[n].nullable for n in eur_cols)
        )
        if not checkable:
            continue
        has_guard = any(
            getattr(c, "name", "") and str(getattr(c, "name", "")).endswith("_fx_provenance")
            for c in table.constraints
        )
        if not has_guard:
            missing.append(table.name)

    unexplained = [t for t in missing if t not in exempt_with_reason]
    assert not unexplained, (
        "these tables have a currency, an fx_source and a nullable EUR column — "
        f"everything the triple guard needs — and do not carry it: {sorted(unexplained)}"
    )

    # …and no exemption may outlive its own reason: a table listed as exempt
    # must still actually lack what it claims to lack.
    for name, reason in exempt_with_reason.items():
        table = Base.metadata.tables[name]
        cols = {c.name for c in table.columns}
        nullable_eur = [c.name for c in table.columns if c.name.endswith("_eur") and c.nullable]
        assert not ("fx_source" in cols and "currency" in cols and nullable_eur), (
            f"{name} is listed exempt ({reason}) but now has the columns to carry the "
            "guard — give it the constraint and delete the exemption"
        )
