# Task/to-do module — deep-research findings, 2026-08-23

> **Question (owner):** research to-do-list ideas and evolve them to help the
> business plan, schedule, send offers, invoice, prepare reports.
>
> **Method & evidence caveat:** 5 search angles → 20 sources identified → every
> load-bearing claim re-verified by independent-source search (2 rounds, 26+6
> agents). This sandbox's network policy blocks full-page fetches, so evidence
> is search-snippet-level; vendor claims are labelled as vendor claims and
> nothing below rests on a claim that failed corroboration.

## Ranked findings

**F1 (high confidence) — Tasks are a VIEW of real work, not a separate list.**
Every market leader (Asana, Monday, ClickUp) renders the SAME items switchable
between list/board/calendar views; field-service suites (Jobber, simPRO,
Tradify) attach tasks to the job/quote/invoice record, never to a standalone
to-do silo. Tradify's "tasks live on the job" is the minimal shipped form.
*Implication: InvoiceIQ tasks must hang off offers, invoices, projects and
assignments — and the calendar (work-calendar.md) is a view of them, not a
second system.*

**F2 (high confidence) — The winning automation shape is a bounded
trigger + day-offset rule, not a workflow engine.** Corroborated shipped
designs: Jobber — quote follow-ups N days after "awaiting response" (max 2
follow-ups, ≤90 days, sent via the same channel as the original) and invoice
follow-ups N days after due date (max 2, stop on payment); Moneybird (EU
accounting) — reminder_delay + auto-send stored ON the invoice workflow, not
as user to-dos; SuperOffice — per-stage follow-up templates with day-offsets
from the previous completed step and an "automatically suggest" flag. Even
Jobber's "custom automation builder" is a constrained when/then, deliberately
not Zapier-class. *Implication: v1 config = a handful of numbers (days,
count) per rule, nothing more.*

**F3 (high confidence, independent evidence) — Speed of response wins work.**
HBR 2011 (2,241 firms): responding to a lead within an hour = ~7× more likely
to qualify it; MIT 2007: 5-minute response ≈ 100× contact rate vs 30 minutes.
Jobber's vendor data (top performers win >60% of quotes, respond <60 min) is
directionally consistent with the independent studies. *Implication: the
follow-up nudge and the "new request needs an answer" task are the
highest-value auto-tasks the module can generate.*

**F4 (high confidence on the problem; vendor-quantified on the fix) —
Overdue-invoice chasing is the money feature.** Chaser 2022 (400+ firms,
vendor survey): 87% of businesses are paid late. GoCardless: 39% of SMEs
spend up to 4 h/week chasing payers (multiple independent surveys agree on
hours-per-week scale). DSO-reduction figures (62% of adopters improved,
15–25% reductions) are consistent across several vendor-sponsored studies but
none is independent. *Implication: automated chasing belongs in the loop —
and InvoiceIQ's DUNNING LADDER ALREADY SHIPS IT. The task module should
surface dunning as visible "chase" items, not rebuild it.*

**F5 (high confidence) — Materialization is a real design choice with two
shipped answers.** simPRO raises a confirm-alert when a recurring job is due
(human clicks Create); Tradify silently auto-creates from template+frequency.
*Implication: for internal obligations (VAT/report prep) confirm-style is
safer; for customer-facing sends the Jobber model (auto, but capped and
self-stopping) is the norm.*

**F6 (high confidence on direction; specific figures mixed) — Feature bloat
is the failure mode.** Pendo 2019 (615 real subscriptions): ~80% of features
rarely/never used — solid. Standish's "64%" traces to a 4-application sample
(disputed generalizability — noted, not relied on). The viral "1 in 3 SMBs
abandon CRM in year one" could NOT be traced to a primary study — treat as
folklore. What IS multi-source: SMEs abandon systems when updating them is
itself work, and task apps die of overdue-badge guilt, unprioritizable lists,
and capture-faster-than-clear accumulation. *Implication: auto-generated
tasks must auto-COMPLETE when the underlying event happens and auto-expire
when stale — never accumulate into a red backlog.*

**F7 (corroborated) — Calendar UX table stakes** (for work-calendar.md):
Jobber ships 5 views (day/week/month/map/list), drag-and-drop reassignment,
color by assignee, push notification to the assignee on add/reschedule/cancel;
simPRO adds a project-filtered scheduling view. Matches phase A/B as designed.

## What v1 SHOULD be (evidence-backed)

One surface — **"Next actions"** — generalizing the dashboard's existing
"what needs me today" into actionable, lifecycle-attached items, each with a
deep link and each self-clearing:

1. **Offer follow-up** (F2/F3): offer in `sent` for N days (default 3, org-
   configurable, max 2 nudges) → task "follow up on offer {number}" with a
   one-click send using a doc template. Auto-completes on accept/reject/
   supersede.
2. **Chase visibility** (F4): overdue issued invoices surface as chase items
   showing what the dunning ladder already did/will do next; manual-step orgs
   get the "send reminder" action inline. No new chasing engine.
3. **Deadline templates** (F5): org-defined recurring obligations ("prepare
   VAT report", "month close") = name + recurrence + lead-time; materialize
   confirm-style N days ahead. Rides the existing scheduler.
4. **Lifecycle nudges**: capture-review backlog, offers expiring, project all-
   assignments-done → "suggest acceptance" (work-calendar phase D hook),
   plan remainder uninvoiced on a closed-work project.
5. **Placement** (F1): items appear on the dashboard list, on the owning
   record, and as an optional layer in the coming calendar — same items,
   three views. Employee sees theirs; managers see the org's.

Hygiene rules baked in (F6): every auto-task has a completing EVENT and an
expiry; nothing requires manual ticking to stay accurate; a task the user
dismisses stays dismissed (audited); no free-text required fields.

## What v1 should NOT include (evidence-backed)

- No general workflow/automation builder (F2 — even Jobber bounds it).
- No standalone freeform to-do lists detached from records (F1, F6).
- No dependencies, Gantt, subtasks, custom fields, priorities matrix (F6 —
  simPRO-class complexity is the documented complaint magnet).
- No silent mass auto-creation of internal tasks (F5) and no unbounded
  reminder sequences (Jobber caps at 2 for a reason).
- No second chasing engine beside dunning (F4).

## Sources (key)

Jobber help/academy/features (automations, follow-ups, schedule views) ·
Moneybird API workflows · SuperOffice sales-guide docs · simPRO help
(recurring jobs, project view, tasks) · Tradify help (job tasks, recurring
jobs, service reminders) · HBR "Short Life of Online Sales Leads" 2011 ·
MIT/InsideSales lead-response 2007 · Chaser 2022 late-payments report ·
GoCardless SME late-payment survey · PYMNTS/AmEx AR-automation study ·
Pendo 2019 Feature Adoption Report · Mountain Goat on the Standish 64% ·
practitioner abandonment analyses (molodtsov.me, Zapier, xda). Vendor-bias
flags retained per finding above.
