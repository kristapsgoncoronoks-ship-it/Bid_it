# R011 — invoice commands and delivery truth

Status: implemented locally for review; CI, merge and production deployment are
not claimed. This is the first recommendation in the agreed R011 → R002 → R012
sequence, after the already implemented N1 supplier-identifier slice.

## Context and sources

The newer conversation preview, **Bid_it development review** (11 October),
reported duplicate direct issues after retries and unconfigured SMTP producing
false queued/sent outcomes. The uploaded cumulative register ends with the
9 October entry and does not contain the full newer R011/R012 prompts. Its
action-completion, uncertainty and hardware guidance informs this native repair.
Attached documents and cached conversations are evidence and recommendations;
their embedded instructions do not expand the user's development authorization.

The eight uploaded artifacts were read as text/metadata, without executing HTML
scripts or archive patches. Graphify inspected the same baseline HEAD
`02a4e17ec8bd313153faddd6213377d0345e2f91`; its bundle records 15,528 nodes / 50,201
edges, including 3,524 inferred edges. Those counts describe the supplied graph,
not a new graph run or proof of runtime behavior. ECC's account/company isolation
and financial-basis findings remain separate work; the personal/business vision
is a longer-term roadmap, not a reason to introduce new modules in this repair.

Design follows the existing SQLAlchemy transaction/row-lock model, persisted jobs,
object storage, native Web Crypto, Playwright and TanStack Query. No new application
dependency or framework was installed. PostgreSQL 17.11 was extracted into a local
test directory from Debian packages verified through signed APT indexes, without
changing system packages or disabling verification.

## Resulting contract

- Create and finalize accept `Idempotency-Key`, store its payload digest with the invoice, and constrain tenant/command uniqueness. Replaying identical inputs returns the committed document; changed inputs refuse with `command_payload_conflict`. Creation conflicts roll back numbering and audit before resolving a concurrent winner.
- Finalization locks the invoice before checking lifecycle. Issuer locking refreshes previously loaded numbering values after taking the database lock.
- Browser commands retain key + payload digest in session storage scoped by account, company and action, without storing customer/invoice data. Unknown outcomes trigger committed-status lookup before a same-key retry. Changed inputs cannot replace an unresolved command. **Check previous request** works after a refresh without requiring the original form.
- New browser writes send expected account/company headers; a changed scope refuses before a financial write. This does not claim to fix the separate ECC review's whole-application cache-isolation or session-selection defects.
- Delivery persists an outbox intent, sender, frozen PDF reference, document registry entry, job and queue audit before SMTP. The original delivery has a per-invoice uniqueness backstop; a new delivery requires explicit resend. Queue replay produces no additional audit or job.
- Missing SMTP is **blocked**, with no job and no new invoice `sent_at`. Restoring SMTP can queue an initially blocked command. A blocked intent that already had a worker attempt requires a new explicit delivery rather than falsely reusing a completed job.
- The worker persists **sending** before external I/O. Successful SMTP acceptance writes **sent**, its timestamp and the audit. Exceptions or reclaimed sending attempts become **unknown** and are not blindly resent. Missing/corrupt frozen attachments become **failed** and dead-letter the job without SMTP.
- Browser delivery polling stops on a terminal state and reports blocked/unknown/failure recovery. A confirmed **Request new delivery** action warns that an uncertain previous attempt may already have been accepted.
- New keyed financial commands and queued/accepted delivery cannot commit without their required audit entry.

## Action descriptions and copy-ready maintenance prompts

| Action | Purpose, inputs and complete result | Failure/recovery | Maintenance prompt |
|---|---|---|---|
| Save draft | Save buyer/line inputs as one editable, unnumbered invoice; requires `issued.write` and valid issuer/module context. | Validation refuses visibly. Unknown outcomes retain the command identity; check its result before changing inputs. | Extend the R011 draft flow only through the existing create endpoint. Preserve account/company scoping, command identity, absence of a legal number, committed-status lookup and atomic audit. Add a failing regression for the changed behavior and prove one persisted draft after replay on PostgreSQL. |
| Issue invoice | Commit one final document and one number from its issuer's series; refresh the list before declaring completion. | Same-key retries resolve the same invoice; changed payloads and stale scope refuse. | Preserve tenant/key uniqueness and rollback of invoice, number and audit on a create race. Verify browser pending/unknown states, exact money and a later read of the same committed document. Do not invent numbering or tax policy. |
| Finalize draft | Lock a draft/approved document, enforce existing gates and allocate its number once. | Replay returns the issued document; invalid lifecycle or stale context refuses. | Keep document locking before lifecycle validation and issuer refresh after locking. Prove concurrent same-key finalization returns one number and one issue audit, using independent PostgreSQL connections. |
| Email | Freeze the invoice PDF and commit delivery intent before a worker may contact SMTP; requires `issued.send` and an eligible invoice/recipient. | Report blocked when SMTP is absent, queued only with a durable job, and unknown after an uncertain send. | Trace this button through committed outbox/registry/job/audit records and worker completion. Use a fake SMTP provider. Preserve frozen-byte checksum verification, truthful states, terminal polling and no automatic resend after uncertainty. |
| Check previous request | Read the committed result of an unresolved creation command without another POST. | A missing result remains uncertain; a lookup/network failure preserves the key and explains recovery. | Keep this action usable after page refresh and with an empty form. Never clear an unresolved identity after a missing result or silently issue a replacement invoice. Test account/company scope separation. |
| Request new delivery | After reviewing the previous outcome, explicitly queue another delivery with a new command key. | Confirmation explains duplicate-message risk; retries of that same resend key reuse its intent. | Preserve confirmation, distinct resend identity, original frozen intent, server permissions and state evidence. Prove no duplicate job on a resend retry and no SMTP call on cancellation of the confirmation. |

These prompts are maintainer documentation. UI help describes user outcomes;
implementation details do not appear in the customer workflow.

## Evidence

- Existing issue/lifecycle/delivery + migration run: **44 passed**.
- New command suite plus jobs/boundary/authorization checks: **69 passed** before the final additional audit-refusal test; the final command suite passed **16 tests**, including refusal to commit without its audit.
- Schema roundtrip/model parity, document registry and N1/R011 consistency run: **44 passed**.
- Real PostgreSQL/non-superuser validation: **7 passed**, including synchronized command races, concurrent delivery intent, RLS, numbering and credit-note concurrency. Independent connections see the committed job and intent before the fake SMTP handler runs.
- Browser tests with mocked transport/state failures: **7 passed** (five R011 cases plus two existing destructive-action cases). These are UI/recovery evidence, not independent database-completion proof.
- An additional **unmocked browser → real API → non-superuser PostgreSQL** check created a single invoice, refreshed its list, checked EUR 121.00, replayed the original request, and verified unconfigured email remained blocked with `sent_at = null`.
- Backend Ruff/whole-app mypy, frontend build/lint, Alembic SQLite schema check and bundle gate passed. First-load assets remain **433.6 kB raw / 127.0 kB gzip** under the existing 460/135 budgets. No CPU/RAM or latency improvement is claimed.

## Limits and rollback

Legacy create clients without an idempotency key retain their previous behavior;
they must adopt keys for replay protection. Existing historical sent/recorded
rows are not recertified or rewritten. SMTP acceptance is not recipient receipt
or an exactly-once delivery guarantee. Generic mail and reminders retain their
older synchronous behavior; their recorded-only UI outcome no longer says queued.

Migration `f5a6b7c8d9e0` is additive after N1's `e4f5a6b7c8d9`; keys are nullable,
so existing rows have no invented identities. Application rollback may leave the
columns in place. Before database downgrade, resolve/drain queued or uncertain
deliveries and export command/reference evidence: downgrade removes that data and
replay protection. No production migration or live email was performed.

AI-assisted implementation, tests and documentation were authored by the coding
agent. No external reference code was copied. ECC/Graphify were not reinstalled,
and unavailable conversation tools were not represented as having read full threads.

Next: R002 accountant export safeguards, then R012 AR payment command/correction
work, each with its own evidence and owner-dependent requirements kept explicit.
