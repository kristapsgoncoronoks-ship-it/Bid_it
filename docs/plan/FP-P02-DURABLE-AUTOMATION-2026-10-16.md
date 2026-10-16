# FP-P02 / FS16-05 — Durable automation child outcomes

AI-assisted implementation and verification; pending human review and exact-head CI.
Source: [uploaded review reconciliation](../reviews/RECONCILIATION-2026-10-16.md).

## Outcome and transaction boundary

Automation email no longer calls the generic synchronous mailer during a sweep.
Each occurrence persists a version-pinned run and ordered child outcomes. Internal
CRM notes and registered webhook intents use their existing services in the same
transaction. Text-mail subject/body/recipient/sender are frozen on EmailMessage;
its command key is the run and action index. A native automation.deliver job commits
with that intent. There is no SMTP on the sweep/request path, no BackgroundTasks,
new broker, source-document transmission or provider integration.

Pending, blocked and unknown are distinct run states, added by migration
`9e15abca5891`. No historical run is rewritten. SMTP absence or missing recipient
is blocked, never a successful enrollment. Queued/sending is pending. A completed
run means every child completed, with email completion meaning SMTP relay acceptance,
not recipient delivery. Completion evidence includes message ID/status/acceptance time.
New cooldown history uses completion time, so a delayed queue cannot consume the
cooldown before its message is accepted; historical timing remains unchanged.

The worker commits sending before external I/O. A crash/reclaim or uncertain SMTP
exception becomes unknown and cannot automatically resend. Fresh workspace/rule and
owner-membership checks occur before sending. Owner resolution uses active authoritative
memberships and active identities, not the user's default organization/role projection.
Disabled rules, suspended workspaces and offboarded frozen owner recipients block.
Draft edits/publication never rewrite a queued occurrence's version or envelope.

Recovery targets the same frozen run. Completed note/webhook children stay completed;
blocked or safely unattempted work may resume. Parent locking serializes recovery;
message command identity and native job generations deduplicate repeated requests.
A completed/unchanged retry produces no duplicate retry audit. A previously completed
worker job may have blocked before SMTP; explicit recovery queues a new job generation
for the same message, without creating another email or repeating completed notes.
Missing evidence refuses recovery instead of recreating an intent that may have sent.
Legacy partial failures with successful children are suppressed pending evidence review.
Legacy successful-run enrollment history remains intact; it is not silently certified
as SMTP acceptance or reset to trigger another message.

## Controls and operational recovery

| Control | Purpose / outcome / recovery | Engineering prompt |
|---|---|---|
| Sweep / Recent runs | Commit native action intent, show pending/blocked/unknown/ok accurately and expose child evidence. | Test transaction rollback, queued versus accepted states, version-pinned text and no request-path SMTP. Never equate a recorded message with delivery. |
| Retry incomplete actions | Resume the same occurrence, preserve completed children and refuse uncertain/legacy evidence. | Race/replay recovery and worker claims; keep one note/message per child and one job per generation. Recheck current authority before I/O. |
| Unknown outcome | Show why automatic resend is unavailable; inspect relay evidence. | Never guess whether SMTP accepted after connection loss or crash. Keep operator evidence/resend policy separate from automated retries. |

Unknown SMTP outcomes and historic partial/recorded-as-success cases need relay/evidence
review before a separate resend decision. This implementation deliberately does not
invent retrospective acceptance or an evidence-free resend override. See
[decision 30](../DECISIONS-NEEDED.md). Other legacy mail consumers, including generic
reminder/digest flows, are not certified as durable by this automation repair.

Rollout: migrate the expanded check first, then deploy paired API/UI and worker with
the registered handler. Inspect legacy preflight and outcome evidence. For rollback,
pause automation processing/rules first and retain schema/evidence; the old engine
could otherwise repeat incomplete occurrences. Schema downgrade refuses any pending,
blocked or unknown rows rather than deleting/relabeling their evidence.

## Verification

Five focused child-intent/recovery tests passed before the legacy-partial test was
added: blocked SMTP, committed sending inspected in a separate session, uncertain
send/reclaim without another send, one-note partial retry and atomic enqueue-failure
rollback. Repeated partial recovery retains one message/job and one retry audit.
An additional legacy-partial test proves automatic enrollment/retry is blocked.
The combined automation/native-queue/migration group passed **92 tests**. A separate
contract/documentation/PII/CI-registration group passed **46 tests**, including repairs
for the preceding branch-wide ZIP/OpenAPI/central-cap failures; no full-suite result from the preceding source revision
is presented as coverage for this new slice.

A real migrated non-superuser PostgreSQL test exercises the published customer-rule
sweep, persisted note and mail intent, native job claim, membership suspension before
send, explicit resume, committed sending inspection from another connection and final
acceptance. The transport callback is synthetic; no real SMTP/provider is contacted.
Eleven browser tests passed, including recovery against the same run and the explicit
unknown-outcome explanation. Browser error branches are mocked, not proof of provider
acceptance. Frontend bundle remains within the unchanged 460/135 kB limits.
