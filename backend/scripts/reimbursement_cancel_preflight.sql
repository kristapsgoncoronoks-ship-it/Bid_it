-- Tenant-scoped review only. Run with psql --set=org_id=<organization UUID>.
-- Output contains internal IDs, not employee names, bank details or references.
\set ON_ERROR_STOP on
\if :{?org_id}
\else
  \echo 'Required: --set=org_id=<organization UUID>'
  \set org_id ''
\endif

BEGIN READ ONLY;
SET LOCAL statement_timeout = '10s';
SET LOCAL lock_timeout = '1s';
SELECT set_config('app.current_org', (:'org_id')::uuid::text, true);
SELECT current_user AS inspection_role,
       current_setting('transaction_read_only') AS read_only;

WITH scoped_batches AS (
    SELECT id, status, export_count, exported_at, last_msg_id, paid_at
    FROM reimbursement_batches
    WHERE org_id = (:'org_id')::uuid
), scoped_reports AS (
    SELECT id, payout_batch_id, status, reimbursed_at
    FROM expense_reports
    WHERE org_id = (:'org_id')::uuid
), batch_counts AS (
    SELECT b.id, b.status, b.export_count, b.exported_at, b.last_msg_id, b.paid_at,
           count(r.id) AS report_count
    FROM scoped_batches b
    LEFT JOIN scoped_reports r ON r.payout_batch_id = b.id
    GROUP BY b.id, b.status, b.export_count, b.exported_at, b.last_msg_id, b.paid_at
), findings AS (
    SELECT 'export_markers_open' AS category, id::text AS batch_id, NULL::text AS report_id
    FROM batch_counts
    WHERE status = 'open'
      AND (export_count <> 0 OR exported_at IS NOT NULL OR last_msg_id IS NOT NULL)
    UNION ALL
    SELECT 'export_markers_cancelled', id::text, NULL::text
    FROM batch_counts
    WHERE status = 'cancelled'
      AND (export_count <> 0 OR exported_at IS NOT NULL OR last_msg_id IS NOT NULL)
    UNION ALL
    SELECT 'cancelled_with_report_links', id::text, NULL::text
    FROM batch_counts WHERE status = 'cancelled' AND report_count > 0
    UNION ALL
    SELECT 'empty_open_or_paid_batch', id::text, NULL::text
    FROM batch_counts WHERE status IN ('open', 'paid') AND report_count = 0
    UNION ALL
    SELECT 'batch_payment_state_mismatch', id::text, NULL::text
    FROM batch_counts
    WHERE (status = 'paid' AND paid_at IS NULL)
       OR (status <> 'paid' AND paid_at IS NOT NULL)
    UNION ALL
    SELECT 'report_payment_state_mismatch', b.id::text, r.id::text
    FROM scoped_reports r JOIN scoped_batches b ON b.id = r.payout_batch_id
    WHERE (b.status = 'paid' AND (r.status <> 'reimbursed' OR r.reimbursed_at IS NULL))
       OR (b.status <> 'paid' AND (r.status = 'reimbursed' OR r.reimbursed_at IS NOT NULL))
    UNION ALL
    SELECT 'unresolved_same_org_batch_link', r.payout_batch_id::text, r.id::text
    FROM scoped_reports r LEFT JOIN scoped_batches b ON b.id = r.payout_batch_id
    WHERE r.payout_batch_id IS NOT NULL AND b.id IS NULL
    UNION ALL
    SELECT 'invalid_export_count', id::text, NULL::text
    FROM batch_counts WHERE export_count < 0 OR export_count IS NULL
    UNION ALL
    SELECT 'partial_export_markers', id::text, NULL::text
    FROM batch_counts
    WHERE (export_count = 0 AND (exported_at IS NOT NULL OR last_msg_id IS NOT NULL))
       OR (export_count <> 0 AND exported_at IS NULL)
    UNION ALL
    SELECT 'zero_markers_history_unproven', id::text, NULL::text
    FROM batch_counts
    WHERE export_count = 0 AND exported_at IS NULL AND last_msg_id IS NULL
), ranked AS (
    SELECT *, row_number() OVER (PARTITION BY category ORDER BY batch_id, report_id) AS n
    FROM findings
)
SELECT category, count(*) AS total,
       json_agg(json_build_object('batch_id', batch_id, 'report_id', report_id)
                ORDER BY batch_id, report_id) FILTER (WHERE n <= 20) AS first_20_examples
FROM ranked
GROUP BY category
ORDER BY category;

SELECT 'Review categories are not proof of payment, submission or corruption. '
       'Zero markers cannot exclude historical exports backfilled by the controls migration. '
       'Unlinked cancelled reports cannot be reconstructed from this snapshot.' AS limitation;
ROLLBACK;
