# Reimbursement cancellation preflight — 27 October 2026

Use the [read-only SQL](../../backend/scripts/reimbursement_cancel_preflight.sql)
to review one explicitly selected organization before assessing historical payout
records. This is an operator review tool, not an automatic financial repair or a
requirement to run against production during development.

Use an authorized read-only PostgreSQL connection and libpq environment or a
private `PGPASSFILE`; keep credentials out of command arguments and saved output.
With the organization's UUID in `BIDIT_ORG_ID`, run from the repository root:

```sh
psql --no-psqlrc --set=org_id="$BIDIT_ORG_ID" --file=backend/scripts/reimbursement_cancel_preflight.sql
```

The script refuses a missing/invalid organization UUID, opens a READ ONLY
transaction, sets transaction-local tenant scope, applies explicit organization
predicates, and rolls back. Statement/lock waits are bounded. Confirm the output's
`read_only` value is `on`. Keep the output access-controlled: examples contain
internal batch/report IDs. No employee names, bank data or payment references
are selected for output. Counts cover the selected organization's categories;
examples are capped at twenty per category and are not a complete record export.

Review recorded export markers on open/cancelled batches, cancelled batches
retaining links, empty open/paid batches, contradictory payment state, unresolved
same-organization links, and invalid/partial export markers. A missing same-org
target is not proof of cross-tenant disclosure. Exported-open is a normal workflow
state that needs cancellation protection, not evidence of corrupt or paid data.
An unresolved example prints the selected report's stored batch ID, including a
foreign ID if that is what the report stores. It does not read that other batch's
rows or enumerate its organization.

**Historical limitation:** migration `d4e6f8a0b2c4` initialized export counters and
timestamps to zero/null. `zero_markers_history_unproven` includes ordinary new
records as well as potentially older records; it is not an anomaly count or proof
that no file was ever generated. Resolve historical uncertainty using available
audit/file/bank evidence and the approved operator process. A cancelled exported
batch may already have lost all original report associations; counts in creation
audit metadata cannot reconstruct those identities.

Do not automatically relink reports, reset counters, reverse/record payment,
delete batches, rewrite audit history or create a replacement file. Record
unresolved cases and obtain an evidence-based disposition/repair decision under
decision 33. The minimum server guard blocks currently recorded export markers;
it does not certify or repair undiscoverable historical exports. Reverting that
guard reopens the same cancellation exposure and is not a safe operational fix.
