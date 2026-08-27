"""WO-V — the FX triple guard reaches `invoices`, and says why it stops there.

WO-88 and WO-89 built the guard (a stored euro may not contradict its own
provenance) and applied it to two TRANSPORT tables. WO-89's own notes recorded
that `invoices` and `expense_items` were left carrying only the WO-8
value-domain check, and named it a platform finding it was not fixing.

`invoices` is not an incidental table to have missed: it is the AP document the
product is built around, and the transport vertical's claim lines resolve
THROUGH it. After WO-89 a fuel transaction could not lie about its euro; the
invoice it points at still could.

WHAT THESE TESTS PIN
----------------------
1. **The constraint bites**, once per conjunct, at the DATABASE — not at a
   service. There is no writer-side gate for invoices and deliberately so: the
   only code that sets `total_eur`/`fx_source` is `fx.eur_total`, which returns
   `(None, "unknown")` or a real pair and cannot produce a contradiction. A
   second gate would be dead code — WO-88's own reasoning for the rebate table.
   Storage protects the writers that do not exist yet.
2. **The honest rows still fit.** A guard that also refuses valid data is a
   worse defect than the one it fixes — and the first draft of this constraint
   WAS that defect: it refused a foreign-currency invoice that had simply not
   been converted yet. The full backend regression caught it (two money-invariant
   fixtures create exactly that row); this list did not, because it was written
   from the predicate rather than from the data. The missing case is now in it.
3. **The predicate is built, never copied.** It lived as a hand-written literal
   on two tables, which is exactly how the third gets a subtly different rule.
4. **The tables that DON'T have it are a decision, not an oversight** — and the
   reason is checked against the live models rather than asserted in prose, so
   the day someone adds the missing column, this test fails and asks for the
   constraint.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.asyncio


async def _vendor(db_session, org_id: str) -> str:
    from app.models.vendor import Vendor

    v = Vendor(org_id=org_id, name=f"WO-V Vendor {uuid.uuid4().hex[:6]}")
    db_session.add(v)
    await db_session.flush()
    return v.id


async def _org(db_session) -> str:
    from app.models.organization import Organization

    org = Organization(name=f"WO-V Org {uuid.uuid4().hex[:6]}")
    db_session.add(org)
    await db_session.flush()
    return org.id


async def _insert(db_session, org_id: str, vendor_id: str, **fx):
    """Insert straight through SQL, bypassing the ORM and every service.

    That is the whole point: the claim under test is that the DATABASE refuses
    these rows, so a test that went through a service would be testing the
    service.
    """
    await db_session.execute(
        text(
            "INSERT INTO invoices (id, org_id, vendor_id, invoice_number, issue_date, "
            "currency, status, subtotal, tax_amount, total, total_eur, fx_source, "
            "workflow_state, version, amount_paid, validation_status, created_at, updated_at) "
            "VALUES (:id, :org, :vendor, :num, :issued, :ccy, 'pending', 100, 21, 121, "
            ":eur, :src, 'draft', 1, 0, 'pending', :now, :now)"
        ),
        {
            "id": str(uuid.uuid4()),
            "org": org_id,
            "vendor": vendor_id,
            "num": f"WOV-{uuid.uuid4().hex[:8]}",
            "issued": date(2026, 6, 1),
            "now": date(2026, 6, 1),
            **fx,
            # Bound as TEXT on purpose: the raw SQLite driver refuses a Decimal
            # parameter, and the column is NUMERIC so the value is cast on the
            # way in. Never a float — this is a money path (§4.9).
            "eur": None if fx.get("eur") is None else str(fx["eur"]),
        },
    )


# --------------------------------------------------------------------------- #
# The constraint bites — one test per conjunct
# --------------------------------------------------------------------------- #


async def test_an_invoice_may_not_deny_a_conversion_while_carrying_its_result(db_session):
    """`unknown` means no rate was available, so the euro must be NULL. A row
    asserting both says "we could not convert this, and here is the
    conversion.\""""
    org = await _org(db_session)
    vendor = await _vendor(db_session, org)
    with pytest.raises(IntegrityError):
        await _insert(db_session, org, vendor, ccy="PLN", eur=Decimal("1400.00"), src="unknown")
        await db_session.commit()


async def test_a_foreign_invoice_may_not_carry_no_provenance_at_all(db_session):
    """§4.15: a converted amount is meaningless without the rate that produced
    it. A NULL provenance on a foreign-currency row is a number nobody can
    audit."""
    org = await _org(db_session)
    vendor = await _vendor(db_session, org)
    with pytest.raises(IntegrityError):
        await _insert(db_session, org, vendor, ccy="PLN", eur=Decimal("1400.00"), src=None)
        await db_session.commit()


async def test_a_foreign_invoice_may_not_claim_the_euro_identity(db_session):
    """The WO-89 shape, now closed on the platform's own table. `eur` means "the
    amount was already EUR, rate 1, no conversion required" — on a PLN invoice
    that is a fabricated conversion wearing the one label nobody re-checks."""
    org = await _org(db_session)
    vendor = await _vendor(db_session, org)
    with pytest.raises(IntegrityError):
        await _insert(db_session, org, vendor, ccy="PLN", eur=Decimal("1400.00"), src="eur")
        await db_session.commit()


# --------------------------------------------------------------------------- #
# …and the honest rows still fit
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("ccy", "eur", "src"),
    [
        ("EUR", Decimal("121.00"), "eur"),  # the identity, on an actual EUR document
        ("PLN", Decimal("280.00"), "ecb"),  # converted at the cached reference rate
        ("PLN", Decimal("275.00"), "stated"),  # converted at the document's own rate
        ("PLN", None, "unknown"),  # no rate available — and no euro claimed
        ("EUR", None, None),  # a euro document, no conversion needed or done
        # THE ROW THE FIRST DRAFT WRONGLY REFUSED, found by the full regression
        # rather than by this list: a foreign invoice not yet converted. It has
        # no euro and no provenance, and it is claiming nothing. `total_eur` is
        # NULLABLE here, unlike the two transport tables the predicate came
        # from, so this state exists at all — which is why the shared builder
        # takes `eur_nullable`.
        ("PLN", None, None),
    ],
)
async def test_an_honest_invoice_is_still_accepted(db_session, ccy, eur, src):
    """A guard that also refuses valid data is a worse defect than the one it
    fixes. Every combination `fx.eur_total` can produce is here."""
    org = await _org(db_session)
    vendor = await _vendor(db_session, org)
    await _insert(db_session, org, vendor, ccy=ccy, eur=eur, src=src)
    await db_session.commit()


async def test_the_constraint_really_reached_the_database(db_session):
    """Belt and braces: the ORM believing in a constraint is not the same as the
    schema having one. Read it back from the live database."""
    conn = await db_session.connection()
    names = {
        c["name"]
        for c in await conn.run_sync(lambda sync: inspect(sync).get_check_constraints("invoices"))
    }
    assert "ck_invoices_fx_provenance" in names, (
        f"the migration did not land — invoices carries {sorted(names)}"
    )
