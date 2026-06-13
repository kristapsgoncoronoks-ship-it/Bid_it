# Process reliability — stuck/stall risks & hardening

A reliability audit of the operational processes (intake → queue/worker → extraction →
validate/register → consolidate→build_master→history close → VAT claim → invoicing),
grounding each finding in the code and in established best practice. Goal: keep processes
flowing and **never silently stuck**.

The durable queue is *mechanically* sound (fsync-before-commit durability, content-hash
dedup, `BEGIN IMMEDIATE` claim serialization, lease-expiry reclaim, capped backoff, a
separate transient/quota `waiting`/`held` track). The residual risks are **operational** —
an upload gate one bad job can freeze, reclaim that's too slow, dead-letters that are
visible-but-not-actioned, and a non-atomic monthly close.

## A. Adjust for smoothness — stuck/stall risks (priority order)

| # | Risk | Evidence | Sev | Status | Fix (grounded) |
|---|------|----------|-----|--------|----------------|
| 1 | One stuck job freezes ALL uploads, fleet-wide — terminal `failed`/`held` are in `PENDING_STATES`; uploads block while `pending_count()>0` | `waiting_room.py:413`, `app.py:787` | High, silent | NEW | Exclude terminal states from the *upload gate*. *Shed poison messages to a DLQ so they don't block the queue (AWS).* |
| 2 | Register split-brain — on permanent register failure, PDFs are vaulted but no `supplier_invoices`; failure IS visible in the monitor (Retry available) but not distinctly flagged | `app.py:2126`, `waiting_room._fail_or_retry` | High | NEW (D4 shipped; hardening pending) | Distinct "registration failed — statement X" worklist item + vaulted-doc-without-invoice reconcile sweep. *A silent DLQ is the documented anti-pattern.* |
| 3 | Close stale-pickle period mismatch — `consolidated_rows.pkl` carries no period stamp; editing `month_config` between steps loads the wrong period | `consolidate.py:68`, `history.py:69` | High, silent corruption | NEW (fold into D5) | Stamp PERIOD + row-count/hash into the pickle; assert before load. *Key off a logical period (Airflow `logical_date`).* |
| 4 | `rejected` releases invoice locks — contradicts 3B/3C/3D → invoice re-claimable = duplicate-submission | `vat_refund.py:437` | High, integrity | NEW (in backlog) | `rejected` keeps locks; release only via `withdraw_claim`. |
| 5 | No startup orphan-sweep — a crashed worker leaves a `processing` row stuck for the full 600s lease; a hard kill burns all 5 attempts thrashing | `waiting_room.py:48,247` | Med | Partly covered | Reclaim expired `processing` rows on worker start; distinguish process-crash. *Lease-reclaim sweep is THE crashed-worker unstick mechanism (SQS/Pub-Sub).* |
| 6 | Notify scheduler blind on SMTP failure — `notify_last_sent` advances even when send fails | `app.py:688`, `notify.py:206` | Med, silent | NEW | Stamp `last_sent` only on success; record SMTP failures to the error log. *Dead-man's-switch: alert on absence of a successful run.* |
| 7 | Close partial-run, no checkpoint; `history.py` runs at import | `history.py:38-188` | Med | Covered (D5) | D5 orchestrator: restartable, per-step idempotency, body → `main()`. *Staging + atomic swap.* |
| 8 | No aggregate extract deadline; `LEASE_SECONDS=600` < worst-case batch → reclaim + double-work; pypdf probe unbounded | `extract.py:156,255,536,442` | Med, head-of-line | NEW | Per-job deadline / cap members; lease heartbeat; bound pypdf. *Timeout→bounded-retry→circuit-breaker; lease > p99.* |
| 9 | `process_lock`: wall-clock TTL, no fencing — a paused leader past lease can double-act; clock skew can double-elect on a fleet | `process_lock.py:55` | Med, rare | NEW | Monotonic-clock deadline; monotonic `lease_epoch` (fencing token) + compare-and-set; per-action `-run` guard. *Kleppmann fencing tokens + monotonic clocks; SQLite already gives the single linearizable store.* |
| 10 | Swallowed errors hiding a stuck step — `file_documents_for_claim` `except: pass` after lock (locked-but-doc-unfiled); `_import_log` failure blinds the monitor feed | `vat_refund.py:499`, `waiting_room.py:275` | Med, silent | Partly covered | Log via `applog`/`_log_exc`; surface as integrity/feed warnings. |
| 11 | Backup can archive a torn file mid-close — vault/lake walked with plain `open()`, no lock vs close writes | `backup.py:89` | Low | NEW | mtime-recheck/skip, or take `backup-run` lock around close writes. |

## B. Develop more — completeness for smooth, automated flow

- **Real exception/DLQ lane + alerting** — formalize `failed`/`held` into a dead-letter
  view with **growth-rate alerting** (not just depth) and one-click **redrive/replay**.
  *AWS DLQ + alarm on `≥1`; alert on rate of change.*
- **Stuck-job metric** — expose **oldest-pending-job age** (`MIN(created_at)` of pending)
  and alarm vs an SLO; the single best early-warning of a stalled consumer.
  *SQS `ApproximateAgeOfOldestMessage` / Pub-Sub `oldest_unacked_message_age`.*
- **UNMATCHED in-product resolution UI** — "assign invoice ref" so a blocked claim isn't a
  dead end (planned Phase 4).
- **Confidence-routed exception queue** — deterministic high-confidence auto-flows;
  AI/low-confidence quarantines, never gating (planned Phase 5). *IDP exception-queue.*
- **Idempotency-key discipline** on every deferred side effect — side-effect + dedup-insert
  in ONE transaction (reclaim will occasionally redeliver). *Stripe/Brandur idempotency.*

## C. Recommended reliability sprint (highest impact, each small)
**#1** (un-gate uploads from terminal jobs) → **#5** (startup orphan-sweep) → **#6** (notify
only on success) → **#4** (`rejected` keeps locks) → **#3** (pickle period stamp, fold into
D5). These kill the worst *silent* stalls and are each a focused, low-risk change.

## Sources (best-practice grounding)
- Durable queues: AWS SQS visibility-timeout/DLQ docs; Google Pub/Sub ack-deadline;
  RabbitMQ/Celery/Sidekiq retry+DLQ; Stripe/Brandur idempotency keys; DDIA (at-least-once).
- Pipelines: Google SRE *Data Processing Pipelines* (freshness SLOs); Airflow/Dagster/Prefect
  idempotent re-runs & checkpointing; staging+atomic-swap; timeout→retry→circuit-breaker.
- Scheduling/locks: Kleppmann "How to do distributed locking" (fencing tokens, monotonic
  clocks) vs antirez "Is Redlock safe?"; Google SRE *Distributed Periodic Scheduling*
  (skew toward skipping; identify launch by start time); Kubernetes Lease/CronJob
  `concurrencyPolicy`/`startingDeadlineSeconds`; dead-man's-switch monitoring.
