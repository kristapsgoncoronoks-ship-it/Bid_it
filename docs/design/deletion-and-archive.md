# Deletion, the recycle bin, and the platform archive — decisions

Owner decisions taken in conversation on 2026-08-14. Recorded here because a
decision that lives only in a chat log is a decision the next engineer will
re-litigate. Nothing below is built yet unless a work order says so.

## The shape

Three separate things, deliberately not one feature:

1. **The client's recycle bin** — a deleted record leaves the workspace, sits in
   a bin for 30 days, and can be restored.
2. **The consent gate** — deleting something past draft warns about possible
   legal consequences and records that the client accepted the warning.
3. **The platform archive** — after the bin expires, the record leaves the
   client's world but is retained by the platform for a defined period.

They are separate because they have different owners, different access rules and
different legal footing. Collapsing them into "delete" is how one of them ends up
wrong.

## Decisions

| Question | Decision | Notes |
|---|---|---|
| Bin retention | **30 days, fixed** | Not per-tenant configurable. Simpler to explain; matches the Windows recycle-bin mental model the owner asked for. |
| Who can restore | **Admin or company owner only** | Restoring puts a record back into the books — the more consequential half of the operation. |
| Can a PAID invoice be deleted? | **Yes — the client's decision** | Overrides the engineer's recommendation to keep a draft-only rule. Taken knowingly: see the consent gate. |
| Deleting past draft | **Warn every time + record the acceptance** | Never a "don't show again" — that is consent given once for decisions not yet made. |
| Platform archive period | **~5 years, to be confirmed** | Baltic accounting law likely REQUIRES multi-year retention of source documents, which would make this a legal obligation (a clean lawful basis) rather than a preference. The exact period must be confirmed with the owner's accountant — it varies by country and document type, and 5 is the owner's estimate, not a verified figure. |
| Archive storage | **A separate sealed store, not a flag on the live table** | A flag means every query, export and support tool can reach archived data, and one forgotten filter surfaces a client's deleted invoice. A separate store makes reaching it an act, not an accident. |
| Who can read the archive | **Off by default; granted by sysadmin/owner to named, strictly limited personnel** | See the note below — this cannot simply be `is_platform_admin`. |
| Forensic logging | **Who, when, what, from which IP** | Standard practice. Note an IP is itself personal data and belongs in the privacy notice. |

## Decisions taken 2026-08-15, after the review cycle

| Question | Decision |
|---|---|
| Purge before the archive exists? | **Leave it on.** Keep the 30-day promise; records are destroyed on day 31. |
| Bin for entities other than invoices? | **Extend it to all of them** — expenses, expense reports, issued-invoice attachments, recurring schedules. One meaning of "Delete" across the product. |
| Invoice locked in a FILED VAT claim? | **Add a real link, then refuse.** See below — the earlier heuristic-based proposal is withdrawn, and this replaces it. |
| Build order | **Archive first**, then the two above. Follows from keeping the purge on. |
| What the archive keeps | **Record AND source document.** The PDF is what proves anything to a tax authority years later, which is the point of retaining at all. |
| Archive read access | **Named individual, time-boxed grant, reason recorded at every access.** Not a role, not a permanent grant, and explicitly not the platform-admin flag. |
| Retention period | **Configurable, default 5 years, marked UNCONFIRMED** until the owner's accountant confirms it per country. Not to appear in any customer-facing claim before then. |

## The VAT-claim question — answered

**Decision: add a real link, then refuse the delete.**

Store the invoice id on the claim line when the claim is built, so "this invoice
is in a filed claim" becomes a fact rather than a guess; then refuse the deletion
and instruct the client to withdraw the claim first, which is a real supported
action rather than a dead end.

This supersedes the withdrawal below — which stands as the reasoning for why the
refusal could NOT be built on the existing data, and therefore why the link has
to come first. The history is kept because the next engineer will otherwise
re-propose the heuristic version.

### Why the original proposal was withdrawn

> **Corrected 2026-08-15, while building the consent gate.** The original
> proposal here — refuse the delete, tell the client to withdraw the claim first
> — rested on a premise that turned out to be wrong, so it is withdrawn rather
> than implemented.
>
> `vat_claimed_invoices` does **not** lock an AP invoice. It locks a
> `(supplier, invoice_ref)` pair against a representative `fuel_transactions`
> row; there is no foreign key to `invoices` at all, and `supplier` is free text
> captured at ingestion rather than a `vendor_id`. Getting from a locked claim
> line to an AP invoice runs through
> `services/transport/invoice_match.py`, which is explicitly a **heuristic**:
> prefix matching, substring matching, an admin override table, and a
> sole-registered fallback.
>
> A hard refusal built on that would be a legal-sounding gate resting on a fuzzy
> string match. When the heuristic fires wrongly the client is blocked from
> deleting their own record and told to withdraw a claim that has nothing to do
> with it; when it misses, the refusal they were promised does not happen. Both
> failures are worse than not having the gate.

So the consent gate ships warning only about facts the schema states with
certainty — the workflow state, a recorded payment, membership of a payment run.
The VAT question is genuinely open and needs a decision on the *data* before a
decision on the *rule*:

1. should an AP invoice carry an explicit, non-heuristic link to a VAT claim
   line (a real FK, set when the claim is built)? Without one, no reliable rule
   is possible — only guesses.
2. given such a link, is deleting a claimed invoice a **refusal** (withdraw the
   claim first) or another **warned consequence**?

Until (1) exists, (2) cannot be implemented honestly, and nothing in the code
pretends otherwise.

## Why archive access cannot just be `is_platform_admin`

`app/core/authz.py` grants a platform admin **every** permission. Hanging archive
access off that flag would bundle "can read any client's deleted documents" with
every other operator power, which is the opposite of "strictly limited".

Archive access must therefore be a **distinct grant** that a platform admin does
NOT hold by default: enabled deliberately, held by named individuals, and
separately auditable. Worth considering when it is built:

- **time-boxed grants** rather than permanent ones (access that expires is much
  safer than access somebody forgets to revoke);
- **a reason recorded at access time**, not just at grant time — the useful
  question is "why did you open this client's document on the 3rd?";
- **every read logged**, not merely every grant;
- **client-visible access history** — a processor that can show a client when its
  archived data was opened is in a materially stronger position than one that
  cannot. Not requested; recommended.

## What makes the archive lawful rather than a liability

Not engineering, but it decides whether the engineering is worth doing. Flagged
for the owner, who should confirm with counsel:

1. **Never call it "permanently deleted" in the UI if the platform retains it.**
   "Removed from your workspace" is accurate and no less clear. Telling a client
   something is gone when it is not is the real exposure — larger than the
   retention itself.
2. **It must appear in the contract/DPA.** Retention a customer agreed to is
   routine; retention they would be surprised by is not.
3. **Right-to-erasure must still function.** `api/routes/privacy.py` already
   implements DSAR erasure with legal-hold handling. Where a legal obligation
   genuinely overrides a request, refusing and saying why is legitimate. What
   must not happen is the archive silently swallowing a request the system would
   otherwise have honoured.

## Build order

1. **Soft delete + the central rule that hides binned records everywhere.**
   19 query sites across 11 modules read the invoice table. This is the risky
   step and ships alone. The precedent is the existing tenant guard: one hook
   that no query can forget, plus a test that fails if one escapes it.
2. **Delete becomes reversible, then the Trash screen** — the routes stop
   destroying rows and start binning them; restore (admin/owner) and the bin
   listing land with them, with a duplicate-number check so restoring cannot
   create two live invoices sharing a number.
3. **The consent gate** — server-enforced acknowledgement (a browser dialog is
   not a consent record; the API must refuse without it), versioned warning text
   so the audit says what was accepted, per deletion.

   > **Swapped, 2026-08-15, during the build.** The original order put the
   > consent gate second. It cannot go there. The gate's whole purpose is to
   > permit deleting something past draft — which today's rule REFUSES outright —
   > so building it against the current rule gates nothing and is dead code,
   > while building it *with* the widening would make a paid invoice destroyable
   > before anything could bring one back. Reversibility first is the only order
   > in which no step temporarily makes an irreversible loss possible.
4. **The 30-day purge** on the existing scheduler, honouring legal holds —
   otherwise records under a preservation duty are destroyed on day 31.
5. **Multi-select on the invoice list** — safe by then, because everything it
   does is undoable.
6. **The platform archive** — separate design, after the above. Written up in
   `platform-archive.md`; nothing built, and it carries four questions for the
   owner (chiefly the retention period, which is a legal question rather than a
   product one).

Steps 1–5 are built. Step 6 is designed, not built.

### The gap that leaves, stated plainly

Until the archive exists, the step-4 purge **destroys** the row. The build order
above puts the archive last, which means the one genuinely irreversible action in
the whole feature currently has no backstop — the opposite of the principle every
other step was sequenced by.

It was shipped that way on purpose, and the reasoning should be checked rather
than assumed:

- Without a purge, the bin never empties. A record would sit invisible and
  immortal while the client has been told it goes after 30 days. That is the
  system stating something untrue about its own data handling, and a
  storage-limitation problem under GDPR Art. 5(1)(e), which is the one this
  codebase's `retention.py` already exists to answer.
- The purge is heavily fenced: it refuses entirely under a legal hold, it takes
  nothing inside its window, and it writes a full snapshot of every destroyed
  record into the audit trail — so even pre-archive, what was destroyed remains
  answerable.

**Owner decision, 2026-08-15: the purge STAYS ON.** Put to the owner with the
one-line off switch as the recommended option; they chose to keep the 30-day
promise the UI makes, knowing records are destroyed on day 31 with only the audit
snapshot behind them. Taken deliberately, and it is why the archive was
re-prioritised ahead of the remaining deletion work (below) — with the purge
running, the archive is the backstop rather than the finishing touch.

## Related work already in the codebase

- `services/retention.py` — per-category retention policies, legal holds, purge
  with preview, and a scheduler. The bin's purge should reuse this, not fork it.
- `api/routes/privacy.py` — GDPR erasure endpoints (ADR-0020).
- `services/bulk.py` — the four bulk guards. Note that soft delete makes the
  client-facing delete REVERSIBLE, so guard 4 (filter-selection refused for
  irreversible actions) stops applying to it and starts applying to the permanent
  purge instead.
