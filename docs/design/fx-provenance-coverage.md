# FX provenance coverage — which tables carry the triple guard, and why the rest cannot

> **Status:** DECIDED (WO-V, 2026-08-27). Records why two tables that look like
> they should carry `fx_provenance_check` do not, and what would have to change
> for them to.

## The invariant

A stored EUR figure may not contradict its own provenance. Three combinations
are lies a database will happily store unless told otherwise
(`app/models/fx.py::fx_provenance_check`):

| # | The lie | What it looks like |
|---|---|---|
| 1 | The euro denies a rate was **used** | `fx_source='unknown'` beside a euro figure — "we could not convert this, and here is the conversion" |
| 2 | The euro denies a rate was **needed to be recorded** | a non-EUR document with `fx_source` NULL — a number nobody can audit (§4.15) |
| 3 | The euro denies a rate was **needed at all** | a non-EUR document claiming `fx_source='eur'`, the IDENTITY provenance — a fabricated conversion wearing the one label nobody re-checks |

`FX_SOURCE_CHECK` (WO-8) is a different, weaker thing: it says `fx_source` holds
one of four words. It says nothing about whether that word is TRUE of the row.

## Who carries it

| Table | Euro column | Guard | Shipped |
|---|---|---|---|
| `fuel_transactions` | `net_eur` | `ck_fuel_transactions_fx_provenance` | WO-88, third conjunct WO-89 |
| `vat_off_invoice_rebates` | `amount_eur` | `ck_vat_off_invoice_rebates_fx_provenance` | WO-88, third conjunct WO-89 |
| `invoices` | `total_eur` | `ck_invoices_fx_provenance` | **WO-V** |

**Why `invoices` mattered.** It is the AP document the whole product is built
around, and the transport vertical's claim lines resolve THROUGH it
(`claim_lines.build_claim_lines` matches a fuel line's `invoice_ref` to an
invoice). After WO-89 a fuel transaction could not lie about its euro; the
invoice it pointed at still could. WO-89's own notes recorded the gap and did
not close it.

No writer-side gate was added for `invoices`, and that is deliberate. The only
code that sets `total_eur`/`fx_source` is `fx.eur_total`, which returns either
`(None, "unknown")` or a real converted pair and **cannot** produce a
contradiction. A second gate would be dead code — WO-88's own reasoning for the
rebate table. The constraint is the point: storage protects the writers that do
not exist yet.

## Who cannot carry it, and what would have to change

Both cases are in the expenses domain, and neither is an oversight. To be
checkable a table needs the three things the predicate talks about: a
**currency**, an **`fx_source`**, and a **nullable euro column**.

### `expense_reports` — a euro with no provenance to contradict

It has `currency` and `total_eur`. It has **no `fx_source` column at all**.

There is nothing for a constraint to catch, because the report never records HOW
its EUR total was arrived at. That is arguably the larger hole — §4.15's whole
point is that a converted amount is meaningless without its rate — but closing
it is a **schema change plus a backfill decision**, not a constraint:

- add `fx_source` (and probably `fx_rate`) to `expense_reports`;
- decide what the existing rows get. They cannot be `unknown` (that would force
  `total_eur` to NULL and lose a figure people have already approved and paid),
  and they cannot honestly be `ecb` (nobody recorded which rate was used).

That is an owner-facing decision about historical data, so it is **not** made
here. Recorded in `docs/DECISIONS-NEEDED.md`.

### `expense_items` — no euro column at all

It has `currency` (the ORIGINAL receipt currency), `fx_rate` and `fx_source`.
Its converted figure is **`amount`, in the REPORT's currency — not EUR — and it
is NOT NULL.**

So conjunct 1 is unrepresentable: "unknown means the euro is NULL" cannot apply
to a column that can never be NULL, and the report currency is not necessarily
EUR anyway. The invariant this table actually wants is a different sentence:

> An item whose `currency` differs from its report's currency must carry an
> `fx_source`, and that source may not be `eur` unless the two currencies match.

That is a **cross-row** rule (it reads the parent report's currency), so a CHECK
constraint cannot express it without denormalising the report currency onto the
item. It belongs at the writer (`services/expenses.py`, which is already
correct) plus, if it is wanted at the storage layer, a denormalised
`report_currency` column — a real design change with its own work order.

## The rule that keeps this honest

`tests/test_wo_v_fx_coverage_structure.py::test_every_table_that_could_carry_the_guard_does`
does not trust this document. It walks `Base.metadata`, finds every table with a
currency, an `fx_source` and a nullable `*_eur` column, and fails if one lacks
the constraint. The two exemptions above are listed there with their reasons —
**and the test also asserts each exemption still lacks what it claims to lack**,
so the day someone adds `fx_source` to `expense_reports`, the suite fails and
asks for the constraint.

That inversion is the lesson of WO-U, where a tenancy exemption whose stated
condition was "gains a probe in the same commit that gives the rate a route"
outlived that condition by an entire arc, because nothing checked.
