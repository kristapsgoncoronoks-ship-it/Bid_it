# The platform archive — design, before any of it is built

Step 6 of `deletion-and-archive.md`. Steps 1–5 are built; this one is deliberately
a written design first, because it is the only part of the feature where the
engineering is the easy half.

## What the owner asked for

Recorded verbatim in intent, not paraphrased into something more convenient:

- as server owner, keep good archives and logs of **what** was deleted, **when**,
  **by whom**, and **from what IP address**;
- a client permanently deleting a document does not mean the platform must
  destroy it — keep a back-end archive of client documents for a period, "let's
  say 5 years";
- **reading** the archive must be a feature activated by a sysadmin or the owner,
  and available to strictly limited personnel.

## The one thing that decides whether this is an asset or a liability

An archive of documents that clients believe they deleted is, in data-protection
terms, the highest-risk store in the product. Every design choice below follows
from a single question: *if this store leaked tomorrow, what would the honest
statement to customers be?*

The answer must be "data we told you we retain, for a period you agreed to, read
only by named people, with every access logged." If the answer is "data our
customers thought was gone", the archive is a liability regardless of how well it
is built.

Three things make it the former, and none of them are code:

1. **Never call it "permanently deleted" in the UI.** The consent warning
   (`services/deletion_consent.py`) already says "it leaves your workspace",
   never "it is destroyed" — deliberately, and that wording must not drift.
2. **It must be in the contract/DPA**, with the retention period and the lawful
   basis stated. Retention a customer agreed to is routine; retention they would
   be surprised by is the whole problem.
3. **Right-to-erasure must still function.** `api/routes/privacy.py` implements
   DSAR erasure with legal-hold handling. Where a statutory retention obligation
   genuinely overrides an erasure request, refusing and saying why is legitimate.
   What must never happen is the archive silently swallowing a request the system
   would otherwise have honoured.

## The retention period is a legal question, not a product one

**5 years is the owner's estimate and must not be shipped as a fact.** Baltic
accounting law likely *requires* multi-year retention of source documents, which
would make this a legal obligation with a clean lawful basis — a much stronger
position than "we prefer to keep it". But the exact period varies by country and
document type, and getting it wrong in either direction is a real exposure:

- too short, and the platform destroys records a client was obliged to keep;
- too long, and the platform holds personal data with no basis for it.

**Action before build: confirm the period with the owner's accountant, per
country.** The implementation should therefore treat the period as configuration
with a documented default, not a constant compiled into a purge.

## Why archive access cannot be `is_platform_admin`

`app/core/authz.py` grants a platform admin **every** permission
(`ALL_PERMISSIONS`). Hanging archive access off that flag would bundle "can read
any client's deleted documents" with every other operator power — the exact
opposite of "strictly limited personnel".

Archive access must be a **distinct grant a platform admin does not hold by
default**. Concretely:

- a permission that is NOT in `ALL_PERMISSIONS` — which means the
  `Role.OWNER: ALL_PERMISSIONS` / `ADMINISTRATOR: ALL - BILLING` shortcuts do not
  reach it, and the authz matrix needs an explicit carve-out rather than an
  addition. This is the single most important structural detail on this page,
  and it is also the one a later refactor is most likely to undo by "tidying up"
  the permission set. It needs a test that fails if the grant ever becomes
  implied.
- **granted per named individual** by a sysadmin or the owner, never per role;
- **time-boxed** — access that expires is far safer than access somebody forgets
  to revoke. A grant with an end date is the default; a permanent grant should
  require a deliberate, separately-audited act.

## Logging: the useful question is not "who has access"

Grant-time logging answers "who could have read it". The question that actually
gets asked after an incident is **"why did you open this client's document on the
3rd?"** So:

- **every READ is logged**, not merely every grant: who, when, which document,
  which client, source IP;
- **a reason is recorded at access time**, entered by the person reading. A
  free-text reason nobody can validate is still worth far more than no reason —
  it makes the access deliberate and it is what an auditor reads;
- an **IP address is itself personal data** and belongs in the privacy notice
  alongside everything else.

Recommended, not requested: **client-visible access history**. A processor that
can show a client exactly when its archived data was opened, and by whom, is in a
materially stronger position than one that cannot — commercially as well as
legally.

## Storage: a separate sealed store, not a flag

A `is_archived` column on the live table means every query, every export and
every support tool can reach archived data, and one forgotten filter surfaces a
client's deleted invoice inside the product. The recycle bin's own step-1 guard
shows how easily a "hidden" row becomes visible when a single query escapes the
rule — and that guard exists precisely because 19 query sites could not be
trusted to each remember.

A separate store makes reaching archived data **an act, not an accident**. It
should be write-once from the purge's point of view, with no code path from the
live application into it other than the gated read.

## Where it hooks into what is already built

`invoices.purge_expired_bin` is the single place a binned record is destroyed. It
already assembles a full snapshot of every row it removes (`deletion_snapshot`)
and writes it to the audit trail. That function is the archive's one and only
producer: archiving becomes "write the record to the sealed store, then delete
the row", inside the same transaction, and nothing else in the codebase needs to
know the archive exists.

That is also why steps 1–5 were sequenced the way they were. There is exactly one
destructive path to intercept.

## Open questions for the owner

1. **The retention period**, per country, confirmed with an accountant (above).
2. **Do the document BYTES go to the archive, or only the record?** An invoice's
   source PDF is the thing an accountant would actually need years later; it is
   also the most sensitive thing to retain. These can be answered differently.
3. **Does the archive follow a client who leaves?** Contract termination and
   statutory retention can point in opposite directions, and the answer belongs
   in the DPA before it belongs in code.
4. **Should the purge run at all before this is built?** Today it destroys the
   row — see the gap recorded at the end of `deletion-and-archive.md`.
