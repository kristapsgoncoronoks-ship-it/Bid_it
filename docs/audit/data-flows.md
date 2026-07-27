# Data Flows — Per-Workflow Request/Data-Flow Detail

Companion to `docs/audit/repository-inventory.md` (module/stack map). Compiled for the Phase 1-11
independent SaaS review board audit from the journey traces independently verified by the Lead Product
Developer (`docs/audit/functional-audit.md` §1) and the Lead System Architect
(`docs/audit/system-architecture.md` §1.3-1.4), cross-referenced against the route/service inventory in
`docs/audit/repository-inventory.md`. Every step below is grounded in a file/line read or a live test run
cited in those source reports — see them for the underlying evidence.

---

## 1. AP — invoice upload → extract → review → approve → pay

```
Client                          API tier                          Worker / async tier
------                          --------                          -------------------
POST /invoices/upload  ───────► filesec.check() (malware/type gate)
                                 documents.store() (SHA-256, dedup)
                                 create ExtractionRun(status=queued)
                                 enqueue Job(kind=upload-extract,
                                   idempotency_key=upload-extract:{run.id})
                                 return 202 ─────────────────────► app.worker picks up job
                                                                    extraction.extract_upload():
                                                                      load bytes from storage
                                                                      parse_invoice_file() in threadpool
                                                                      (deterministic UBL/CII/Factur-X
                                                                       first → OCR fallback)
                                                                      persist draft Invoice + fields,
                                                                      or ExtractionRun(status=failed)
GET /invoices/upload/{run_id} ─► poll ExtractionRun.status
                                 (only returns draft once status=parsed)
PATCH .../review        ───────► invoice_review.py: edit draft fields
PUT .../lines            ───────► edit line items
POST .../submit          ───────► validation.reconcile(inv)  [BLOCKING, zero-tolerance]
                                 validation.run_checks(inv)   [advisory]
                                 refuse submit on any blocking finding
                                 wf.assert_version / wf.assert_transition
                                 ApprovalStep chain built (ap.build_chain)
POST .../approve|reject|         wf.assert_version/assert_transition before any state change
  return|reassign|transition ──► ApprovalStep chain walked (ap.evaluate)
POST /payment-runs        ─────► payment_run.py: select approved invoices
                                 _assert_vendors_payable() [check #1 — creation time]
POST .../{run_id}/approve  ────► approve_run(): row-locked (with_for_update)
                                 _enforce_sod() — maker≠checker (creator ≠ approver)
POST .../{run_id}/pay      ────► pay_run(): row-locked (with_for_update)
                                 _enforce_sod(include_approver=True) — payer ≠ creator/approver
                                 _assert_vendors_payable() [check #2 — pay time, re-checked]
                                 ap_payments.set_cumulative() — append-only AP ledger
                                 mark_paid: workflow_state/status → paid
GET .../{run_id}/export     ───► payment_run.export_csv() — SEPA pain.001 XML
                                 export-once; re-export requires confirm_reexport=true
```
**Concurrency guarantees (independently verified live against Postgres):** `test_payment_run_pay_concurrency.py`
fires two truly-concurrent `pay` calls at the same run and asserts exactly one settles
(`docs/audit/test-baseline.md`).
**Known gap on this flow:** `payment_run.export_csv()`'s CSV output is not formula-injection-sanitized
(`docs/audit/remediation-roadmap.md` R1, P1).

---

## 2. AR — invoice create → issue → PDF/XML → send → credit-note → cash-application

```
Client                          API tier
------                          --------
POST /issued-invoices    ─────► issued.create_issued() → issued_service.build_invoice()
                                 (single shared constructor — also used by the recurring-invoice
                                  generator; snapshots the seller once)
PATCH .../{id}           ─────► edit_draft() (draft-only)
POST .../{id}/approve    ─────► approve_draft()
POST .../{id}/issue      ─────► issue_draft():
                                   issuer.lock() — SELECT ... FOR UPDATE on the IssuerProfile row
                                   allocate gap-free invoice number (per-issuer sequence)
                                   recompute due date server-side
                                   lifecycle → ISSUED
GET .../{id}/pdf | /xml  ─────► _require_numbered() gate — refuses a draft (no PDF pre-issue)
POST .../{id}/send       ─────► mailer.send():
                                   record to email_messages outbox FIRST
                                   deliver over SMTP only if settings.smtp_enabled
                                   delivery failure caught → status=failed, never raised,
                                     never silently reported as sent
                                   idempotent resend (already_sent=True returns the first row)
POST .../{id}/credit-note ────► create_credit_note():
                                   _load(db, org_id, invoice_id)  ◄── NO ROW LOCK (see gap below)
                                   already_credited() reads cached credited_total
                                   remaining = total - already; VAT-preserving partial scaling
                                   original.credited_total = min(already + this_credit, total)
                                   issuer.lock() acquired AFTER the unlocked read — protects
                                     numbering only, not credited_total
POST .../{id}/payment     ────► record_payment():
                                   _load(..., lock=True)  ◄── ROW LOCK (correct pattern)
                                   cap at effective_total = total - credited_total
                                   payments.set_cumulative() — append-only ledger (signed delta
                                     recorded as a NEW row, never mutates a prior row)
```
**Concurrency guarantees (independently verified live against Postgres):**
`test_numbering_concurrency.py` fires 16 concurrent `issue` transactions on the same issuer and asserts
numbers 1..16 allocated with no gaps/dupes (`docs/audit/test-baseline.md`,
`docs/audit/functional-audit.md` §1.2).
**Known gap on this flow:** `create_credit_note()` has no row lock — the debate stage reproduced a live
lost-update on `credited_total` under two concurrent partial credit notes
(`docs/audit/remediation-roadmap.md` R2, **P0** — the highest-priority item on the roadmap).

---

## 3. Expenses — create → submit → approve → reimburse

```
Client                          API tier
------                          --------
POST /expenses            ────► expenses.create_report()
POST .../{id}/receipts     ────► upload_receipt():
                                   filesec.check() — type-sniff (PNG/JPEG/PDF allowlist) + malware scan
                                   vault by SHA-256; document_versions.record() — version history row
POST .../{id}/submit       ────► submit_report(); _require_owner_editable enforced (claimant can't
                                   edit once submitted)
POST .../{id}/decision     ────► decide():
                                   _load() — PLAIN select(), NO row lock, NO version field on
                                     ExpenseDecision schema  ◄── GAP (see below)
                                   expense_approval.decide_step() — walks the approval chain,
                                     analogous to AP's ApprovalStep chain
POST .../{id}/mark_reimbursed  ─► decide("mark_reimbursed") — LIVE UI-reachable shortcut that sets
                                   status=reimbursed directly, bypassing the reimbursement-batch lock
POST /reimbursements/batches ──► reimbursement.create_batch(): groups only APPROVED, un-batched
                                   reports; refuses anything else
POST .../{batch_id}/pay      ──► pay_batch():
                                   _load(..., lock=True)  ◄── ROW LOCK on the BATCH (correct, but
                                     no SoD check between batch creator and payer — see gap below)
                                   eur_of() — FAILS CLOSED on a foreign-currency report with no
                                     stamped total_eur (refuses to pay, does not silently treat the
                                     raw foreign total as EUR)
                                   emits the SEPA/CSV payout file
GET .../{batch_id}/export    ──► reimbursement.export_csv()
```
**Known gaps on this flow:**
1. `decide()` has no optimistic-concurrency version field or row lock — unlike every other
   money-adjacent mutation route in the codebase; the live `mark_reimbursed` shortcut bypasses the one
   lock (`reimbursements.py`) that does exist. Un-deduplicated `webhooks.emit()` would double-fire on a
   race. (`docs/audit/remediation-roadmap.md` R4, P1.)
2. `reimbursement.mark_paid` has no maker≠checker (SoD) check between the batch's creator and its payer,
   unlike the analogous AP `payment_run._enforce_sod`. (`docs/audit/remediation-roadmap.md` R6, P2.)
3. `reimbursement.export_csv()`'s CSV output is not formula-injection-sanitized, same defect class as
   AP's payment-run export. (`docs/audit/remediation-roadmap.md` R1, P1.)

---

## 4. Cross-cutting data flow: tenant isolation (applies to every workflow above)

```
Every authenticated request
  → app/api/deps.py::get_current_user()
      sets tenant.set_current_org(user.org_id)   [BEFORE any tenant-scoped query runs]
  → every ORM SELECT for a TENANT_MODELS-registered model
      auto-ANDed with org_id == current_org via do_orm_execute/with_loader_criteria
  → every write, additionally, hits Postgres FORCE ROW LEVEL SECURITY
      (independently verified: pg_class.relforcerowsecurity = 't' on vendors/invoices/users)
Background jobs (app.worker)
  → job_handlers.py dispatch wraps the handler in the SAME set_current_org() call
      before the handler runs — a worker-processed job is scoped exactly like a live request
```
Three independent layers, all live-verified against a real Postgres cluster this audit stood up itself
(`docs/audit/security-findings.md` §2.2). The one intentional gap: with the `app.current_org` GUC unset
(i.e., a raw `psql`/BI-tool connection as the `appuser` DB role, never the app's own request lifecycle),
RLS fails open by design — recommended as an explicit operational rule
(`docs/audit/remediation-roadmap.md` — "Verified controls" section).

---

## 5. Cross-cutting data flow: file-upload security gate (applies to every intake path)

```
8 UploadFile route parameters (expenses×3, invoice_review, invoices, issued, issuer, reconciliation)
  → EVERY ONE calls filesec.check()/reject_active_content() BEFORE documents.store() or parse
2 non-multipart email-intake paths (POST /email/inbound JSON, Mailgun multipart adapter)
  → BOTH funnel through email_intake.process_attachment()
      → filesec.check() at email_intake.py:117, before storage
filesec.check() itself:
  → magic-byte sniff + allowlist + size cap
  → EICAR signature match → reject
  → ClamAV scan IF clamav_enabled (off by default) — FAILS CLOSED on scanner-unreachable/non-OK
```
All 8+2 = 10 intake paths independently traced to a `filesec` call site preceding storage/parsing, no
bypass found (`docs/audit/security-findings.md` §2.3). The ClamAV branch itself has no automated test
coverage (`docs/audit/remediation-roadmap.md` R7, P2) — verified correct in the debate stage via a
monkeypatched fake `clamd` module, but unreachable by CI today.

---

*Source reports: `docs/audit/functional-audit.md` §1, `docs/audit/system-architecture.md` §1.3-1.4 & §2,
`docs/audit/test-baseline.md`, `docs/audit/agent-debate.md`. Module/stack context:
`docs/audit/repository-inventory.md`.*
