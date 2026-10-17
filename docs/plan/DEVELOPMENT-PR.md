Invoice retries could duplicate issued documents or secondary payment effects,
SMTP-unconfigured delivery could appear sent, and account switching/offboarding
could retain access or stale data. Received-invoice capture and accountant exports
also lacked several source-evidence and action-state safeguards.

This change adds durable invoice/delivery and cash-application commands, explicit
payment reversal, bounded accountant export preview/download, session-specific
workspace identity and browser-bound single-use OIDC, and calendar revocation.
Received capture gains supplier identifiers, captured/calculated line evidence,
bounded page previews, VAT rate summaries and document-only supplier IBAN evidence.
Reconciliation retains the source currency. The GitHub submission helper provides
checked branch/commit/push/draft-PR automation.

Validation is documented per slice under docs/plan and in
`docs/DEVELOPMENT-REVIEW-2026-10-13.md`. The submission helper runs local backend
checks, frontend lint/build, bundle and PII gates before publishing. PostgreSQL,
browser and other GitHub CI results must be reviewed on this PR. Historical test
reports are evidence for their recorded source state, not a single all-green
full-suite result for the combined final change.

Seven additive Alembic revisions extend the previously deployed schema; review
migration and rollback notes in the slice reports before rollout. Source bank
instructions never replace approved vendor payment accounts. Source snapshots and
financial states are not backfilled automatically. Accountant product templates,
production identity-provider verification and external go-live decisions remain
tracked separately. This PR is created as a draft for review.
