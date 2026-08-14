# WO-101 — H-3, automation provenance shown to the operator

**Priority:** BUILD NOW (third harvest work order) · **Source:** `docs/harvest/
CANDIDATES.md` §BUILD NOW, from paperless-ngx scout S4-5.

## The hole

We already refuse to let a derived value overwrite a human (I-18; `capture_memory`
is read-only by construction). What was missing is S4-5's third leg: **the
reason, on the record, in front of the operator.**

Supplier resolution (`vendors.get_or_create_vendor`) is exact-name and silent, so
both of its outcomes are invisible:

- it matched an existing supplier — and the operator cannot see WHY, so they
  cannot tell a correct match from a coincidence;
- it matched nothing and created a new supplier — so a captured name with one
  character wrong quietly forks the master data into two suppliers that will
  never agree on a balance, and nothing on any screen said so.

Trust in automation is bounded by the ability to interrogate one instance of it.

## What was built

`services/vendor_resolution.py` + `GET /vendors/resolve?name=…`, rendered on the
capture-review screen.

**It mirrors the real rule exactly.** The explanation describes exact stripped
name, because that is what `get_or_create_vendor` actually does. An explanation
that describes a different rule from the one that runs is worse than none.

**It abstains.** When nothing matches exactly but something nearly does,
`needs_decision` is true, `vendor_id` is null, and the candidates come back with
a reason each. Never take first-match: silently attaching an invoice to a
supplier because the names nearly agree is precisely the failure this prevents —
and so is silently creating a second one.

**Reasons, not scores.** Three explicit rules — case/spacing, punctuation/accents,
legal-form suffix — instead of an edit-distance number. "87% similar" tells the
operator nothing they can check; "the same name apart from the company-form
suffix" is something they can agree or disagree with, which is the entire point
of showing it. The folded/suffix-stripped forms are used ONLY to raise a "did you
mean", never to make a match, so a missing legal-form entry costs a hint and can
never cause a wrong link.

**The consequence is stated before it happens.** `outcome` says, in words, what
confirming unchanged will do — including "a NEW supplier called X will be created
alongside the existing one". That is the sentence the operator most needs before
the confirm, not after.

**It writes nothing.** No vendor is created, renamed or linked in this module. A
provenance module that could itself mutate the master data would be the same
class of hazard it exists to expose.

**The decision goes on the permanent record.** Creating an invoice from a supplier
NAME writes a `vendor.auto_resolved` audit event carrying the captured name, the
basis, whether it abstained, and the near candidates. Resolution runs **before**
the create — resolving afterwards would describe the row it had just made and
always report a confident exact match: an audit trail that is always reassuring
and never true. Choosing a supplier explicitly writes no event, because there is
no machine decision to explain and inventing one would put a fabricated
automation event in the chain.

## Why the audit chain and not a column on the invoice

The chain is immutable, hash-chained, and already the thing someone reads when
they ask "why did the machine do this?" months later. A sentence stored on the
invoice would go stale the moment a supplier is renamed and would then be a
confident false account of history — worse than no account. It also needs no
migration.

## Verification

- 12 tests in `backend/tests/test_vendor_resolution_provenance.py`.
- **Three seeded violations, all caught**: (1) taking first-match instead of
  abstaining, (2) resolving AFTER the create, (3) loosening the near rules to a
  prefix comparison so genuinely different suppliers collapse together.
- One test drives BOTH the explanation and the real confirm path and compares
  them, so the two cannot drift apart silently.
- 428 passed / 1 skipped across the vendor, invoice, audit, capture and review
  suites — the new audit action breaks no existing expectation.
- Frontend builds and typechecks clean.

## Known limits

- **The candidate scan is capped** at 2000 suppliers and 5 candidates. Stated in
  the code rather than hidden: suggestions are a courtesy and correctness never
  depends on the list being complete, but an org past the cap gets fewer hints.
- **The invoice-review update path is not covered.** `PATCH /invoices/{id}/review`
  also resolves a supplier from a name; it does not yet write the provenance
  event. Recorded rather than silently assumed away — it is the same three lines
  and belongs in a follow-up increment, not smuggled into this one.
- **No merge action.** The screen can now show that two suppliers are probably
  the same; it cannot yet merge them. That is a separate, destructive operation
  and needs its own design (it is close to L-4's territory).
