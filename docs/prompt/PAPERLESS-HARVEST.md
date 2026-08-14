# PROMPT — Harvest paperless-ngx, build into InvoiceIQ

Re-runnable multi-agent brief. Paste the whole file as the prompt. It assumes
nothing about prior conversation.

---

## 0. THE LICENSING FIREWALL — READ FIRST, IT OVERRIDES EVERYTHING

**paperless-ngx is GPL-licensed. InvoiceIQ is a commercial product.**

You may take **ideas, architecture, failure-mode knowledge and problem
statements**. You may **not** take code.

Hard rules, no exceptions:

1. **Never copy, paste, adapt, transliterate or "rewrite from memory while
   looking at" any paperless-ngx source.** Not a function, not a regex, not a
   constant table, not a docstring.
2. **Clean-room separation.** The agent that READS paperless-ngx (the Scout)
   never writes InvoiceIQ code. The agent that WRITES InvoiceIQ code never
   opens the paperless-ngx tree. The Scout hands over a **behavioural
   specification in prose** — what problem exists, what the failure modes are,
   what a correct solution must guarantee — with **no code and no
   implementation detail** that only makes sense as a transcription.
3. **Provenance record.** Every harvested idea gets a row in
   `docs/harvest/PROVENANCE.md`: what was observed, where (path only, as a
   citation), what was specified from it, and an explicit statement that the
   implementation was written independently.
4. If a Scout finds itself wanting to quote more than a **single identifier
   name** to make a point, it is too close to the code. Describe the behaviour
   instead.
5. **This is not legal advice.** If a harvested idea turns out to require
   something structurally identical to their implementation to work at all,
   STOP and escalate to the owner for counsel rather than deciding it yourself.

A harvested idea that cannot be described in prose to an engineer who has never
seen paperless-ngx is not an idea. It is a copy. Drop it.

---

## 1. WHAT THIS IS FOR

InvoiceIQ is an invoice / VAT-recovery / AP-AR platform. paperless-ngx is a
mature document-management system with roughly a decade of accumulated
real-world document-handling scar tissue.

We want the **scar tissue**, not the feature list. Specifically: what breaks
when real people push real documents through a system, and what a correct
answer looks like.

**We do not want to become a document-management system.** Every candidate must
earn its place against the InvoiceIQ thesis: *turn messy multi-supplier invoice
and fuel spend into recovered cash and an audit-ready financial record.*

---

## 2. GROUND TRUTH — do not re-derive, do not assume

**Source (read-only): `paperless-ngx/paperless-ngx`, branch `dev`.**
Clone fresh. Record the HEAD sha in every report; a finding without a sha is
unanchored. Verified at the time of writing (re-verify, it moves):

- `src/` apps: `documents`, `paperless`, `paperless_ai`, `paperless_mail`.
- Sizes: `documents/views.py` ~5.4k LOC, `serialisers.py` ~3.5k, `models.py`
  ~2.0k, `signals/handlers.py` ~1.4k, `consumer.py` ~1.1k, `bulk_edit.py` ~1.1k,
  `paperless_mail/mail.py` ~1.1k.
- Search: **Tantivy**, guarded by a single `filelock.FileLock`, randomised
  backoff, and on lock exhaustion a **silently deferred** re-index task. This is
  their scaling ceiling and the single most useful negative lesson in the repo.
- AI: `paperless_ai/`, llama-index + sqlite-vec, **default OFF**, but torch /
  transformers / sentence-transformers are **mandatory** dependencies.
- Weakest test ratio: `paperless_mail` (17 source files, 7 test files).
- Their metadata→filepath templating is sanitised (`pathvalidate` inside a
  Jinja `SandboxedEnvironment`); their document-list query IS
  `select_related`/`prefetch_related`-optimised. Both are places a naive audit
  reports a bug and is wrong. Do not repeat that mistake.

**Target (read-write): InvoiceIQ, branch `claude/bidit-invoice-data-analytics`.**
Non-negotiable invariants — a change that violates one is rejected regardless of
value:

- Money is `Decimal` end to end, `money.q2`, ROUND_HALF_UP, strings on the wire.
  **No float touches an amount.**
- **No cross-currency sums, ever.** Currency is never inferred from silence.
- FX refuses rather than guesses provenance.
- Tenancy is three layers: org-filtered queries + ORM guard + Postgres FORCE
  RLS. Cross-tenant reads are opaque 404, never 403.
- Advisory figures never silently become binding ones; nothing auto-overwrites a
  human-confirmed figure.
- Audit records old→new **in the same transaction** as the mutation.
- Additive by default. Existing filed/frozen records do not change retroactively.

---

## 3. OPERATING MODE

`IMPLEMENT_CHANGES = FALSE` until Phase 3.

Phases 1 and 2 are analysis only. Do not write InvoiceIQ code, do not create
branches, do not modify anything. The output of Phases 1–2 is a decision
document for the owner.

---

## 4. THE AGENTS

### SCOUT AGENTS (read paperless-ngx ONLY — never open InvoiceIQ)

**S1 — Ingestion & failure modes.** Their document lifecycle from arrival to
availability. Chase specifically: what leaves **partial state** (bytes on disk
with no row, row with no bytes, index entry with no document); duplicate
detection; corrupt / password-protected / enormous files; worker death
mid-process; retry idempotency; what an operator can see when it fails.

**S2 — Mail ingestion.** How untrusted mail becomes documents. Rule
construction, duplicate protection, credential handling, misbehaving servers,
and what their thin test coverage there implies about the risky paths.

**S3 — Search & index consistency.** Why the index/DB can disagree, how they
detect and repair it, the reindex path, and what the single-writer file lock
costs. **This one is primarily a cautionary tale — report what NOT to do.**

**S4 — Document handling & UX mechanics.** Bulk operations, saved views /
filtering, custom fields, storage-path templating, thumbnails, and the
metadata-editing loop. What makes a document set navigable at 100k documents.

Each Scout returns: problem statements, guarantees a correct solution must hold,
observed failure modes, and **path-only citations**. No code. No pseudocode that
mirrors their structure.

### ASSESSOR AGENTS (read InvoiceIQ ONLY — never open paperless-ngx)

**A1 — Fit & duplication.** For each Scout specification: do we already have
this? Where? Is ours weaker, equal or stronger? **Kill anything we already
have.** We already have a durable job queue, three-layer tenancy, an audit
trail, camt.053/CSV/PDF statement parsing and a document vault — check before
proposing.

**A2 — Invariant compatibility.** Would this specification, implemented, violate
any §2 invariant? Name the invariant and the conflict. A specification that
cannot be built without breaking one is rejected, not negotiated.

**A3 — Product fit.** Does this move recovered cash, audit-readiness, or
operator time? A feature that only makes us more like a DMS is rejected.
Be adversarial: most candidates should die here.

### BUILD AGENTS (Phase 3 only)

**B1 — Implementer.** One work order at a time. Never opens paperless-ngx.
**B2 — Adversarial reviewer.** Reviews B1's diff. Different agent, always.

---

## 5. PHASE 1 — HARVEST

Scouts run in parallel. Each produces candidates in this exact shape:

```
CANDIDATE <id>
Problem observed:        (what goes wrong in the real world)
Guarantee required:      (what a correct solution must always hold)
Failure modes seen:      (concrete, from their code paths)
Evidence:                (paperless-ngx path only + HEAD sha)
Prose specification:     (implementable by someone who has never seen their repo)
Explicitly NOT taken:    (their implementation approach, named and rejected)
```

If a Scout cannot fill "Prose specification" without describing their code
structure, the candidate is dropped and recorded as dropped-for-licensing.

## 6. PHASE 2 — ASSESSMENT & DECISION GATE

Assessors challenge every candidate. Require disagreement; artificial consensus
is a failure of this phase. Then score:

```
Recovered-cash impact   1–10
Operator-time saved     1–10
Audit/compliance value  1–10
Build cost              1–10
Regression risk         1–10
Priority = (first three) − (last two)
```

Produce `docs/harvest/CANDIDATES.md`:
- **BUILD NOW** — ranked, with a one-paragraph work order each.
- **LATER** — with the trigger that would promote it.
- **REJECTED** — with the reason (already have it / violates invariant / not our
  product / licensing).

**Then STOP.** Present to the owner. Do not start building. The owner picks.

## 7. PHASE 3 — BUILD (only after the owner picks)

One work order at a time, on `claude/bidit-invoice-data-analytics`.

For each:

1. Write the work-order doc and **push it before writing code** (container
   resets have destroyed unpushed work repeatedly — push every increment).
2. **Write the test first and watch it FAIL** against current code. A test that
   passes before the fix proves nothing. This is not optional: on this codebase,
   four money defects passed a 2,445-test suite, and three first-draft tests
   passed against the very code they were written to catch.
3. Implement.
4. Watch the test pass.
5. For any structural guarantee, **seed the violation** — break the guarantee
   deliberately, confirm the test goes red, restore, confirm `git diff` is empty.
6. **Run the thing and look at the output.** Render the PDF and view it. Feed
   the parser a realistic file and read what comes back. Text extraction and
   green suites do not see a wrong figure on a right-shaped page.
7. Lint, format, type-check. Run the affected suites and report **real numbers**.
8. B2 reviews the diff adversarially before the commit is final.
9. Commit with the reasoning, not the changelog. Push.

**Definition of done:** tests pass; a seeded violation fails them; the output was
looked at; no invariant weakened; no unrelated refactor in the diff; docs and
provenance updated; another agent reviewed it; rollback is possible.

---

## 8. STANDING RULES

- **Evidence or silence.** Every claim cites a path and line. Where you cannot
  verify, write `NEEDS VERIFICATION` and say exactly what to check. Never fill a
  gap with a plausible guess.
- **Report disproofs.** If you set out to find a defect and the code is fine,
  say so explicitly. A killed hypothesis is a real result and protects the
  report's credibility.
- **Correct yourself in the open.** If you were wrong earlier in the run, say
  which claim and what the truth is. Do not quietly revise.
- **CI is currently blind** (no Actions runners). Local runs are the only
  verification; say so whenever you report a result.
- **Never invent a commercial fact** — a rate, a price, a threshold. If a number
  is needed and nobody chose it, refuse and ask.
- Ask the owner when two readings would produce materially different work. Do
  not guess and do not skip the question.

---

## 9. FIRST OUTPUT

Before any building, return:

1. The paperless-ngx HEAD sha you analysed.
2. Candidates by Scout, in the Phase-1 shape.
3. The Assessor challenge log — including candidates killed and why.
4. `docs/harvest/CANDIDATES.md` with BUILD NOW / LATER / REJECTED.
5. A single recommended first work order, with the reason it is first.
6. Anything needing an owner decision before Phase 3 can start.
