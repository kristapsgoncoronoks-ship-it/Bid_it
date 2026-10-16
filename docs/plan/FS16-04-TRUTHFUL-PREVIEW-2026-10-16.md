# R008-A-AU06 / FS16-04 — Explain automation preview eligibility

AI-assisted implementation/test/evidence drafting; pending exact-head CI and human
review. Continues the uploaded [review findings](../reviews/RECONCILIATION-2026-10-16.md).

Preview and execution now share condition, successful-run suppression, cooldown and
per-sweep cap decisions in the same evaluator. Both order candidates by immutable
record ID. Preview reports `would_fire`, `suppressed` with the policy reason, or
`throttled`; suppressed records do not consume the cap. A successful prior run still
counts across published versions. Preview defaults to the saved draft definition;
`basis=published` explicitly selects the immutable live snapshot. The response names
basis, version (null for draft), evaluation time and cap. The UI displays that basis
and explains that proposed actions are not attempted and provider/recipient readiness
can still block real execution. It does not certify queued or delivered email.

Each eligible preview includes rendered action proposals, with delivery explicitly
`not_attempted`. No action handler is invoked, and the dry path does not flush.
It refuses more than 500 matching output records or 2 MiB rather than silently
truncating the explanation. This bounds the returned plan; existing matcher-query
scaling is not established as a hardware benchmark and remains part of E16-05.
Unsaved form edits are not simulated: save the draft, then preview the saved definition.

Four preview regressions passed: prior once-per-record success becomes suppressed;
cooldown and cap predictions match actual selected records; draft versus published
conditions produce different, accurately labelled plans; overflow refuses with no
action writes. Counts of runs, CRM notes, messages, durable jobs and webhook deliveries
remain unchanged by preview. The overlapping complete automation group passed 42
tests. Nine affected browser tests passed; they mock response branches. Static checks
and full branch CI remain separate evidence.

Action: Dry run reads the saved draft and existing success history, then shows
matching/eligible/suppressed/throttled proposals or an explicit validation/size error.
Recovery is to review the named basis, narrow the saved condition and retry the read.

Maintenance prompt: keep preview and execution on the same eligibility decisions;
never skip policy checks for simulation. Name version/time and explain that eligibility
is not delivery. Assert zero notes/messages/jobs/runs/webhooks/financial mutations,
compare cap/cooldown with actual selected IDs, and never truncate a plan silently.
Keep durable email/partial-action retries (FS16-05) as a separate required repair.
