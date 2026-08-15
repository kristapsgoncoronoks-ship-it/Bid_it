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

## Open question, put to the owner and not yet answered

**An invoice locked into a FILED VAT claim.** `vat_claimed_invoices` is an
existing lock whose own docstring says it is released ONLY by withdrawing the
claim. That invoice has been submitted to a foreign tax authority, so deleting it
contradicts a document already filed with a third party — a different situation
from a client removing their own record.

Proposed: **refuse** with an instruction to withdraw the claim first (a real,
supported action, so not a dead end), rather than warn-and-confirm. The owner may
choose to make it a warning like the rest; until they say so, refusal is the
assumption.

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
2. **The consent gate** — server-enforced acknowledgement (a browser dialog is
   not a consent record; the API must refuse without it), versioned warning text
   so the audit says what was accepted, per deletion.
3. **The Trash screen** — contents, days remaining, restore (admin/owner), with a
   duplicate-number check so restoring cannot create two live invoices sharing a
   number.
4. **The 30-day purge** on the existing scheduler, honouring legal holds —
   otherwise records under a preservation duty are destroyed on day 31.
5. **Multi-select on the invoice list** — safe by then, because everything it
   does is undoable.
6. **The platform archive** — separate design, after the above.

## Related work already in the codebase

- `services/retention.py` — per-category retention policies, legal holds, purge
  with preview, and a scheduler. The bin's purge should reuse this, not fork it.
- `api/routes/privacy.py` — GDPR erasure endpoints (ADR-0020).
- `services/bulk.py` — the four bulk guards. Note that soft delete makes the
  client-facing delete REVERSIBLE, so guard 4 (filter-selection refused for
  irreversible actions) stops applying to it and starts applying to the permanent
  purge instead.
