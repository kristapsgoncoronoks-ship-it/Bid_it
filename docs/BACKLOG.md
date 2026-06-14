# Backlog

Consolidated outstanding work, synthesized from this project's audits/research
(`FINDINGS.md`, `RELIABILITY.md`, `DATA_ARCHITECTURE.md`, `PLATFORM.md`, `AI_REVIEW.md`,
`ROADMAP.md`, `SCALING.md`) and the in-flight programs. Grouped by **readiness**:
ready-now (no decision needed) → decision-gated → strategic. Each item notes its source,
rough effort (S/M/L), risk, and any product decision that gates it.

DONE this cycle (not repeated below): decoupling D1–D5; reliability sprint
(orphan-watcher, stuck-job surfacing, notify digest, scheduler, monitoring panel,
upload-gate, startup orphan-sweep, notify-on-success, `rejected` keeps locks); Part-1
logic fixes (goods-code, falsy-zero, quarterly fee-base, money sweep) + the frozen-VAT
clobber fix; pricing-correctness fixes (period bucketing, volume-weighted pack mean);
FX/pivots + report enhancements; monetization M5a/M2/M1/M3/M4/M6; CSRF hardening.

**This sprint (section A, shipped):** intake DLQ alerting + oldest-pending-job age SLO
(`2dd8bdd`); time-of-day fuelling analytics + off-hours anomaly flag (`31df60b`);
supplier/channel processing-reliability scorecard (`2bf5031`); per-vehicle NET €/L
outlier flag + cost summary (`a6e0944`); data-lake confidence mining → parser-build
priorities (`7db2d95`); import-reliability + audit-activity trends (`cc4df3d`); the full
`except: pass` → `applog` migration across the whole codebase (`4af7cf7`/`ce5ac8b`/`e51ef52`/`e116b43`).
**→ Section A's under-used-data analytics runway AND the code-quality except-pass item are now cleared.**

---

## A. Ready now — no decision needed (ordered by value)

### Reliability (RELIABILITY.md)
- ~~**DLQ alerting + oldest-pending-job age SLO metric.**~~ ✅ SHIPPED (`2dd8bdd`) —
  `waiting_room.queue_health()` (DLQ size, oldest-pending age vs 6h SLO, `dlq_growth_24h`
  from a sampled history), surfaced on `/queue` + worklist + notify digest; redrive reuses
  the existing "Send / restart all".
- **Register-failure reconcile.** A distinct "registration failed — statement X" worklist
  item + a vaulted-doc-without-registered-invoice reconcile sweep (the D4 split-brain
  hardening). *(M, low)*
- **`process_lock` fencing token + monotonic-clock deadline.** Add a monotonic
  `lease_epoch` + compare-and-set; measure lease validity with `time.monotonic()`. *(M, med)*
- **Per-job extract deadline / lease sizing.** Cap members or set an aggregate extract
  deadline; bound the pypdf probe; ensure `LEASE_SECONDS` ≥ worst-case batch (or heartbeat). *(M, med)*
- **Backup torn-file mid-close** — mtime-recheck/skip, or take the `backup-run` lock around
  close file-writes. *(S, low)*
- **M5a two-phase write** — wrap `record_payment`'s stamp + status transition atomically. *(S, low)*

### Data de-duplication (DATA_ARCHITECTURE.md Part 1)
- **Derive `gross`/`gross_local`** instead of storing net+vat (or add a CHECK). *(S, low)*
- **Persist only receipt-control overrides** (`waived`/`note`); derive `status`/`expected`. *(S, low)*
- **`supplier_invoices.gross_total`** — reference the statement line or re-sync on
  re-register (currently insert-once, drifts on correction). *(M, low)*
- **Document path-migration completeness** — confirm a SharePoint/FTPS backend migration
  re-points `stored_path` in `intake_jobs`/`data_lake_files`, not just `invoice_documents`. *(S, med)*

### Under-used-data leverage (DATA_ARCHITECTURE.md Part 2)
- ~~**Supplier processing-reliability scorecard**~~ ✅ SHIPPED (`2bf5031`) —
  `waiting_room.reliability_scorecard()` (per-channel success/retry/median-duration +
  failure-reason histogram + per-supplier from draft), rendered on `/queue`.
- ~~**Time-of-day analytics + off-hours anomaly flag**~~ ✅ SHIPPED (`31df60b`) —
  `anomaly` `off_hours` flag (22:00–04:59 diesel) + `time_of_day_summary()`, on `/anomalies`.
- ~~**Per-vehicle €/L & consumption outliers.**~~ ✅ SHIPPED (`a6e0944`) — `anomaly`
  `vehicle_price` flag (fleet per-vehicle NET €/L distribution, high outliers) +
  `vehicle_cost_summary()`, on `/anomalies`. (L/100km deferred — no odometer data.)
- ~~**Data-lake `meta.confidence` mining**~~ ✅ SHIPPED (`7db2d95`) —
  `data_lake.parser_priority()` ranks suppliers by AI-extraction volume × low-confidence
  (best `parse_<x>()` ROI), on `/files`.
- ~~**Import-reliability / audit-activity trends**~~ ✅ SHIPPED (`cc4df3d`) —
  `import_log.reliability()` (success rate by channel & supplier) on `/imports`;
  `audit.activity_summary()` (per-user/per-table activity + churn hotspots) on `/history`.

### Decoupling completion
- **D6 — intake worker as a dedicated worker-process by default** (web nodes set
  `FFS_ROLE=web`, a separate `python waiting_room.py --work`); docs + sample unit. *(M, low)*

### Code-quality
- ~~**Finish `except: pass` → `applog`/`_log_exc` migration**~~ ✅ SHIPPED
  (`4af7cf7`, `ce5ac8b`, `e51ef52`, `e116b43`) — every silent swallow in real code now
  logs (incl. the two named blind spots). Only documented-deliberate guards remain:
  circular log-the-logging-failure fallbacks (`app.py`/`auth.py`/`applog.py`), the
  `O_EXCL` secret-key create-race, the `BEGIN IMMEDIATE` nested-txn guard, the
  `rejected`-keeps-locks no-op branch, and the two bootstrap scripts (`make_cert`/`start`).
- ~~**Money-precision sweep remnants**~~ ✅ SHIPPED (`05c5c24`, `aab11bf`) — unified the
  shadowed `extract._num` to one `money.f2` parser (also fixed a dotted-thousands→0.0
  bug); converted the last currency `round()` (Q8/TFC VAT row_maps) to `money.f2`. The
  remaining `round()` calls are on litres/€-per-L/FX-rates/percentages (correct per CLAUDE.md).
- **Test coverage** for `invoice_control`/`ingest`/`build_master`/`history` (some added). *(M)*

---

## B. Decision-gated — needs a product call first

### Logic correctness (FINDINGS.md Part 1)
- **VAT thresholds in national currency** for non-EUR refund countries (SE/DK/PL) — €400/€50
  is currently compared in EUR. **Decision:** confirm the per-currency minimums (SEK 4 000/500,
  PLN/DKK equivalents). *(M, high value)*
- **Threshold actually gates submission** (below-min blocks or requires override) — today
  it's verdict-text only, so a sub-threshold quarter can be filed and rejected, locking
  invoices out of the annual mop-up. **Decision:** hard gate vs warn. *(M, high)*
- **Deferral coverage** — assert every VAT-bearing quarter is filed-or-mopped before Sept-30.
  **Decision:** force-pull deferred quarters into the annual claim, or allow intentional gaps. *(M)*
- **Country diesel recoverability / pro-rata** — no logic exists. **Decision:** in scope, or
  handled upstream? *(? )*

### Data architecture (DATA_ARCHITECTURE.md)
- **FX provenance + single source of truth** — store the applied rate (or its `ecb_fx` key)
  per line/period so a claim's EUR is traceable. **Decision:** is `month_config.FX`
  (hard-coded `1/4.27`) authoritative, or should it derive from `ecb_fx`? *(M, med)*
- **Promote `card` to the transaction grain?** (enables per-card analytics; needs a schema
  change). **Decision:** card vs vehicle as the unit.
- **`wholesale_prices` default source** so `margin_vs_wholesale` isn't silently inert.
  **Decision:** ship a default market source (EU Oil Bulletin) or keep opt-in.

### Automation program — Phase 2 (PLATFORM.md / the automation plan)
- **Scheduled portal scrape** — **Decision:** which portals are authorized for unattended
  scheduled pulls. *(M, med)*
- **Scheduled API ingest** (DKV/E100) — **Decision:** which APIs authorized + tokens exist. *(M, med)*
- **Receipt-control required-set into the submission gate** — **Decision:** does a MISSING
  required invoice **block** submission or **warn**? *(M, high)*

### Automation program — Phase 3
- **`parse_dkv()` / `parse_e100()` deterministic parsers** — **Prereq:** 2–3 redacted DKV
  and E100 sample invoices. *(M each)*
- **Auto-attach a document to its invoice by parsed invoice_no.** *(S–M)*

### Automation program — Phase 4
- **Structured invoice↔transaction matching** (invoice_no/supplier/amount/period) replacing
  the fragile `note`-substring match, + an **UNMATCHED resolution UI**. Touches legal figures
  — audited, admin-gated. **Decision:** UNMATCHED-resolution authority (processor vs admin). *(L, high)*

### Automation program — Phase 5 (the confidence-learning model)
- **Per-invoice confidence + validation-event ledger; AI validation returns a score;
  settled→skip-AI; per-supplier-×-country trust that grows with each clean validation.**
  Confidence reduces redundant WORK, never bypasses the legal gates. **Decision:** the
  growth/decay constants (proposed init 0.50, growth `+0.25·(0.95−trust)`, decay −0.30,
  floor 0.10) and the skip-AI / human-review thresholds. *(L, the centerpiece)*

---

## C. Strategic / larger bets

- **Monetization Opp. 3 — external pooled benchmark (SALE).** Only if/when selling
  externally; gated by the full legal stack in `FINDINGS.md` (EU competition-law
  hub-and-spoke, GDPR anonymization, min-cohort ≥5/no-single->25%, independent trustee,
  Data Act unfair-terms, data-use license) + counsel. **Internal peer benchmark (M1) is
  already shipped.**
- **Monetization Opp. 5 — embedded finance** (finance the VAT-refund receivable). M3 built
  the data layer; this is the partner-integration / lending layer. *(L)*
- **M4 API follow-ups** — per-key rate-limiting/quotas, key-expiry policy, and a scoped v2
  for any write/extract surface. *(M)*
- **AI review assistant v2** — extend the advisory panel to invoice/claim review surfaces (A6). *(L)*
- **Overcharge → recovery workflow** — turn contract-audit/overpay detection into an
  actionable recovery packet → supplier credit (the analytics "act on it" gap). *(L)*
- **Factur-X follow-ups** — per-file handling of mixed hybrid/plain batches; catalog
  name-tree fallback test. *(S)*
- **Materialize expensive aggregates** (overpay/benchmark/fleet/cycle-time totals) into a
  settled metrics table rebuilt at the close, with a recompute-and-compare drift check
  (DATA_ARCHITECTURE.md "in increments"). *(M, med)*

## D. Platform / scaling (CLAUDE.md / SCALING.md)
- **Validated Postgres cutover** — exercise `_PgShim` on real psycopg; port dialect-isms
  (`datetime('now')`→`now()`, `INSERT OR IGNORE`→`ON CONFLICT`, audit triggers → a PG
  trigger fn); migrate the intake queue + `process_lock` leases to the shared DB;
  Postgres-native backups (`pg_dump`). *(L, needs a live Postgres)*
- **Off-machine backup sync** (OneDrive/SharePoint) so `backups/` survives disk loss. *(M)*
- **Notifications** — per-event alerts + an SMTP relay config UI (the digest mailer is done). *(M)*
- **PDF generation for `.docx` templates** (text templates already export PDF). *(M)*

---

## Consolidated open product decisions (gate the B items)
1. Per-country VAT minimums (SEK/DKK/PLN values) + does the threshold **hard-gate** filing?
2. FX authority: `month_config.FX` vs `ecb_fx`?
3. Receipt-control gate: **block** vs **warn** on a missing required invoice?
4. Which portals/APIs are authorized for unattended scheduled pulls?
5. Redacted DKV/E100 sample invoices (for Phase-3 parsers)?
6. Confidence-learning α/β/thresholds (defaults proposed)?
7. UNMATCHED-resolution authority: processor or admin?
8. Promote `card` to the transaction grain?
9. Diesel recoverability/pro-rata in scope?
10. External SALE of the pooled benchmark — pursue (counsel-gated) or keep internal-only?
