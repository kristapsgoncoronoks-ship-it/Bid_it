# WO-AF — Statement-byte vaulting: a finding points at a file that exists

**Shipped 2026-09-05.** No migration (the document catalog already exists;
`statements` is a new `kind` value in `documents.kind`, a free-text column).

## The gap, as found in the tree

`statement_ingest.ingest_statement` digests the upload
(`extraction_baseline.digest`) and every downstream surface keys on that
digest: WO-Z's review findings (`vat_statement_findings.statement_sha256`),
the anti-drift baseline (`fuel_extraction_baselines.statement_sha256`), and
both statement-level audit events (`target_id=sha`). The bytes themselves were
never stored — `documents.store` did not appear anywhere on the path
(`routes/transport/statements.py`, `services/transport/statement_ingest.py`).

So "line 4: net must be greater than zero" named a line in a file nobody
could open. The refused case was the worst: the operator most needs the file
exactly when registration was blocked, and that was the case that kept the
least.

## What shipped

- **Vault before ingest, through the one choke point.** The upload route
  writes the original with `documents.store(documents.STATEMENTS, …)` —
  content-addressed, tenant-prefixed, idempotent — BEFORE
  `ingest_statement` runs. The object write sits outside the ingest
  transaction on purpose: a refusal rolls the session back, and the bytes must
  survive it.
- **Catalogued in both branches.** The document-registry row (`kind =
  statements`, filename, size, mime, uploader) is written in the transaction
  each branch commits: after ingest on success, after the rollback on refusal.
  The uploader's email is read BEFORE the try, for the same reason the route
  already reads `org_id` there — a rollback expires the identity instance and
  a lazy reload raises `MissingGreenlet` (found the hard way in this slice's
  first run).
- **`GET /transport/statements/{sha}/file`.** `VAT_READ`, like the queue.
  The lookup goes through the tenant's catalog: a digest this workspace never
  vaulted — another tenant's, a malformed one, or a finding from before this
  slice — is an opaque 404 and never a read of the object store on a guess.
  Served inert (`application/octet-stream`, forced download, `nosniff`) and
  audited as `document.download` with `target_type=fuel_statement`, the same
  shape as every other original's download route.
- **The finding says whether the file exists.** `StatementFindingOut.
  file_available` is computed for the whole worklist in one query
  (`document_registry.vaulted`). Findings recorded before vaulting existed
  report `false`; the screen offers **Download the statement** only when it
  can be served. The ingest report offers it too — a just-registered
  statement is always on file now.
- **Retention.** Statement bytes are catalogued like every other original,
  so they appear in the Documents catalog and are counted with the tenant's
  stored bytes. No retention CATEGORY purges fuel transactions today (there is
  none for the transport domain), so nothing purges statement bytes either —
  the same rule as before for the transaction rows they produced. When a
  transport retention category is added, its `_delete_object_bytes` branch
  must delete `documents.STATEMENTS` by the findings' digests.

## Certification

Backend (`tests/transport/test_wo_af_statement_vault.py`, 6 tests): a
registered upload is vaulted, catalogued (sha, filename, size, mime) and
served back byte-identical with the inert headers and the audit event; a
refused upload is vaulted too and its finding reports `file_available: true`;
re-uploading the same bytes keeps one catalog row; a finding whose catalog
row is absent reports `false` and the download is a 404; another tenant's
digest, an upper-case digest and a non-digest are 404s; `user_free`
(`VAT_READ`) may download, `user` (no VAT permission) gets the router's 403.
The seeded violation (the store call removed) fails the first test on the
bytes and the catalog.

Frontend (`e2e/statement-intake.spec.ts`, +1): the finding on file shows the
download control, the pre-vaulting finding does not, and the click fetches by
the finding's own digest.

Contract snapshot regenerated (`docs/api/openapi.json`).
