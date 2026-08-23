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

**Phase B2 — Google/Apple/Microsoft calendar sync (owner addition, same day).**
The standards-based way, in two steps that require NO external API, OAuth,
or vendor account — honouring zero-external-calls-by-default:

1. **ICS download**: any assignment (or a person's whole schedule) exports
   as a standard `.ics` file — opens in Google Calendar, Apple Calendar,
   Outlook alike.
2. **ICS feed subscription (the real "sync")**: a per-user secret feed URL
   (`/calendar/feed/{token}.ics`) serving that user's OWN assignments as a
   live iCalendar feed. The employee subscribes once from their phone
   ("Subscribe to calendar" / webcal; in Outlook: "Add calendar → Subscribe
   from web" — Outlook.com, desktop and Microsoft 365 all take ICS URLs),
   and Google/Apple/Microsoft then POLL US — assignments appear and update
   in the personal calendar automatically.
   The token is a revocable capability (regenerate = old URL dead), scoped
   to one user's schedule, event bodies carry project code + time + note,
   never financial figures. Honest caveat: refresh cadence is the calendar
   vendor's choice (Apple configurable to minutes; Google can take hours).

**Deferred, decision-gated: two-way sync** (edit in Google → flows back).
Requires per-user OAuth against Google's API (a verified Google Cloud app,
quotas, webhooks), Microsoft Graph for Outlook/365 (an Entra ID app
registration), and CalDAV-against-iCloud for Apple, plus a "who wins"
conflict policy — an external-provider decision like SMS. Only worth it if
a pilot customer demands editing their schedule from Google; the feed
covers "see my jobs on my phone", which is the actual need stated.

**Phase B3 — client notifications: "we arrive in 48h" (owner addition,
same day).**
A different audience from B: not the employee, the CUSTOMER. When a project
has scheduled work, the customer contact gets a reminder 48h before the
assignment starts ("Scheduled work on {date} at {time} for {project.name} —
reply/call to reschedule"). No-show and locked-door visits are the classic
margin-killer this addresses.

Channel ladder:
1. **Email first** — free, ships with B on the existing mailer/jobs rails,
   no provider decision needed. Requires a contact email on the customer.
2. **SMS** — the channel the owner asked for, and an EXTERNAL PROVIDER
   (Twilio/Vonage/LINK-class). Under the zero-external-calls-by-default
   policy this is an opt-in module behind a provider seam (the
   billing-provider pattern: one interface, sealed credentials via
   keyvault, swappable vendor). Design constraints, all server-enforced:
   - **Transactional only** — service reminders about work the customer
     ordered (contract-performance ground under GDPR), never marketing; an
     opt-out stops future sends and is recorded.
   - **Idempotent** — the queue is at-least-once; a per-assignment
     sent-marker guarantees ONE message per reminder window, rescheduling
     re-arms it.
   - **Quiet hours** — a 48h-before moment that lands at 03:00 sends at
     the next morning window instead.
   - **Metered** — SMS costs real money per message; usage_counters +
     plan gating decide who pays (platform re-bills or org brings its own
     provider account — owner decision).
   - Phone number lives on the customer master (customers/contacts), a
     normal PII field under the existing retention/erasure machinery.

Timing default: 48h before assignment start, per-org configurable
(24h/48h/72h), per-assignment override, audited like every send.

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
   worth an external-provider decision now? (B3 raises it for CUSTOMERS —
   which provider, and who pays per message: platform re-bills, or each
   org connects its own account?)
4. Should "all assignments done" nudge the project toward acceptance
   automatically, or stay a purely manual step?
