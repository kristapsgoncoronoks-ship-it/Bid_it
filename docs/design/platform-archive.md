# The platform archive — design

**Status: owner decisions taken 2026-08-15; this is the next thing to build.**
Four of the questions below are now answered inline. One remains open and is a
legal question, not an engineering one: the retention period, per country, from
the owner's accountant.

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

**DECIDED 2026-08-15: THREE years included; longer retention is a PAID extension.**
Configurable, so changing it is a settings change and never a migration.

This makes retention a commercial dimension, not just a compliance one — the
included tier keeps three years, and a client who needs longer buys it. That is a
normal and defensible SaaS model.

### The risk it creates, and the mitigation that has to ship with it

Baltic accounting law commonly requires source documents to be kept for LONGER
than three years — frequently five to ten, varying by country and document type.
So a client on the included tier who does not buy the extension will have records
destroyed that they were legally obliged to keep.

That obligation is the CLIENT's, not the platform's — we are a processor, and
the contract can say so. But "your software deleted the invoice my tax authority
just asked for" is a support and reputation event regardless of where the duty
formally sits, and it lands on the client at the worst possible moment.

**Mitigation, and it is also the better commercial design: NOTIFY BEFORE
EXPIRY, never after.** Well ahead of the three-year mark the company owner is
told what is about to leave the archive and offered the extension. Silence
turns a liability into a complaint; a notice turns the same moment into an
upsell. Nothing should ever be destroyed from the archive without the owner
having been told first.

Also required, and not negotiable given the above:

- the included period must be stated plainly at onboarding and in the DPA, not
  buried — a client choosing three years should know they are choosing it;
- the extension must be purchasable at any time, including AFTER the notice.
  A client who discovers the problem at month 35 must be able to fix it.

**The exact statutory periods still need the owner's accountant, per country.**
Three years is now the product decision; whether it clears the legal floor for a
given client is a separate question, and the notice above is what makes a
mismatch survivable rather than silent.

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

## WHO the archive is for — decided 2026-08-15, and it changes the design

**The client's own company OWNER can see their organisation's archive.** The
earlier reading of "activated by sysadmin or owner" took "owner" to mean the
platform operator; it means the CLIENT's owner. Both are true: a client owner
sees their own org's archive, and platform staff see any client's only under the
strict grant below.

This is a better product and a better legal position than a platform-only store,
and it should be treated as the headline rather than a detail:

- retention stops being something done to the client and becomes something they
  use. "Your records are kept for N years and you can look at them" is a
  sentence you can put in a DPA, a sales deck and an onboarding screen. "We keep
  copies of things you deleted" is a sentence you have to explain.
- it removes the worst failure mode this document was written to avoid — a
  client discovering a store they did not know existed.
- GDPR erasure gets simpler, not harder: the data stays under the data subject's
  own visibility, so a request is a conversation rather than a surprise.

**Read-only, not restorable — an engineering assumption, flagged for correction.**
The bin restores into live books; the archive only shows. Pulling a three-
year-old invoice back into the ledger would reopen a closed accounting period and
can collide with invoice numbers issued since. Download is fine and is most of
the value; re-entering the books is not.

### Consequences that must ship WITH this

1. **The consent warning changes and `WARNING_VERSION` must be bumped.** It
   currently ends "after that it leaves your workspace", which was written for a
   platform-only store. With a client-visible archive the honest sentence is
   closer to "after that it moves to your archive, where the company owner can
   still view and download it for N years." Consent to the old words is not
   consent to this arrangement — which is exactly what the versioning mechanism
   exists to handle.
2. **The archive needs a client-facing surface**, not just an operator one. That
   is new UI scope this document did not previously carry.
3. **Tenant scoping becomes load-bearing on the archive itself.** A platform-only
   store is read by a handful of named staff; a client-visible one is read by
   every client owner, so the archive's own org filter is now a primary control
   rather than a backstop, and needs the same guard treatment as the live tables.

## Why PLATFORM archive access cannot be `is_platform_admin`

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

**DECIDED 2026-08-15:** the strictest of the three options offered — named
individual, time-boxed grant, and a reason recorded at EVERY read (not merely at
grant time). The owner chose this over a permanent named grant and over an
ordinary assignable role.

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

1. **The statutory minimums per country**, from the accountant — now to validate
   the three-year included tier rather than to set it (above).
2. ~~Do the document BYTES go to the archive?~~ **DECIDED 2026-08-15: record AND
   source document.** The PDF is what proves anything to a tax authority years
   later, which is most of the reason to retain at all. This makes the archive
   the highest-risk store in the product, which is why the access controls above
   are non-negotiable rather than nice-to-have.
3. **Read-only, or restorable into live books?** Assumed read-only above.
4. **Does the archive follow a client who leaves?** Contract termination and
   statutory retention can point in opposite directions, and the answer belongs
   in the DPA before it belongs in code.
4. ~~Should the purge run before this is built?~~ **DECIDED 2026-08-15: yes, it
   stays on.** Which is precisely why this document stopped being step 6 and
   became the next thing built: with the purge running, the archive is the
   backstop, not the finishing touch.
