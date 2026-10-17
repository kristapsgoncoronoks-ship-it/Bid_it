# Markdown documentation inventory — 2026-10-13

Structural analysis of all repository Markdown files, including hidden review and PR-template files; excludes dependency, Git, and build directories. Each file was scanned for headings and relative link destinations. A missing link is an observation, not proof that historical documentation should be rewritten. Detailed current-work interpretation is in [the development review](DEVELOPMENT-REVIEW-2026-10-13.md).

Files: **310**. Lines: **90,949**. Relative links with missing destinations: **0**.

| Group | Files |
|---|---:|
| Architecture and decisions | 43 |
| Audit and security | 29 |
| Historical work orders | 119 |
| Plans and operations | 46 |
| Product and design | 19 |
| Reference investigations | 54 |

## Complete file inventory

| File | Role | Lines | Main subject | Missing links |
|---|---|---:|---|---:|
| [.ai-review/00-executive-summary.md](../.ai-review/00-executive-summary.md) | Audit and security | 87 | Executive Summary — engineering review of the harvest change set | 0 |
| [.ai-review/01-architecture.md](../.ai-review/01-architecture.md) | Audit and security | 94 | Architecture review | 0 |
| [.ai-review/02-change-impact.md](../.ai-review/02-change-impact.md) | Audit and security | 92 | Change impact | 0 |
| [.ai-review/03-code-quality.md](../.ai-review/03-code-quality.md) | Audit and security | 269 | Findings | 0 |
| [.ai-review/04-security.md](../.ai-review/04-security.md) | Audit and security | 105 | Security review | 0 |
| [.ai-review/05-testing.md](../.ai-review/05-testing.md) | Audit and security | 105 | Test report | 0 |
| [.ai-review/06-performance-reliability.md](../.ai-review/06-performance-reliability.md) | Audit and security | 91 | Performance and reliability | 0 |
| [.ai-review/07-implementation-log.md](../.ai-review/07-implementation-log.md) | Audit and security | 91 | Implementation log — changes made during this review | 0 |
| [.ai-review/08-final-review.md](../.ai-review/08-final-review.md) | Audit and security | 101 | Final skeptical review | 0 |
| [.github/PULL_REQUEST_TEMPLATE.md](../.github/PULL_REQUEST_TEMPLATE.md) | Plans and operations | 73 | Objective | 0 |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | Plans and operations | 42 | InvoiceIQ — Architecture (pointer) | 0 |
| [CHANGELOG.md](../CHANGELOG.md) | Plans and operations | 51 | Changelog | 0 |
| [README.md](../README.md) | Plans and operations | 200 | InvoiceIQ — the SME financial workspace | 0 |
| [SECURITY.md](../SECURITY.md) | Plans and operations | 21 | Security Policy | 0 |
| [TODO.md](../TODO.md) | Plans and operations | 2692 | TODO — Task Board | 0 |
| [deploy/SCALING.md](../deploy/SCALING.md) | Plans and operations | 93 | Scaling InvoiceIQ — sizing for 100 and 1,000 tenants | 0 |
| [docs/BACKLOG.md](BACKLOG.md) | Plans and operations | 92 | Engineering backlog | 0 |
| [docs/DECISIONS-NEEDED.md](DECISIONS-NEEDED.md) | Plans and operations | 1244 | Decisions needed — where your involvement is required | 0 |
| [docs/DEPLOY-HOSTINGER.md](DEPLOY-HOSTINGER.md) | Plans and operations | 402 | Deploy InvoiceIQ on a Hostinger VPS (KVM) — exact setup | 0 |
| [docs/DEPLOY-RUNBOOK-2026-08-12.md](DEPLOY-RUNBOOK-2026-08-12.md) | Plans and operations | 169 | Deploy runbook — the 2026-08-12 release to the Hostinger VPS | 0 |
| [docs/DEPLOY-RUNBOOK-2026-08-15.md](DEPLOY-RUNBOOK-2026-08-15.md) | Plans and operations | 344 | Deploy runbook — the 2026-08-15 release to the Hostinger VPS | 0 |
| [docs/DEPLOY-TLS.md](DEPLOY-TLS.md) | Plans and operations | 74 | TLS / SSL + Cloudflare | 0 |
| [docs/DEPLOYMENT.md](DEPLOYMENT.md) | Plans and operations | 407 | InvoiceIQ — Production Deployment Guide | 0 |
| [docs/DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) | Plans and operations | 292 | InvoiceIQ design system & application shell | 0 |
| [docs/M0-exit-gate.md](M0-exit-gate.md) | Plans and operations | 162 | M0 exit gate — "Safe to hold a stranger's money data" | 0 |
| [docs/MANUAL.md](MANUAL.md) | Plans and operations | 577 | InvoiceIQ — User Manual | 0 |
| [docs/RELEASE-READINESS.md](RELEASE-READINESS.md) | Plans and operations | 308 | Release readiness — go/no-go gate | 0 |
| [docs/architecture/AI-ENGINEERING-POLICY.md](architecture/AI-ENGINEERING-POLICY.md) | Architecture and decisions | 128 | AI Engineering Policy | 0 |
| [docs/architecture/DEPRECATION-WARNINGS-2026-09-08.md](architecture/DEPRECATION-WARNINGS-2026-09-08.md) | Architecture and decisions | 79 | Deprecation warnings — inventory and ratchet (2026-09-08, reference R4, FLASK-P3-01) | 0 |
| [docs/architecture/adr/0001-modular-monolith.md](architecture/adr/0001-modular-monolith.md) | Architecture and decisions | 24 | ADR-0001 — Modular monolith over microservices | 0 |
| [docs/architecture/adr/0002-backend-stack.md](architecture/adr/0002-backend-stack.md) | Architecture and decisions | 24 | ADR-0002 — FastAPI + async SQLAlchemy 2.0 + Pydantic v2 | 0 |
| [docs/architecture/adr/0003-postgres-primary-datastore.md](architecture/adr/0003-postgres-primary-datastore.md) | Architecture and decisions | 24 | ADR-0003 — PostgreSQL as the single primary datastore | 0 |
| [docs/architecture/adr/0004-tenant-isolation.md](architecture/adr/0004-tenant-isolation.md) | Architecture and decisions | 112 | ADR-0004 — Shared-schema tenant isolation with an ORM guard (+ RLS) | 0 |
| [docs/architecture/adr/0005-authentication-jwt.md](architecture/adr/0005-authentication-jwt.md) | Architecture and decisions | 24 | ADR-0005 — Stateless JWT authentication | 0 |
| [docs/architecture/adr/0006-authorization-model.md](architecture/adr/0006-authorization-model.md) | Architecture and decisions | 29 | ADR-0006 — Role hierarchy + permission matrix + module gating | 0 |
| [docs/architecture/adr/0007-background-jobs.md](architecture/adr/0007-background-jobs.md) | Architecture and decisions | 24 | ADR-0007 — DB-backed durable job queue over Celery/Redis | 0 |
| [docs/architecture/adr/0008-object-storage.md](architecture/adr/0008-object-storage.md) | Architecture and decisions | 57 | ADR-0008 — S3-compatible object storage for documents | 0 |
| [docs/architecture/adr/0009-extraction-pipeline.md](architecture/adr/0009-extraction-pipeline.md) | Architecture and decisions | 27 | ADR-0009 — Deterministic-first extraction, opt-in AI | 0 |
| [docs/architecture/adr/0010-money-tax-fx.md](architecture/adr/0010-money-tax-fx.md) | Architecture and decisions | 31 | ADR-0010 — Decimal money + ECB FX provenance + VAT engine | 0 |
| [docs/architecture/adr/0011-idempotency-concurrency.md](architecture/adr/0011-idempotency-concurrency.md) | Architecture and decisions | 29 | ADR-0011 — Idempotency keys + guarded/optimistic concurrency | 0 |
| [docs/architecture/adr/0012-audit-log.md](architecture/adr/0012-audit-log.md) | Architecture and decisions | 24 | ADR-0012 — Hash-chained append-only audit log | 0 |
| [docs/architecture/adr/0013-billing-metering.md](architecture/adr/0013-billing-metering.md) | Architecture and decisions | 113 | ADR-0013 — Subscription billing (Stripe + EveryPay) + usage metering | 0 |
| [docs/architecture/adr/0014-search.md](architecture/adr/0014-search.md) | Architecture and decisions | 24 | ADR-0014 — Postgres full-text search before a search engine | 0 |
| [docs/architecture/adr/0015-api-strategy.md](architecture/adr/0015-api-strategy.md) | Architecture and decisions | 33 | ADR-0015 — REST + OpenAPI, versioned API strategy | 0 |
| [docs/architecture/adr/0016-config-secrets.md](architecture/adr/0016-config-secrets.md) | Architecture and decisions | 29 | ADR-0016 — Env config + envelope-encrypted secrets (KMS-backed) | 0 |
| [docs/architecture/adr/0017-feature-flags.md](architecture/adr/0017-feature-flags.md) | Architecture and decisions | 27 | ADR-0017 — DB-backed feature flags (modules + settings) | 0 |
| [docs/architecture/adr/0018-notifications.md](architecture/adr/0018-notifications.md) | Architecture and decisions | 28 | ADR-0018 — Notifications via webhooks + email on the durable queue | 0 |
| [docs/architecture/adr/0019-retention-legal-hold.md](architecture/adr/0019-retention-legal-hold.md) | Architecture and decisions | 56 | ADR-0019 — Data retention + legal hold | 0 |
| [docs/architecture/adr/0020-gdpr-erasure.md](architecture/adr/0020-gdpr-erasure.md) | Architecture and decisions | 33 | ADR-0020 — GDPR right-to-erasure (DSAR) that respects statutory retention | 0 |
| [docs/architecture/adr/0021-sso-scim.md](architecture/adr/0021-sso-scim.md) | Architecture and decisions | 38 | ADR-0021 — Enterprise SSO (OIDC → SCIM → SAML) | 0 |
| [docs/architecture/adr/0022-data-residency.md](architecture/adr/0022-data-residency.md) | Architecture and decisions | 27 | ADR-0022 — Data residency / region-pinning | 0 |
| [docs/architecture/adr/0023-platform-evolution-and-transport-seam.md](architecture/adr/0023-platform-evolution-and-transport-seam.md) | Architecture and decisions | 761 | ADR-0023 — Platform evolution: 8 bounded contexts, 2 projection layers, and the transport-vertical seam | 0 |
| [docs/architecture/adr/0024-structural-authorization.md](architecture/adr/0024-structural-authorization.md) | Architecture and decisions | 41 | ADR-0024 — Structural authorization: declared router permissions + both-direction CI coverage | 0 |
| [docs/architecture/adr/0025-vendor-bank-detail-control.md](architecture/adr/0025-vendor-bank-detail-control.md) | Architecture and decisions | 70 | ADR-0025 — Vendor bank-detail control: protected-field changes are a workflow, not a write | 0 |
| [docs/architecture/adr/0026-one-validation-engine-one-fx-convention.md](architecture/adr/0026-one-validation-engine-one-fx-convention.md) | Architecture and decisions | 111 | ADR-0026 — One validation engine, one FX convention, one currency registry, one dimension registry | 0 |
| [docs/architecture/adr/0027-ai-capture-policy.md](architecture/adr/0027-ai-capture-policy.md) | Architecture and decisions | 94 | ADR-0027 — AI capture policy (decided before any model is wired) | 0 |
| [docs/architecture/adr/0028-rls-unscoped-guc-sticky-empty-string.md](architecture/adr/0028-rls-unscoped-guc-sticky-empty-string.md) | Architecture and decisions | 163 | ADR-0028 — RLS "unscoped" GUC: a custom `set_config` never restores SQL NULL (WO-27) | 0 |
| [docs/architecture/adr/0029-reclaimable-vat-figure.md](architecture/adr/0029-reclaimable-vat-figure.md) | Architecture and decisions | 109 | ADR-0029 — `reclaimable_tax` is wired; the "Reclaimable VAT" figure excludes drafts and rejections (C1.8, WO-43) | 0 |
| [docs/architecture/adr/0030-ai-agent-action-safety.md](architecture/adr/0030-ai-agent-action-safety.md) | Architecture and decisions | 68 | ADR-0030 — AI / MCP action safety: the contract required before any write-capable agent | 0 |
| [docs/architecture/adr/README.md](architecture/adr/README.md) | Architecture and decisions | 47 | Architecture Decision Records | 0 |
| [docs/architecture/data-flows.md](architecture/data-flows.md) | Architecture and decisions | 296 | InvoiceIQ — Request & Event Data Flows | 0 |
| [docs/architecture/data-model-erd.md](architecture/data-model-erd.md) | Architecture and decisions | 289 | InvoiceIQ — Generated ER Diagrams (by domain) | 0 |
| [docs/architecture/data-model.md](architecture/data-model.md) | Architecture and decisions | 327 | InvoiceIQ — Logical Data Model | 0 |
| [docs/architecture/deployment.md](architecture/deployment.md) | Architecture and decisions | 145 | InvoiceIQ — Deployment, Operations & Runtime Concerns | 0 |
| [docs/architecture/diagram-matrix.md](architecture/diagram-matrix.md) | Architecture and decisions | 111 | InvoiceIQ — Diagram Matrix (what we diagram, for whom, and how it stays true) | 0 |
| [docs/architecture/domain-modules.md](architecture/domain-modules.md) | Architecture and decisions | 272 | InvoiceIQ — Domain-Module Map & Data Ownership | 0 |
| [docs/architecture/engineering-rules.md](architecture/engineering-rules.md) | Architecture and decisions | 253 | Engineering rules | 0 |
| [docs/architecture/foundation.md](architecture/foundation.md) | Architecture and decisions | 223 | Platform foundation | 0 |
| [docs/architecture/overview.md](architecture/overview.md) | Architecture and decisions | 417 | InvoiceIQ — Architecture Overview | 0 |
| [docs/architecture/security-boundaries.md](architecture/security-boundaries.md) | Architecture and decisions | 150 | InvoiceIQ — Security Boundaries | 0 |
| [docs/audit/2026-08-16-bug-scan.md](audit/2026-08-16-bug-scan.md) | Audit and security | 303 | Bug and edge-case scan — 2026-08-16 | 0 |
| [docs/audit/2026-09-05/DASHBOARD.md](audit/2026-09-05/DASHBOARD.md) | Audit and security | 120 | SYSTEM AUDIT 2026-09-05 — live dashboard | 0 |
| [docs/audit/2026-09-05/FINAL-REPORT.md](audit/2026-09-05/FINAL-REPORT.md) | Audit and security | 235 | SYSTEM AUDIT 2026-09-05 — FINAL REPORT | 0 |
| [docs/audit/2026-09-05/FINDINGS-AND-BACKLOG.md](audit/2026-09-05/FINDINGS-AND-BACKLOG.md) | Audit and security | 322 | System audit 2026-09-05 — findings register, debate and master backlog | 0 |
| [docs/audit/agent-debate.md](audit/agent-debate.md) | Audit and security | 262 | Adversarial Debate — P0/P1 Findings | 0 |
| [docs/audit/commercial-readiness.md](audit/commercial-readiness.md) | Audit and security | 111 | Commercial Readiness Review — Bid_it (InvoiceIQ) | 0 |
| [docs/audit/data-flows.md](audit/data-flows.md) | Audit and security | 191 | Data Flows — Per-Workflow Request/Data-Flow Detail | 0 |
| [docs/audit/final-scorecard.md](audit/final-scorecard.md) | Audit and security | 140 | Final Scorecard — Bid_it (InvoiceIQ) | 0 |
| [docs/audit/functional-audit.md](audit/functional-audit.md) | Audit and security | 120 | Functional Audit — Lead Product Developer | 0 |
| [docs/audit/remediation-roadmap.md](audit/remediation-roadmap.md) | Audit and security | 606 | Remediation Roadmap — Merged, De-duplicated, Prioritized | 0 |
| [docs/audit/repository-inventory.md](audit/repository-inventory.md) | Audit and security | 141 | Repository Inventory — Module/Stack/Workflow Map | 0 |
| [docs/audit/security-findings.md](audit/security-findings.md) | Audit and security | 65 | Security Findings (extract) | 0 |
| [docs/audit/system-architecture.md](audit/system-architecture.md) | Audit and security | 160 | Bid_it (InvoiceIQ) — System Architecture & Security Review | 0 |
| [docs/audit/test-baseline.md](audit/test-baseline.md) | Audit and security | 72 | Senior Test Engineer — Independent Test-Quality Audit | 0 |
| [docs/design/UX-AUDIT.md](design/UX-AUDIT.md) | Product and design | 575 | InvoiceIQ — UI/UX audit and redesign plan (Phase 1) | 0 |
| [docs/design/crm-module-research.md](design/crm-module-research.md) | Product and design | 124 | CRM module ("clients") — deep-research findings, 2026-08-23 | 0 |
| [docs/design/deletion-and-archive.md](design/deletion-and-archive.md) | Product and design | 195 | Deletion, the recycle bin, and the platform archive — decisions | 0 |
| [docs/design/demo-dataset.md](design/demo-dataset.md) | Product and design | 57 | Demo dataset — design brief (research-backed, 2026-08-25) | 0 |
| [docs/design/fx-provenance-coverage.md](design/fx-provenance-coverage.md) | Product and design | 98 | FX provenance coverage — which tables carry the triple guard, and why the rest cannot | 0 |
| [docs/design/invoice-layout.md](design/invoice-layout.md) | Product and design | 178 | The issued-invoice layout — the design, and why it is this one | 0 |
| [docs/design/platform-archive.md](design/platform-archive.md) | Product and design | 232 | The platform archive — design | 0 |
| [docs/design/project-profitability.md](design/project-profitability.md) | Product and design | 266 | Project profitability — design | 0 |
| [docs/design/supplier-cost-analytics.md](design/supplier-cost-analytics.md) | Product and design | 105 | Supplier cost analytics & external price data — owner idea, recorded 2026-08-20 | 0 |
| [docs/design/supplier-reliability-rating.md](design/supplier-reliability-rating.md) | Product and design | 138 | Supplier Reliability Rating — Design (WO-Q, deliverable 1) | 0 |
| [docs/design/tasks-module-research.md](design/tasks-module-research.md) | Product and design | 134 | Task/to-do module — deep-research findings, 2026-08-23 | 0 |
| [docs/design/work-calendar.md](design/work-calendar.md) | Product and design | 172 | Work-planning calendar — owner idea, recorded 2026-08-20 | 0 |
| [docs/design/workflow-builder-research.md](design/workflow-builder-research.md) | Product and design | 173 | Admin workflow builder — research & design ruling (2026-08-23) | 0 |
| [docs/harvest/CANDIDATES.md](harvest/CANDIDATES.md) | Plans and operations | 1169 | Harvest candidates — paperless-ngx → InvoiceIQ | 0 |
| [docs/perf/BASELINE-2026-08-27.md](perf/BASELINE-2026-08-27.md) | Plans and operations | 201 | Read-path performance baseline — 2026-08-27 (WO-R, audit item R15) | 0 |
| [docs/perf/CONCURRENCY-2026-09-06.md](perf/CONCURRENCY-2026-09-06.md) | Plans and operations | 307 | Concurrency measurement — 2026-09-06 (audit 2026-09-05, "concurrency and write-path performance unmeasured") | 0 |
| [docs/perf/DASHBOARD-ROUND-TRIPS-2026-09-09.md](perf/DASHBOARD-ROUND-TRIPS-2026-09-09.md) | Plans and operations | 129 | PERF-018 — the composed dashboard's round trips (2026-09-09) | 0 |
| [docs/perf/GC-PAUSE-2026-09-06.md](perf/GC-PAUSE-2026-09-06.md) | Plans and operations | 170 | PERF-016 — the tail was the garbage collector (2026-09-06) | 0 |
| [docs/perf/README.md](perf/README.md) | Plans and operations | 45 | `docs/perf/` — recorded performance baselines | 0 |
| [docs/perf/SEED-STATISTICS-2026-09-08.md](perf/SEED-STATISTICS-2026-09-08.md) | Plans and operations | 113 | The gate measured a plan production does not run — 2026-09-08 (reference R6, PERF-020) | 0 |
| [docs/perf/TENANT-GUARD-2026-09-08.md](perf/TENANT-GUARD-2026-09-08.md) | Plans and operations | 249 | The tenant guard's per-statement cost — 2026-09-08 (reference R4, PERF-DUCK-002 → PERF-019) | 0 |
| [docs/plan/DEVELOPMENT-PLAN-2026-08.md](plan/DEVELOPMENT-PLAN-2026-08.md) | Plans and operations | 1051 | Development plan — from recorded ideas to work orders (2026-08-23) | 0 |
| [docs/plan/PLAN_A_vs_PLAN_B.md](plan/PLAN_A_vs_PLAN_B.md) | Plans and operations | 51 | Decision sheet — Plan A (evolve Bid_it) vs Plan B (greenfield from zero) | 0 |
| [docs/plan/PROMPTS.md](plan/PROMPTS.md) | Plans and operations | 16 | PROMPT LIBRARY — INDEX (content reorganized 2026-07-25) | 0 |
| [docs/plan/README.md](plan/README.md) | Plans and operations | 53 | Execution hub — how to run Plan A or Plan B | 0 |
| [docs/plan/REFERENCE-INTEGRATION-PLAN.md](plan/REFERENCE-INTEGRATION-PLAN.md) | Plans and operations | 393 | Reference-integration plan — the eight-repository knowledge archive → InvoiceIQ | 0 |
| [docs/plan/plan-a/ARCH_plan.md](plan/plan-a/ARCH_plan.md) | Plans and operations | 1872 | Platform Engineering Plan — evolving Bid_it (InvoiceIQ) into the all-in-one financial workspace | 0 |
| [docs/plan/plan-a/README.md](plan/plan-a/README.md) | Plans and operations | 10 | Plan A — evolve Bid_it (runbook) | 0 |
| [docs/plan/plan-a/wo/00_PART_B_INTRO.md](plan/plan-a/wo/00_PART_B_INTRO.md) | Historical work orders | 22 | PART B — THE FIRST TEN WORK ORDERS | 0 |
| [docs/plan/plan-a/wo/WO-01.md](plan/plan-a/wo/WO-01.md) | Historical work orders | 183 | WO-1 — Structural authorization: router dependency + CI coverage | 0 |
| [docs/plan/plan-a/wo/WO-02.md](plan/plan-a/wo/WO-02.md) | Historical work orders | 175 | WO-2 — Vendor bank-detail control | 0 |
| [docs/plan/plan-a/wo/WO-03.md](plan/plan-a/wo/WO-03.md) | Historical work orders | 89 | WO-3 — Partners router lockdown | 0 |
| [docs/plan/plan-a/wo/WO-04.md](plan/plan-a/wo/WO-04.md) | Historical work orders | 101 | WO-4 — Tenant & session integrity | 0 |
| [docs/plan/plan-a/wo/WO-05.md](plan/plan-a/wo/WO-05.md) | Historical work orders | 85 | WO-5 — Mandatory inbound-email secret | 0 |
| [docs/plan/plan-a/wo/WO-06.md](plan/plan-a/wo/WO-06.md) | Historical work orders | 131 | WO-6 — Fleet Fuel PII quarantine + harvest protocol | 0 |
| [docs/plan/plan-a/wo/WO-07.md](plan/plan-a/wo/WO-07.md) | Historical work orders | 116 | WO-7 — One validation engine | 0 |
| [docs/plan/plan-a/wo/WO-08.md](plan/plan-a/wo/WO-08.md) | Historical work orders | 131 | WO-8 — One FX convention, no silent cross-currency sums, scheduled refresh | 0 |
| [docs/plan/plan-a/wo/WO-09.md](plan/plan-a/wo/WO-09.md) | Historical work orders | 112 | WO-9 — Payment-run controls | 0 |
| [docs/plan/plan-a/wo/WO-10.md](plan/plan-a/wo/WO-10.md) | Historical work orders | 108 | WO-10 — Documentation truth-up, the new ADRs, and the M0 exit gate | 0 |
| [docs/plan/plan-a/wo/WO-100-inbound-channel-health.md](plan/plan-a/wo/WO-100-inbound-channel-health.md) | Historical work orders | 103 | WO-100 — H-2, inbound-channel health | 0 |
| [docs/plan/plan-a/wo/WO-101-automation-provenance.md](plan/plan-a/wo/WO-101-automation-provenance.md) | Historical work orders | 95 | WO-101 — H-3, automation provenance shown to the operator | 0 |
| [docs/plan/plan-a/wo/WO-11-B15.md](plan/plan-a/wo/WO-11-B15.md) | Historical work orders | 210 | WORK ORDER 11 — B1.5 | 0 |
| [docs/plan/plan-a/wo/WO-12-E11.md](plan/plan-a/wo/WO-12-E11.md) | Historical work orders | 126 | WORK ORDER 12 — Capture-review UI (board E1.1) | 0 |
| [docs/plan/plan-a/wo/WO-13-E12.md](plan/plan-a/wo/WO-13-E12.md) | Historical work orders | 137 | WORK ORDER 13 — Line-item provenance (board E1.2) | 0 |
| [docs/plan/plan-a/wo/WO-14-F11.md](plan/plan-a/wo/WO-14-F11.md) | Historical work orders | 163 | WORK ORDER 14 — Master-data & document screens (board F1.1) | 0 |
| [docs/plan/plan-a/wo/WO-14-followup-disposition.md](plan/plan-a/wo/WO-14-followup-disposition.md) | Historical work orders | 73 | WO-14 follow-up stash — disposition record | 0 |
| [docs/plan/plan-a/wo/WO-15-C16.md](plan/plan-a/wo/WO-15-C16.md) | Historical work orders | 138 | WORK ORDER 15 — One dimension registry (board C1.6) | 0 |
| [docs/plan/plan-a/wo/WO-16-I11.md](plan/plan-a/wo/WO-16-I11.md) | Historical work orders | 167 | WORK ORDER 16 — Composed home dashboard (board I1.1) | 0 |
| [docs/plan/plan-a/wo/WO-17-I12.md](plan/plan-a/wo/WO-17-I12.md) | Historical work orders | 162 | WORK ORDER 17 — Grouped navigation IA (board I1.2) | 0 |
| [docs/plan/plan-a/wo/WO-18-I13.md](plan/plan-a/wo/WO-18-I13.md) | Historical work orders | 121 | WORK ORDER 18 — Cash-position honesty: relabel, don't invent an entity (board I1.3) | 0 |
| [docs/plan/plan-a/wo/WO-19-E13.md](plan/plan-a/wo/WO-19-E13.md) | Historical work orders | 150 | WORK ORDER 19 — Hash-based re-upload detection: advisory, overridable (board E1.3) | 0 |
| [docs/plan/plan-a/wo/WO-20-E16.md](plan/plan-a/wo/WO-20-E16.md) | Historical work orders | 124 | WORK ORDER 20 — Email-intake provider adapter: Mailgun inbound-parse (board E1.6) | 0 |
| [docs/plan/plan-a/wo/WO-21-I15.md](plan/plan-a/wo/WO-21-I15.md) | Historical work orders | 158 | WORK ORDER 21 — Report-to-Excel and report-to-PDF writers for Explore (board I1.5) | 0 |
| [docs/plan/plan-a/wo/WO-22-A15.md](plan/plan-a/wo/WO-22-A15.md) | Historical work orders | 407 | WORK ORDER 22 — Make the 8 business roles reachable (board A1.5) | 0 |
| [docs/plan/plan-a/wo/WO-23-C15.md](plan/plan-a/wo/WO-23-C15.md) | Historical work orders | 133 | WORK ORDER 23 — One currency registry (board C1.5) | 0 |
| [docs/plan/plan-a/wo/WO-24-C17.md](plan/plan-a/wo/WO-24-C17.md) | Historical work orders | 149 | WO-24 — Multi-currency reporting: analytics, benchmark, budget (board C1.7) | 0 |
| [docs/plan/plan-a/wo/WO-25-C19.md](plan/plan-a/wo/WO-25-C19.md) | Historical work orders | 261 | Objective and business value | 0 |
| [docs/plan/plan-a/wo/WO-26-R2.md](plan/plan-a/wo/WO-26-R2.md) | Historical work orders | 188 | Objective and business value | 0 |
| [docs/plan/plan-a/wo/WO-27.md](plan/plan-a/wo/WO-27.md) | Historical work orders | 181 | Objective and business value | 0 |
| [docs/plan/plan-a/wo/WO-28-R3.md](plan/plan-a/wo/WO-28-R3.md) | Historical work orders | 224 | Objective and business value | 0 |
| [docs/plan/plan-a/wo/WO-29-R1.md](plan/plan-a/wo/WO-29-R1.md) | Historical work orders | 166 | Objective and business value | 0 |
| [docs/plan/plan-a/wo/WO-30-R4.md](plan/plan-a/wo/WO-30-R4.md) | Historical work orders | 351 | Task selection rationale | 0 |
| [docs/plan/plan-a/wo/WO-31-R5b.md](plan/plan-a/wo/WO-31-R5b.md) | Historical work orders | 204 | Task selection | 0 |
| [docs/plan/plan-a/wo/WO-32-R6.md](plan/plan-a/wo/WO-32-R6.md) | Historical work orders | 191 | Task selection rationale | 0 |
| [docs/plan/plan-a/wo/WO-33-R7.md](plan/plan-a/wo/WO-33-R7.md) | Historical work orders | 162 | Task selection rationale | 0 |
| [docs/plan/plan-a/wo/WO-34-R16.md](plan/plan-a/wo/WO-34-R16.md) | Historical work orders | 144 | Task selection rationale | 0 |
| [docs/plan/plan-a/wo/WO-35-R18.md](plan/plan-a/wo/WO-35-R18.md) | Historical work orders | 169 | Task selection rationale | 0 |
| [docs/plan/plan-a/wo/WO-36-R9.md](plan/plan-a/wo/WO-36-R9.md) | Historical work orders | 187 | Task-selection rationale (read this first) | 0 |
| [docs/plan/plan-a/wo/WO-37-R8.md](plan/plan-a/wo/WO-37-R8.md) | Historical work orders | 199 | Task-selection rationale (read this first) | 0 |
| [docs/plan/plan-a/wo/WO-38-R13.md](plan/plan-a/wo/WO-38-R13.md) | Historical work orders | 168 | Task-selection rationale (read this first) | 0 |
| [docs/plan/plan-a/wo/WO-39-R17.md](plan/plan-a/wo/WO-39-R17.md) | Historical work orders | 162 | Task-selection rationale (read this first) | 0 |
| [docs/plan/plan-a/wo/WO-40-R12.md](plan/plan-a/wo/WO-40-R12.md) | Historical work orders | 225 | WO-40 — R12: root README.md/ARCHITECTURE.md scale numbers have drifted stale again since WO-10 | 0 |
| [docs/plan/plan-a/wo/WO-41-R11.md](plan/plan-a/wo/WO-41-R11.md) | Historical work orders | 163 | WO-41 — R11: stale `# TODO: secret store (ADR-0016)` comment on `SsoConnection.client_secret` | 0 |
| [docs/plan/plan-a/wo/WO-42-R10.md](plan/plan-a/wo/WO-42-R10.md) | Historical work orders | 59 | Objective and business value | 0 |
| [docs/plan/plan-a/wo/WO-43-C18.md](plan/plan-a/wo/WO-43-C18.md) | Historical work orders | 185 | WO-43 — Wire `reclaimable_tax`; fix the "Reclaimable VAT" figure (board C1.8) | 0 |
| [docs/plan/plan-a/wo/WO-44-E14.md](plan/plan-a/wo/WO-44-E14.md) | Historical work orders | 201 | WO-44 — Duplicate detection beyond exact number (E1.4) | 0 |
| [docs/plan/plan-a/wo/WO-45-UX1-async-state-and-page-header.md](plan/plan-a/wo/WO-45-UX1-async-state-and-page-header.md) | Historical work orders | 185 | WORK ORDER 45 — Async-state truth + consistent page headers (board UX1) | 0 |
| [docs/plan/plan-a/wo/WO-46-E15.md](plan/plan-a/wo/WO-46-E15.md) | Historical work orders | 305 | WO-46 — Extraction learning loop (E1.5) | 0 |
| [docs/plan/plan-a/wo/WO-47-H13.md](plan/plan-a/wo/WO-47-H13.md) | Historical work orders | 377 | WO-47 — Quota keyed off the org's plan, not the user's role (H1.3) | 0 |
| [docs/plan/plan-a/wo/WO-48-H16.md](plan/plan-a/wo/WO-48-H16.md) | Historical work orders | 289 | WO-48 — Dogfood fallback: bill our own customers through our own AR module (H1.6) | 0 |
| [docs/plan/plan-a/wo/WO-49-G1.1-G2.1-G2.3.md](plan/plan-a/wo/WO-49-G1.1-G2.1-G2.3.md) | Historical work orders | 331 | WO-49 — M3 opener: transport claim grain + is_synthetic() + module entitlement (G1.1, G2.1, G2.3) | 0 |
| [docs/plan/plan-a/wo/WO-50-G1.2.md](plan/plan-a/wo/WO-50-G1.2.md) | Historical work orders | 378 | WO-50 — `fuel_transactions`: the typed transaction model + idempotent ingestion + product-group derivation (board G1.2) | 0 |
| [docs/plan/plan-a/wo/WO-51-G2.2.md](plan/plan-a/wo/WO-51-G2.2.md) | Historical work orders | 386 | WO-51 — `vat_claimed_invoices`: the one-invoice-one-submission lock table (board G2.2) | 0 |
| [docs/plan/plan-a/wo/WO-52-G2.4.md](plan/plan-a/wo/WO-52-G2.4.md) | Historical work orders | 335 | WO-52 — Claim-line construction + note→invoice resolution (board G2.4) | 0 |
| [docs/plan/plan-a/wo/WO-53-G1.3-G1.4.md](plan/plan-a/wo/WO-53-G1.3-G1.4.md) | Historical work orders | 266 | WO-53 — The monthly close as a durable job + locked-line protection (board G1.3, G1.4) | 0 |
| [docs/plan/plan-a/wo/WO-54-G2.5.md](plan/plan-a/wo/WO-54-G2.5.md) | Historical work orders | 252 | WO-54 — Frozen claim lines + frozen VAT base at submission (board G2.5) | 0 |
| [docs/plan/plan-a/wo/WO-55-G2.8.md](plan/plan-a/wo/WO-55-G2.8.md) | Historical work orders | 186 | WO-55 — Art. 9 goods-code mapping (board G2.8) | 0 |
| [docs/plan/plan-a/wo/WO-56-G2.6-slice1.md](plan/plan-a/wo/WO-56-G2.6-slice1.md) | Historical work orders | 273 | WO-56 — G2.6 slice 1: hard period-end gate + Art. 17 minimum-amount gate + deadline risk scanner (board G2.6, R7/R8/R9) | 0 |
| [docs/plan/plan-a/wo/WO-57-G2.6-slice2.md](plan/plan-a/wo/WO-57-G2.6-slice2.md) | Historical work orders | 204 | WO-57 — G2.6 slice 2: annual mop-up + quarterly duplicate-block (board G2.6, R6) | 0 |
| [docs/plan/plan-a/wo/WO-58-G2.6-slice3.md](plan/plan-a/wo/WO-58-G2.6-slice3.md) | Historical work orders | 319 | WO-58 — G2.6 slice 3: document-presence gate + receipt-control waivers (board G2.6, R10/R15) | 0 |
| [docs/plan/plan-a/wo/WO-59-G2.7.md](plan/plan-a/wo/WO-59-G2.7.md) | Historical work orders | 244 | WO-59 — G2.7: status lifecycle 1A→5, the AUTO-code gate + the soft 2B reminder (board G2.7, R12/R17) | 0 |
| [docs/plan/plan-a/wo/WO-60-G2.10-slice1.md](plan/plan-a/wo/WO-60-G2.10-slice1.md) | Historical work orders | 253 | WO-60 — G2.10 slice 1: the adjustable submission checklist engine + claim-level items (board G2.10, R45) | 0 |
| [docs/plan/plan-a/wo/WO-61-G3.1-slice1.md](plan/plan-a/wo/WO-61-G3.1-slice1.md) | Historical work orders | 221 | WO-61 — G3.1 slice 1: the per-country supplier legal-entity registry (board G3.1, R21/R22) | 0 |
| [docs/plan/plan-a/wo/WO-62-G3.2-slice1.md](plan/plan-a/wo/WO-62-G3.2-slice1.md) | Historical work orders | 381 | WO-62 — G3.2 slice 1: the fuel-card parser registry + the Eurowag parser (board G3.2, R20) | 0 |
| [docs/plan/plan-a/wo/WO-63-G3.2-slice2.md](plan/plan-a/wo/WO-63-G3.2-slice2.md) | Historical work orders | 371 | WO-63 — G3.2 slice 2: the E100 fuel-card parser (board G3.2, R20) | 0 |
| [docs/plan/plan-a/wo/WO-63-stash-disposition.md](plan/plan-a/wo/WO-63-stash-disposition.md) | Historical work orders | 144 | WO-63 stash — disposition record | 0 |
| [docs/plan/plan-a/wo/WO-64-G3.2-slice3.md](plan/plan-a/wo/WO-64-G3.2-slice3.md) | Historical work orders | 372 | WO-64 — G3.2 slice 3: the Q8/Kuwait Petroleum fuel-card parser (board G3.2) | 0 |
| [docs/plan/plan-a/wo/WO-65-G3.2-slice4.md](plan/plan-a/wo/WO-65-G3.2-slice4.md) | Historical work orders | 353 | WO-65 — G3.2 slice 4: the DKV fuel-card parser + the supplier-stated-EUR money model (board G3.2) | 0 |
| [docs/plan/plan-a/wo/WO-66-G3.3.md](plan/plan-a/wo/WO-66-G3.3.md) | Historical work orders | 373 | WO-66 — G3.3: the two independent validation regimes (engine tie-out + capture review gate) (board G3.3, R25) | 0 |
| [docs/plan/plan-a/wo/WO-67-G3.2-slice5.md](plan/plan-a/wo/WO-67-G3.2-slice5.md) | Historical work orders | 368 | WO-67 — G3.2 slice 5: the TFC by Moya parser + the hub-only discount money model (board G3.2) | 0 |
| [docs/plan/plan-a/wo/WO-68-G3.2-slice6.md](plan/plan-a/wo/WO-68-G3.2-slice6.md) | Historical work orders | 456 | WO-68 — G3.2 slice 6: the Moeve parser + the per-line-IVA VAT-inclusive money model (board G3.2) | 0 |
| [docs/plan/plan-a/wo/WO-69-G3.2-slice7.md](plan/plan-a/wo/WO-69-G3.2-slice7.md) | Historical work orders | 514 | WO-69 — G3.2 slice 7: the BP/Aral parser + the PLN/ECB independently-given money model (board G3.2 — the LAST network; this order CLOSES G3.2) | 0 |
| [docs/plan/plan-a/wo/WO-70-G3.3-slice2.md](plan/plan-a/wo/WO-70-G3.3-slice2.md) | Historical work orders | 432 | WO-70 — G3.3 slice 2: the anti-drift extraction baseline + `regression_check` (board G3.3) | 0 |
| [docs/plan/plan-a/wo/WO-71-G3.4.md](plan/plan-a/wo/WO-71-G3.4.md) | Historical work orders | 392 | WO-71 — G3.4: deterministic advisory post-capture checks (board G3.4) | 0 |
| [docs/plan/plan-a/wo/WO-72-G3.5.md](plan/plan-a/wo/WO-72-G3.5.md) | Historical work orders | 446 | WO-72 — G3.5: receipt control (cadence × activity, orphan check, overrides survive) | 0 |
| [docs/plan/plan-a/wo/WO-73-G2.11.md](plan/plan-a/wo/WO-73-G2.11.md) | Historical work orders | 415 | WO-73 — G2.11: customer lifecycle + per-country activation gates (R44) | 0 |
| [docs/plan/plan-a/wo/WO-74-G2.12.md](plan/plan-a/wo/WO-74-G2.12.md) | Historical work orders | 331 | WO-74 — G2.12: evidence pack + claim workbook | 0 |
| [docs/plan/plan-a/wo/WO-75-R3-lock-gate.md](plan/plan-a/wo/WO-75-R3-lock-gate.md) | Historical work orders | 246 | WO-75 — R3: the synthetic-line refusal as a LOCK-GATE consumer in `submit_claim` | 0 |
| [docs/plan/plan-a/wo/WO-76-transport-routes-1.md](plan/plan-a/wo/WO-76-transport-routes-1.md) | Historical work orders | 352 | WO-76 — transport routes slice 1: the claim lifecycle over HTTP | 0 |
| [docs/plan/plan-a/wo/WO-77-transport-routes-2.md](plan/plan-a/wo/WO-77-transport-routes-2.md) | Historical work orders | 424 | WO-77 — transport routes slice 2: admin/config surfaces + filing artifacts | 0 |
| [docs/plan/plan-a/wo/WO-78-transport-ui-1.md](plan/plan-a/wo/WO-78-transport-ui-1.md) | Historical work orders | 318 | WO-78 — transport UI slice 1: the VAT claims workspace | 0 |
| [docs/plan/plan-a/wo/WO-79-fuel-read-surface.md](plan/plan-a/wo/WO-79-fuel-read-surface.md) | Historical work orders | 344 | WO-79 — the fuel-transaction read surface + the submit pick-list it unlocks | 0 |
| [docs/plan/plan-a/wo/WO-80-transport-ui-2.md](plan/plan-a/wo/WO-80-transport-ui-2.md) | Historical work orders | 350 | WO-80 — transport UI slice 2: the admin/config workspace | 0 |
| [docs/plan/plan-a/wo/WO-81-recovery-analytics.md](plan/plan-a/wo/WO-81-recovery-analytics.md) | Historical work orders | 353 | WO-81 — the cash-recovery analytics service + its read route (G4.3 / R38) | 0 |
| [docs/plan/plan-a/wo/WO-82-overcharge-claimback.md](plan/plan-a/wo/WO-82-overcharge-claimback.md) | Historical work orders | 462 | WO-82 — supplier overcharge detection + claim-back (G4.5 / R41) | 0 |
| [docs/plan/plan-a/wo/WO-83-overcharge-artifacts.md](plan/plan-a/wo/WO-83-overcharge-artifacts.md) | Historical work orders | 440 | WO-83 — R41's send-ready artifacts: the evidence packet and the claim letter | 0 |
| [docs/plan/plan-a/wo/WO-84-net-eur-eff-merge.md](plan/plan-a/wo/WO-84-net-eur-eff-merge.md) | Historical work orders | 203 | WORK ORDER 84 — G4.2/R50: the off-invoice rebate merge into `net_eur_eff` + the source guard | 0 |
| [docs/plan/plan-a/wo/WO-85-canonical-query-registry.md](plan/plan-a/wo/WO-85-canonical-query-registry.md) | Historical work orders | 357 | WORK ORDER 85 — G4.1/R51: the canonical query registry for transport | 0 |
| [docs/plan/plan-a/wo/WO-86-transport-ui-3.md](plan/plan-a/wo/WO-86-transport-ui-3.md) | Historical work orders | 404 | WO-86 — transport UI slice 3: the recovery intelligence workspace | 0 |
| [docs/plan/plan-a/wo/WO-87-overpay-benchmark.md](plan/plan-a/wo/WO-87-overpay-benchmark.md) | Historical work orders | 275 | Objective and business value | 0 |
| [docs/plan/plan-a/wo/WO-88-fx-provenance-consistency.md](plan/plan-a/wo/WO-88-fx-provenance-consistency.md) | Historical work orders | 496 | 0. Current-state recon (done BEFORE any design — "who can write a row that lies about its own euro?") | 0 |
| [docs/plan/plan-a/wo/WO-89-fx-wrong-provenance.md](plan/plan-a/wo/WO-89-fx-wrong-provenance.md) | Historical work orders | 533 | 0. Current-state recon (done BEFORE any design — "who can write a row that lies about *needing* a rate?") | 0 |
| [docs/plan/plan-a/wo/WO-90-savings-ui.md](plan/plan-a/wo/WO-90-savings-ui.md) | Historical work orders | 389 | WO-90 — transport UI slice 4: the savings / negotiation-evidence workspace | 0 |
| [docs/plan/plan-a/wo/WO-91-excise-refund.md](plan/plan-a/wo/WO-91-excise-refund.md) | Historical work orders | 461 | Objective and business value | 0 |
| [docs/plan/plan-a/wo/WO-92-excise-ui.md](plan/plan-a/wo/WO-92-excise-ui.md) | Historical work orders | 414 | WO-92 — transport UI slice 5: the diesel-excise screen | 0 |
| [docs/plan/plan-a/wo/WO-93-client-claim-status.md](plan/plan-a/wo/WO-93-client-claim-status.md) | Historical work orders | 566 | WO-93 — the client claim-status portal (G4.4 / R39) | 0 |
| [docs/plan/plan-a/wo/WO-94-known-defects.md](plan/plan-a/wo/WO-94-known-defects.md) | Historical work orders | 428 | WO-94 — two known defects, each with its evidence (G2.7 D7 · backlog N3) | 0 |
| [docs/plan/plan-a/wo/WO-95-fee-freezing.md](plan/plan-a/wo/WO-95-fee-freezing.md) | Historical work orders | 615 | WO-95 — G2.9: fee freezing on the contingency model (board G2.9 · R13 · C10/C11) | 0 |
| [docs/plan/plan-a/wo/WO-96-dependency-modernisation.md](plan/plan-a/wo/WO-96-dependency-modernisation.md) | Historical work orders | 666 | WO-96 — dependency modernisation: every backend and frontend dependency to latest | 0 |
| [docs/plan/plan-a/wo/WO-97-invoice-layout.md](plan/plan-a/wo/WO-97-invoice-layout.md) | Historical work orders | 320 | WO-97 — the issued-invoice PDF layout (design A, "refined ledger") | 0 |
| [docs/plan/plan-a/wo/WO-98-capture-retry-review-guard.md](plan/plan-a/wo/WO-98-capture-retry-review-guard.md) | Historical work orders | 77 | WO-98 — a retry may not silently destroy a human's corrections | 0 |
| [docs/plan/plan-a/wo/WO-99-failed-capture-worklist.md](plan/plan-a/wo/WO-99-failed-capture-worklist.md) | Historical work orders | 103 | WO-99 — H-1, the failed-capture worklist | 0 |
| [docs/plan/plan-a/wo/WO-AB-checklist-missing-rules.md](plan/plan-a/wo/WO-AB-checklist-missing-rules.md) | Historical work orders | 162 | WO-AB — the claimant checklist's missing rules (G2.10 slice 2, R45) | 0 |
| [docs/plan/plan-a/wo/WO-AC-refund-estimate-funnel.md](plan/plan-a/wo/WO-AC-refund-estimate-funnel.md) | Historical work orders | 115 | WO-AC — the refund-estimate funnel (G4.8, R43) | 0 |
| [docs/plan/plan-a/wo/WO-AD-billing-go-live-wiring.md](plan/plan-a/wo/WO-AD-billing-go-live-wiring.md) | Historical work orders | 97 | WO-AD — billing go-live wiring: retention rides the ladder | 0 |
| [docs/plan/plan-a/wo/WO-AE-idp-role-mapping.md](plan/plan-a/wo/WO-AE-idp-role-mapping.md) | Historical work orders | 88 | WO-AE — IdP role mapping for the four business roles: one role vocabulary | 0 |
| [docs/plan/plan-a/wo/WO-AF-statement-byte-vaulting.md](plan/plan-a/wo/WO-AF-statement-byte-vaulting.md) | Historical work orders | 74 | WO-AF — Statement-byte vaulting: a finding points at a file that exists | 0 |
| [docs/plan/plan-b/GREENFIELD_plan.md](plan/plan-b/GREENFIELD_plan.md) | Plans and operations | 318 | GREENFIELD PLAN v2 — start from zero, both repos deleted | 0 |
| [docs/plan/plan-b/README.md](plan/plan-b/README.md) | Plans and operations | 13 | Plan B — greenfield (runbook) | 0 |
| [docs/plan/plan-b/wo/G-01.md](plan/plan-b/wo/G-01.md) | Historical work orders | 51 | G-1 — New repo bootstrap (specs in-tree, CI green on empty scaffold) | 0 |
| [docs/plan/plan-b/wo/G-02.md](plan/plan-b/wo/G-02.md) | Historical work orders | 30 | G-2 — Money + time core | 0 |
| [docs/plan/plan-b/wo/G-03.md](plan/plan-b/wo/G-03.md) | Historical work orders | 44 | G-3 — Tenancy spine (orgs, users, RLS, parity test, composite FKs) | 0 |
| [docs/plan/plan-b/wo/G-04.md](plan/plan-b/wo/G-04.md) | Historical work orders | 35 | G-4 — Structural authorization (deny-by-default, both-direction CI) | 0 |
| [docs/plan/plan-b/wo/G-05.md](plan/plan-b/wo/G-05.md) | Historical work orders | 31 | G-5 — Audit chain + domain events | 0 |
| [docs/plan/plan-b/wo/G-06.md](plan/plan-b/wo/G-06.md) | Historical work orders | 35 | G-6 — Job queue + scheduler + worker | 0 |
| [docs/plan/plan-b/wo/G-07.md](plan/plan-b/wo/G-07.md) | Historical work orders | 34 | G-7 — File gate + content-addressed object storage | 0 |
| [docs/plan/plan-b/wo/G-08.md](plan/plan-b/wo/G-08.md) | Historical work orders | 31 | G-8 — AR data model + concurrency-safe numbering | 0 |
| [docs/plan/plan-b/wo/G-09.md](plan/plan-b/wo/G-09.md) | Historical work orders | 34 | G-9 — AR lifecycle + server-side VAT engine | 0 |
| [docs/plan/plan-b/wo/G-10.md](plan/plan-b/wo/G-10.md) | Historical work orders | 33 | G-10 — AR outputs: PDF, EN-16931 XML, Factur-X, idempotent delivery | 0 |
| [docs/plan/shared/00_MASTER_CONTEXT.md](plan/shared/00_MASTER_CONTEXT.md) | Plans and operations | 228 | PART A — MASTER CONTEXT PROMPT | 0 |
| [docs/plan/shared/DECOMMISSION_FLEET_FUEL.md](plan/shared/DECOMMISSION_FLEET_FUEL.md) | Plans and operations | 87 | PART F — FLEET FUEL DECOMMISSION (RUN ONCE) | 0 |
| [docs/plan/shared/REVIEW_PROMPTS.md](plan/shared/REVIEW_PROMPTS.md) | Plans and operations | 177 | PART D — SPECIALIST REVIEW PROMPTS | 0 |
| [docs/plan/shared/VAT_HARVEST.md](plan/shared/VAT_HARVEST.md) | Plans and operations | 170 | PART E — VAT/TRANSPORT HARVEST PROMPT | 0 |
| [docs/plan/shared/WORK_ORDER_TEMPLATE.md](plan/shared/WORK_ORDER_TEMPLATE.md) | Plans and operations | 95 | PART C — WORK-ORDER TEMPLATE | 0 |
| [docs/plan/shared/specs/BA_bidit.md](plan/shared/specs/BA_bidit.md) | Plans and operations | 1447 | InvoiceIQ — Business Analysis & Rebuild Specification | 0 |
| [docs/plan/shared/specs/BA_fleet_fuel.md](plan/shared/specs/BA_fleet_fuel.md) | Plans and operations | 1731 | Fleet Fuel & VAT Refund System — Business Analysis for Rebuild | 0 |
| [docs/product/metrics.md](product/metrics.md) | Product and design | 136 | InvoiceIQ — Product Metrics & Event-Tracking Plan | 0 |
| [docs/product/personas.md](product/personas.md) | Product and design | 124 | InvoiceIQ — Personas & Value Propositions | 0 |
| [docs/product/pricing-hypothesis.md](product/pricing-hypothesis.md) | Product and design | 101 | InvoiceIQ — Pricing & Packaging Hypothesis | 0 |
| [docs/product/product-requirements.md](product/product-requirements.md) | Product and design | 320 | InvoiceIQ — Product Requirements Document | 0 |
| [docs/product/risks.md](product/risks.md) | Product and design | 91 | InvoiceIQ — Assumption & Risk Register | 0 |
| [docs/product/workflows.md](product/workflows.md) | Product and design | 185 | InvoiceIQ — Jobs-to-be-Done & Workflow Map | 0 |
| [docs/prompt/PAPERLESS-HARVEST.md](prompt/PAPERLESS-HARVEST.md) | Plans and operations | 251 | PROMPT — Harvest paperless-ngx, build into InvoiceIQ | 0 |
| [docs/reference-investigations/00-MASTER/CUMULATIVE-ENGINEERING-PATTERN-LIBRARY.md](reference-investigations/00-MASTER/CUMULATIVE-ENGINEERING-PATTERN-LIBRARY.md) | Reference investigations | 1152 | Engineering Pattern Library | 0 |
| [docs/reference-investigations/00-MASTER/CUMULATIVE-REFERENCE-REPOSITORY-LEARNINGS.md](reference-investigations/00-MASTER/CUMULATIVE-REFERENCE-REPOSITORY-LEARNINGS.md) | Reference investigations | 5007 | Reference Repository Learnings — D4Vinci/Scrapling | 0 |
| [docs/reference-investigations/00-MASTER/README-FIRST.md](reference-investigations/00-MASTER/README-FIRST.md) | Reference investigations | 67 | InvoiceIQ — Master Reference Repository Knowledge Transfer | 0 |
| [docs/reference-investigations/00-MASTER/TRANSFER-INSTRUCTIONS.md](reference-investigations/00-MASTER/TRANSFER-INSTRUCTIONS.md) | Reference investigations | 78 | Transfer Instructions for the Receiving Engineering Agent | 0 |
| [docs/reference-investigations/DUPLICATES.md](reference-investigations/DUPLICATES.md) | Reference investigations | 60 | Reference investigations — archive index | 0 |
| [docs/reference-investigations/duckdb-knowledge-transfer/LEAD-DEVELOPER-ORDERS-DUCKDB.md](reference-investigations/duckdb-knowledge-transfer/LEAD-DEVELOPER-ORDERS-DUCKDB.md) | Reference investigations | 41 | DuckDB → InvoiceIQ Lead Developer Orders | 0 |
| [docs/reference-investigations/duckdb-knowledge-transfer/POST-IMPLEMENTATION-REVIEW-DUCKDB.md](reference-investigations/duckdb-knowledge-transfer/POST-IMPLEMENTATION-REVIEW-DUCKDB.md) | Reference investigations | 22 | DuckDB cycle post-implementation review | 0 |
| [docs/reference-investigations/duckdb-knowledge-transfer/README-DUCKDB.md](reference-investigations/duckdb-knowledge-transfer/README-DUCKDB.md) | Reference investigations | 21 | DuckDB → InvoiceIQ knowledge-transfer bundle | 0 |
| [docs/reference-investigations/duckdb-knowledge-transfer/VALIDATION-DUCKDB.md](reference-investigations/duckdb-knowledge-transfer/VALIDATION-DUCKDB.md) | Reference investigations | 59 | DuckDB cycle validation | 0 |
| [docs/reference-investigations/duckdb-knowledge-transfer/docs/engineering-pattern-library.md](reference-investigations/duckdb-knowledge-transfer/docs/engineering-pattern-library.md) | Reference investigations | 901 | Engineering Pattern Library | 0 |
| [docs/reference-investigations/duckdb-knowledge-transfer/docs/reference-repository-learnings.md](reference-investigations/duckdb-knowledge-transfer/docs/reference-repository-learnings.md) | Reference investigations | 3680 | Reference Repository Learnings — D4Vinci/Scrapling | 0 |
| [docs/reference-investigations/flask-knowledge-transfer/LEAD-DEVELOPER-ORDERS-FLASK.md](reference-investigations/flask-knowledge-transfer/LEAD-DEVELOPER-ORDERS-FLASK.md) | Reference investigations | 107 | Flask → InvoiceIQ Lead Developer Orders | 0 |
| [docs/reference-investigations/flask-knowledge-transfer/POST-IMPLEMENTATION-REVIEW-FLASK.md](reference-investigations/flask-knowledge-transfer/POST-IMPLEMENTATION-REVIEW-FLASK.md) | Reference investigations | 46 | Flask cycle post-implementation review | 0 |
| [docs/reference-investigations/flask-knowledge-transfer/README-FLASK.md](reference-investigations/flask-knowledge-transfer/README-FLASK.md) | Reference investigations | 30 | Flask → InvoiceIQ knowledge-transfer bundle | 0 |
| [docs/reference-investigations/flask-knowledge-transfer/STATUS-FLASK.md](reference-investigations/flask-knowledge-transfer/STATUS-FLASK.md) | Reference investigations | 34 | Flask reference-cycle status | 0 |
| [docs/reference-investigations/flask-knowledge-transfer/VALIDATION-FLASK.md](reference-investigations/flask-knowledge-transfer/VALIDATION-FLASK.md) | Reference investigations | 71 | Flask cycle validation | 0 |
| [docs/reference-investigations/flask-knowledge-transfer/docs/reference/FLASK-P3-01-deprecation-ratchet.md](reference-investigations/flask-knowledge-transfer/docs/reference/FLASK-P3-01-deprecation-ratchet.md) | Reference investigations | 52 | FLASK-P3-01 — Deprecation-warning ratchet | 0 |
| [docs/reference-investigations/lago-knowledge-transfer/docs/engineering-pattern-library.md](reference-investigations/lago-knowledge-transfer/docs/engineering-pattern-library.md) | Reference investigations | 808 | Engineering Pattern Library | 0 |
| [docs/reference-investigations/lago-knowledge-transfer/docs/reference-repository-learnings.md](reference-investigations/lago-knowledge-transfer/docs/reference-repository-learnings.md) | Reference investigations | 3256 | Reference Repository Learnings — D4Vinci/Scrapling | 0 |
| [docs/reference-investigations/paperless-knowledge-transfer/BRANCH-PROTECTION.md](reference-investigations/paperless-knowledge-transfer/BRANCH-PROTECTION.md) | Reference investigations | 31 | InvoiceIQ main-branch protection order | 0 |
| [docs/reference-investigations/paperless-knowledge-transfer/POST-IMPLEMENTATION-REVIEW.md](reference-investigations/paperless-knowledge-transfer/POST-IMPLEMENTATION-REVIEW.md) | Reference investigations | 60 | Post-implementation review | 0 |
| [docs/reference-investigations/paperless-knowledge-transfer/README.md](reference-investigations/paperless-knowledge-transfer/README.md) | Reference investigations | 58 | Paperless-ngx → InvoiceIQ knowledge-transfer bundle | 0 |
| [docs/reference-investigations/paperless-knowledge-transfer/VALIDATION.md](reference-investigations/paperless-knowledge-transfer/VALIDATION.md) | Reference investigations | 41 | Validation report | 0 |
| [docs/reference-investigations/paperless-knowledge-transfer/docs/engineering-pattern-library.md](reference-investigations/paperless-knowledge-transfer/docs/engineering-pattern-library.md) | Reference investigations | 454 | Engineering Pattern Library | 0 |
| [docs/reference-investigations/paperless-knowledge-transfer/docs/reference-repository-learnings.md](reference-investigations/paperless-knowledge-transfer/docs/reference-repository-learnings.md) | Reference investigations | 1389 | Reference Repository Learnings — D4Vinci/Scrapling | 0 |
| [docs/reference-investigations/personal-security-checklist-knowledge-transfer/APPLY.md](reference-investigations/personal-security-checklist-knowledge-transfer/APPLY.md) | Reference investigations | 97 | Apply This Reference Cycle to InvoiceIQ | 0 |
| [docs/reference-investigations/personal-security-checklist-knowledge-transfer/LEAD-DEVELOPER-ORDERS.md](reference-investigations/personal-security-checklist-knowledge-transfer/LEAD-DEVELOPER-ORDERS.md) | Reference investigations | 62 | Lead Developer Orders — Personal Security Checklist Transfer | 0 |
| [docs/reference-investigations/personal-security-checklist-knowledge-transfer/POST-IMPLEMENTATION-REVIEW-PSC.md](reference-investigations/personal-security-checklist-knowledge-transfer/POST-IMPLEMENTATION-REVIEW-PSC.md) | Reference investigations | 174 | Post-Implementation Review — Personal Security Checklist → InvoiceIQ | 0 |
| [docs/reference-investigations/personal-security-checklist-knowledge-transfer/README.md](reference-investigations/personal-security-checklist-knowledge-transfer/README.md) | Reference investigations | 105 | Personal Security Checklist → InvoiceIQ Knowledge Transfer | 0 |
| [docs/reference-investigations/personal-security-checklist-knowledge-transfer/VALIDATION-PSC.md](reference-investigations/personal-security-checklist-knowledge-transfer/VALIDATION-PSC.md) | Reference investigations | 76 | Personal Security Checklist → InvoiceIQ Validation | 0 |
| [docs/reference-investigations/personal-security-checklist-knowledge-transfer/docs/engineering-pattern-library.md](reference-investigations/personal-security-checklist-knowledge-transfer/docs/engineering-pattern-library.md) | Reference investigations | 713 | Engineering Pattern Library | 0 |
| [docs/reference-investigations/personal-security-checklist-knowledge-transfer/docs/reference-repository-learnings.md](reference-investigations/personal-security-checklist-knowledge-transfer/docs/reference-repository-learnings.md) | Reference investigations | 2615 | Reference Repository Learnings — D4Vinci/Scrapling | 0 |
| [docs/reference-investigations/personal-security-checklist-knowledge-transfer/docs/security/SECURITY-CONTROLS.md](reference-investigations/personal-security-checklist-knowledge-transfer/docs/security/SECURITY-CONTROLS.md) | Reference investigations | 207 | InvoiceIQ Security Controls | 0 |
| [docs/reference-investigations/personal-security-checklist-knowledge-transfer/docs/security/VULNERABILITY-DISCLOSURE-DECISION.md](reference-investigations/personal-security-checklist-knowledge-transfer/docs/security/VULNERABILITY-DISCLOSURE-DECISION.md) | Reference investigations | 47 | SEC-GOV-001 — Vulnerability Disclosure Decision | 0 |
| [docs/reference-investigations/personal-security-checklist-knowledge-transfer/proposed/.github/PULL_REQUEST_TEMPLATE.md](reference-investigations/personal-security-checklist-knowledge-transfer/proposed/.github/PULL_REQUEST_TEMPLATE.md) | Reference investigations | 72 | Objective | 0 |
| [docs/reference-investigations/scrapling-knowledge-transfer/README.md](reference-investigations/scrapling-knowledge-transfer/README.md) | Reference investigations | 52 | Scrapling → InvoiceIQ Knowledge-Transfer Bundle | 0 |
| [docs/reference-investigations/scrapling-knowledge-transfer/VALIDATION.md](reference-investigations/scrapling-knowledge-transfer/VALIDATION.md) | Reference investigations | 76 | Validation Report — Scrapling Knowledge Transfer | 0 |
| [docs/reference-investigations/scrapling-knowledge-transfer/docs/AI-ENGINEERING-POLICY.md](reference-investigations/scrapling-knowledge-transfer/docs/AI-ENGINEERING-POLICY.md) | Reference investigations | 224 | AI Engineering Policy | 0 |
| [docs/reference-investigations/scrapling-knowledge-transfer/docs/engineering-pattern-library.md](reference-investigations/scrapling-knowledge-transfer/docs/engineering-pattern-library.md) | Reference investigations | 347 | Engineering Pattern Library | 0 |
| [docs/reference-investigations/scrapling-knowledge-transfer/docs/reference-repository-learnings.md](reference-investigations/scrapling-knowledge-transfer/docs/reference-repository-learnings.md) | Reference investigations | 761 | Reference Repository Learnings — D4Vinci/Scrapling | 0 |
| [docs/reference-investigations/stirling-pdf-knowledge-transfer/LEAD-DEVELOPER-ORDERS-STIRLING.md](reference-investigations/stirling-pdf-knowledge-transfer/LEAD-DEVELOPER-ORDERS-STIRLING.md) | Reference investigations | 81 | Stirling-PDF → InvoiceIQ Lead Developer Orders | 0 |
| [docs/reference-investigations/stirling-pdf-knowledge-transfer/POST-IMPLEMENTATION-REVIEW-STIRLING.md](reference-investigations/stirling-pdf-knowledge-transfer/POST-IMPLEMENTATION-REVIEW-STIRLING.md) | Reference investigations | 45 | Stirling-PDF cycle post-implementation review | 0 |
| [docs/reference-investigations/stirling-pdf-knowledge-transfer/README-STIRLING.md](reference-investigations/stirling-pdf-knowledge-transfer/README-STIRLING.md) | Reference investigations | 27 | Stirling-PDF → InvoiceIQ engineering bundle | 0 |
| [docs/reference-investigations/stirling-pdf-knowledge-transfer/STATUS-STIRLING.md](reference-investigations/stirling-pdf-knowledge-transfer/STATUS-STIRLING.md) | Reference investigations | 24 | Stirling-PDF reference-cycle status | 0 |
| [docs/reference-investigations/stirling-pdf-knowledge-transfer/VALIDATION-STIRLING.md](reference-investigations/stirling-pdf-knowledge-transfer/VALIDATION-STIRLING.md) | Reference investigations | 59 | Stirling-PDF cycle validation | 0 |
| [docs/reference-investigations/stirling-pdf-knowledge-transfer/docs/engineering-pattern-library.md](reference-investigations/stirling-pdf-knowledge-transfer/docs/engineering-pattern-library.md) | Reference investigations | 1016 | Engineering Pattern Library | 0 |
| [docs/reference-investigations/stirling-pdf-knowledge-transfer/docs/reference-repository-learnings.md](reference-investigations/stirling-pdf-knowledge-transfer/docs/reference-repository-learnings.md) | Reference investigations | 4291 | Reference Repository Learnings — D4Vinci/Scrapling | 0 |
| [docs/reference-investigations/twenty-knowledge-transfer/POST-IMPLEMENTATION-REVIEW.md](reference-investigations/twenty-knowledge-transfer/POST-IMPLEMENTATION-REVIEW.md) | Reference investigations | 126 | Twenty → InvoiceIQ Post-Implementation Review | 0 |
| [docs/reference-investigations/twenty-knowledge-transfer/README.md](reference-investigations/twenty-knowledge-transfer/README.md) | Reference investigations | 75 | Twenty → InvoiceIQ Knowledge-Transfer Bundle | 0 |
| [docs/reference-investigations/twenty-knowledge-transfer/VALIDATION.md](reference-investigations/twenty-knowledge-transfer/VALIDATION.md) | Reference investigations | 41 | Twenty implementation validation | 0 |
| [docs/reference-investigations/twenty-knowledge-transfer/docs/ai-agent-action-safety.md](reference-investigations/twenty-knowledge-transfer/docs/ai-agent-action-safety.md) | Reference investigations | 59 | InvoiceIQ AI / MCP Action Safety Contract | 0 |
| [docs/reference-investigations/twenty-knowledge-transfer/docs/engineering-pattern-library.md](reference-investigations/twenty-knowledge-transfer/docs/engineering-pattern-library.md) | Reference investigations | 582 | Engineering Pattern Library | 0 |
| [docs/reference-investigations/twenty-knowledge-transfer/docs/migration-plan-before-apply.md](reference-investigations/twenty-knowledge-transfer/docs/migration-plan-before-apply.md) | Reference investigations | 53 | Migration Safety: Plan Before Apply | 0 |
| [docs/reference-investigations/twenty-knowledge-transfer/docs/reference-repository-learnings.md](reference-investigations/twenty-knowledge-transfer/docs/reference-repository-learnings.md) | Reference investigations | 1911 | Reference Repository Learnings — D4Vinci/Scrapling | 0 |
| [docs/security/SECURITY-CONTROLS.md](security/SECURITY-CONTROLS.md) | Audit and security | 236 | InvoiceIQ Security Controls | 0 |
| [docs/security/VULNERABILITY-DISCLOSURE-DECISION.md](security/VULNERABILITY-DISCLOSURE-DECISION.md) | Audit and security | 55 | SEC-GOV-001 — Vulnerability disclosure decision | 0 |
| [docs/security/authorization-policy-matrix.md](security/authorization-policy-matrix.md) | Audit and security | 212 | Authorization Policy Matrix | 0 |
| [docs/security/cross-tenant-isolation-report.md](security/cross-tenant-isolation-report.md) | Audit and security | 65 | Cross-Tenant Isolation — Test Report | 0 |
| [docs/security/identity-authz-gap-analysis.md](security/identity-authz-gap-analysis.md) | Audit and security | 192 | Identity, Org Management & Tenant Isolation — Audit & Gap Analysis | 0 |
| [docs/security/multi-org-membership-plan.md](security/multi-org-membership-plan.md) | Audit and security | 96 | Multi-Org Membership & Organization Switching — Migration Plan | 0 |
| [docs/transport/harvest-protocol.md](transport/harvest-protocol.md) | Plans and operations | 141 | Fleet Fuel harvest protocol (WO-6 — binding on every Epic-G PR) | 0 |
| [docs/transport/rules.md](transport/rules.md) | Plans and operations | 64 | Transport vertical — harvested rule ledger | 0 |
| [frontend/src/components/ui/README.md](../frontend/src/components/ui/README.md) | Plans and operations | 177 | InvoiceIQ UI kit | 0 |

## Missing relative link destinations

Historical bundle paths and shelved Plan B references account for some of these entries. No links were automatically changed.

| Source | Line | Destination |
|---|---:|---|
