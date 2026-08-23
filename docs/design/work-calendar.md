# Work-planning calendar — owner idea, recorded 2026-08-20

> **Status: recorded, not yet scheduled.** Owner's words: "Work planning
> calendar. For tradesman jobs related. This module is good fit for our app.
> Send quote, sign contract, invoice, assign employee calendar with
> notifications/reminders, make pictures, sign acceptance, issue final
> invoice, get paid."
>
> Industry-neutral rule applies: "tradesman" is an EXAMPLE. Schema and copy
> say project, assignment, schedule, photo — a cleaning crew, a survey team,
> or a consultancy books work the same way.

## 1. How the pitch maps onto what is already shipped

| Owner's step | Status | Where |
|---|---|---|
| Send quote | ✅ shipped | project offers (versioned, send/accept/reject) |
| Sign contract | ✅ docs + templates; e-sign is a queued seam | project documents + template generation (PP-5a) |
| Invoice | ✅ shipped | issuing with `project_id`, invoicing plan |
| Assign employee, calendar, notifications | ❌ **the new module** | this design |
| Make pictures | 🟡 storage exists | `project_documents` takes any file; needs a photo-first capture surface |
| Sign acceptance | 🟡 queued | phase-5 remainder (acceptance & handover state; template exists) |
| Final invoice | 🟡 queued | phase-5 remainder (adjustable final invoice) |
| Get paid | ✅ shipped | receipts, allocation, reminders/dunning |

The lifecycle spine (§5a of project-profitability.md) was built so stages
slot in front of and behind the loop — scheduling is exactly such a stage:
it sits between "contract signed" and "costs collected", and it REUSES the
project as its aggregate. No rework of phases 1–5a is needed.

## 2. The new module, phased

**Phase A — assignments (the data + the screen).**
`project_assignments` (tenant table, all three isolation layers + probe in
the same commit): org_id · project_id (composite FK) · assignee user ·
starts_at/ends_at (a day or a time window) · note · status
(`planned|confirmed|done|cancelled`) · created_by. Screens: a workspace
calendar (month/week; filter by person or project) and a "my work" list for
the assigned employee — an employee sees their OWN schedule; planning others
needs a manager permission. Conflict display (same person, overlapping
window) is advisory, never blocking — real life double-books.

**Phase B — notifications & reminders.**
Reuses the durable jobs queue + mailer + webhooks (all shipped): on assign /
change / cancel → notify the assignee; reminder N hours before start
(per-org default, per-assignment override). Email first (zero new
infrastructure); in-app inbox is the notifications target the data-model doc
already reserves. SMS/push would mean an external provider — decision-gated
under the zero-external-calls-by-default policy, same as AI capture.

**Phase C — photos from the job.**
A mobile-friendly capture surface on the project page (camera input, EXIF
timestamp kept, stored via the existing content-addressed document path into
`project_documents`, kind `photo`). Photos become evidence attached to
acceptance (phase-5 remainder) — "signed acceptance + the pictures" is the
dispute-killer for the final invoice.

**Phase D — closing the owner's loop.**
Acceptance & handover state + adjustable final invoice (ALREADY the queued
phase-5 remainder) — the calendar module doesn't change their design, it
feeds them (last assignment done → suggest acceptance).

## 3. Open questions for the owner (decision-gated)

1. Assignment granularity: whole days, time windows, or both (both is the
   proposal — `starts_at`/`ends_at` covers either)?
2. Who may plan: any bookkeeping role, or a dedicated scheduling permission?
   (Proposal: managers plan everyone; employees see their own.)
3. Reminder channel for v1: email only (no new infrastructure), or is SMS
   worth an external-provider decision now?
4. Should "all assignments done" nudge the project toward acceptance
   automatically, or stay a purely manual step?
