# Admin workflow builder — research & design ruling (2026-08-23)

> Question researched: should InvoiceIQ get an **integrated dynamic workflow
> builder** operated from the platform-admin account ("I architect, plan and
> build workflows for functionalities"), and is there a **ready solution to
> embed**? Method: 5-angle web research (embeddable engines, builder UI
> libraries, Python orchestration, how established SaaS ships admin
> automation, security/tenancy/licensing) + adversarial verification of
> load-bearing claims (2 independent refuter votes each; 3/4 survived, 1
> corrected without changing the conclusion). Research ran WebSearch-only
> (sandbox egress blocked); vendor-only claims are labelled below.
>
> Related earlier ruling: `tasks-module-research.md` rejected a **user-facing**
> workflow builder. This document is about the **admin/platform level** — a
> different question with a different answer: yes, but as a bounded
> trigger-condition-action engine on our own rails, not an embedded platform.

## 1 · The ruling

**Do not embed a ready-made workflow platform. Build a thin, declarative
trigger-condition-action (TCA) rules engine on the rails we already own**
(durable Postgres job queue, hash-chained audit, mailer, signed webhooks,
role matrix, three-layer tenancy), with a form-based admin builder UI first
and React Flow (MIT) held in reserve if a visual canvas is ever justified.

Every credible path to "implement some ready solution" fails on at least one
of three axes — license, footprint on the 4 GB VPS, or multi-tenancy:

| Platform | License | Verdict |
|---|---|---|
| **n8n** | Sustainable Use License (fair-code, **not** open source — OSI-verified, survived adversarial check) | **Blocked.** Embedding/hosting for customers requires the paid n8n Embed agreement; SUL is GPL-incompatible as a code combination; no built-in multi-tenancy (practitioner consensus: instance-per-tenant); ~500 MB main + Redis + workers — 4 GB is the floor for n8n *alone*. |
| **Windmill** | AGPLv3 (combinable with GPLv3 via §13) | Architecturally the best fit (Rust, **Postgres-only queue**, no Redis) but wants ~4 GB for itself; official Docker images bundle proprietary EE code that must not be embedded in a commercial product; free tier is workspace-scoped, not multi-tenant. |
| **Activepieces** | MIT core, but embed SDK / managed multi-tenant projects / RBAC / audit are **paid EE** | The features we'd embed it *for* are the commercial ones. |
| **Node-RED** | Apache-2.0, explicitly embeddable | Embeds only into **Node.js**; single-user runtime, no tenant separation (one bad flow starves the process) — instance-per-tenant on our VPS is a non-starter. |
| **Kestra** | Apache-2.0 core | JVM app; vendor minimum 4 GiB/2 vCPU by itself; tenancy/RBAC/audit are EE-only. |
| **Temporal** | MIT server + SDKs (Java SDK is Apache-2.0 — corrected in verification) | Not embeddable: its own cluster ≈ 1.8–2.3 GB across ~4 containers. Rules it out here, not everywhere. |
| **Trigger.dev** | Apache-2.0 | Self-host v4 stack (webapp, supervisor, registry, object storage, ClickHouse-recommended) sized for 12 GB+ clusters. |
| **Automatisch** | AGPL-3.0 | Light, legal — but ~40 integrations and weak development pace; embedding a stagnating engine is a liability. |
| **Huginn** | MIT | Rails app, personal-automation model, dated; no SaaS tenancy story. |

License law behind the table (FSF/OSI-sourced, high confidence): GPL-3.0-or-later
**can** incorporate MIT and Apache-2.0 code (one-directional); GPLv3 §13
permits combining with AGPLv3 works (network-source obligation then applies —
cheap for us, our source is public); use-restricted licenses (n8n SUL) are
GPL-incompatible for combination, and even side-by-side "mere aggregation"
composition remains subject to the SUL's own commercial-use limits.

## 2 · What the field actually ships (and why it settles our design)

The strongest cross-vendor pattern found, survived adversarial verification:
**every mass-market product shipped flat trigger-(condition)-action rules
first, not a DAG builder** — Monday ("When X, [if Y], then Z"), Asana Rules,
Jira Automation (trigger/conditions/actions verbatim), Zapier (born as one
trigger + one action; even today executes a *rooted tree*, not a DAG, with
deliberate branch caps). No product surveyed shipped a full graph builder as
its first admin automation feature (medium confidence — absence of
counterexample). Salesforce is the cautionary tale on both ends: three
overlapping automation systems had to be forcibly consolidated into Flow
(EOL of Workflow Rules/Process Builder 2025-12-31) — *don't ship parallel
engines* — and Flow itself is the documented example of handing admins
unlimited graph power without guardrails ("mega-flows" nobody can debug).
Atlassian didn't even build theirs (acquired Code Barrel, 2019).

The closest analog to our exact situation is **Odoo's Automation Rules**:
self-hosted, ORM-event-driven, positioned as a platform-admin tool — model +
trigger + domain filter + server actions. That shape, minus Odoo's
"execute Python code" escape hatch (see §4), is what we build.

Engineering retrospectives (incident.io's two-part series is the best) agree
the hard part is **executor semantics, not the builder UI**: once-only
firing, dedup, concurrency control, idempotency. We already own that layer —
it is the durable jobs queue with idempotency keys that WO-B's reminders run
on. That inverts the "workflow builder trap" (documented mostly by vendors
selling alternatives, but corroborated by Salesforce/Atlassian/incident.io):
the trap catches teams building the *engine*; ours exists.

## 3 · The design: `automation rules` (admin-level TCA)

One new concept, three tables, zero new infrastructure:

- **`automation_rules`** — org-scoped (full three-layer tenancy + parity
  probe), `entity_type` (one per rule: invoice, issued invoice, offer,
  project, assignment, expense…), `trigger` (a closed enum of domain events we
  already audit: `issued.overdue`, `offer.sent`, `project.status_change`,
  `assignment.done`, `invoice.captured`…), `condition` (JSON, see §4),
  ordered `actions` (JSON array from a **fixed action catalog**), `status`
  draft|published|disabled, `fire_policy` once-per-record (default) |
  every-time | cooldown-N-hours.
- **`automation_rule_versions`** — immutable snapshot per publish; every run
  records which version it executed (the durable-execution industry pattern:
  in-flight runs stay pinned; also satisfies the SOC-2-grade audit shape we
  already follow elsewhere).
- **`automation_runs`** — per-fire log: rule, version, record, per-step
  status/input/output, terminal state incl. `failed—needs attention`.
  Rendered in an admin "Runs" panel. (Jira's model: caps + throttle +
  visible audit log.)

**Action catalog v1** (each is a service we already have): send templated
email · create a Next action · set a field/status from an allowed list ·
generate a document from a template · fire a signed webhook (already
HMAC-signed, existing machinery) · notify a member. No loops, no
user-visible branching, single trigger per rule, linear action list.

**Guardrails, all from shipped precedent:** fire-once-per-record default with
explicit opt-in re-fire (HubSpot enrollment model); the engine skips events
*caused by its own actions* and caps chained rule firings (HubSpot/Jira loop
prevention — Jira halts self-triggering chains at 10); per-org hourly
execution caps with throttle-then-auto-disable into the runs log; draft →
**publish** with version history and one-click revert (the exact feature n8n
users demanded for years and got in v2.0); **dry-run** — evaluate trigger +
conditions against a real record, log would-be actions, suppress side
effects, reroute any email to the admin (HubSpot/Salesforce test-mode
shape). Platform-admin authoring first; org-admin delegation later is a
toggle, not a redesign, because nothing in the engine trusts the author.

## 4 · Conditions: declarative only — never embedded scripting

Security research was unambiguous: Python cannot be safely sandboxed
in-process (RestrictedPython says so itself; 2026 real-world escape via a
whitelisted numpy module → RCE), and Jinja2 SSTI escalates to RCE even
sandboxed. So: **no "execute code" action, ever**, and template variables in
action payloads use constrained `{{var.path}}` lookup substitution, never
`Template.render` on admin strings.

Condition engine choice: **JSON Logic** (safe-by-construction: pure
spec-defined ops, no I/O, JSON-serializable — the same rule document the
React builder composes is what Postgres stores and FastAPI evaluates), with
**GoRules zen-engine** (MIT, Rust, in-process Python bindings, decision
tables) as the upgrade path if rules outgrow JSON Logic, and CEL noted as
the more expressive alternative (official Python binding only open-sourced
March 2026, still read-only — too young). Pure-Python JSON Logic ports are
thinly maintained (panzi-json-logic the most faithful) — pin and test, or go
zen-engine from the start if JS-side evaluation isn't needed.

Webhook/arbitrary-URL actions are an SSRF primitive even when admin-authored
(compromised account, future delegation): OWASP allowlist stance + the 2026
DNS-rebinding CVE cluster (mlflow, Postiz, Budibase…) mean any
admin-supplied-URL fetch must validate the **resolved, pinned IP** (custom
httpx transport), block private ranges *and* the docker bridge networks —
string checks and pre-resolution checks are demonstrably insufficient. V1
sidesteps most of this by reusing the existing registered-webhook machinery
rather than free-URL actions.

## 5 · UI, and what stays out

V1 builder is a **form** (trigger picker → condition rows → ordered action
cards), not a canvas — that is what Monday/Asana/Jira/Odoo ship, and it's a
Settings-style page on existing components. If a canvas is ever warranted:
**React Flow / @xyflow/react** — MIT (verified against GitHub + npm, Pro is
support/examples, not a license), React 19 compatible since Jan 2025,
actively maintained (12.11.x days before this research), the de-facto
standard with public automation-builder precedent. Lighter sequential
option: sequential-workflow-designer (MIT, React wrapper). Avoid: Rete.js
advanced plugins (CC-BY-NC — GPL-incompatible), beautiful-react-diagrams &
drawflow (dormant), JointJS free core (high effort without paid Plus).

Out of scope, deliberately: user-facing builder (unchanged ruling), DAG/
branching (revisit only on evidence rules can't express a real need — then
rooted-tree à la Zapier Paths, never re-merging graphs), loops, polling
triggers/dedup (our triggers are in-app domain events — a major
simplification Zapier can't have), a second engine for approvals (if
approval chains are ever needed, ERPNext's lesson: that's a separate small
per-entity state machine, not this engine).

## 6 · Effort & queue position

Rules engine + 3 tables + versioned publish + runs log + form builder +
dry-run ≈ **2 sessions** (the executor rails exist); each additional catalog
action is small. Proposed as **WO-J** after the committed queue — WO-E
(arrival notices), WO-H (CRM light), and WO-I (portal) each add events and
actions that make the catalog richer, and nothing upstream depends on this.
When several WOs would each add a bespoke "when X do Y" setting, pulling
WO-J earlier pays for itself — that's the signal to watch for.
