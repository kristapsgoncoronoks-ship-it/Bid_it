# N4 — bounded capture page previews

Status: implemented locally for review on 14 October 2026. CI, merge and
production deployment are not claimed. This continues the unblocked capture
backlog after supplier identifiers, R011/R002/R012 and N1 line amounts.

## Behavior and limits

Direct-upload captures produce reduced JPEG page images in the existing
`upload.extract` worker lane after successful extraction. PDF, JPEG and PNG
sources are supported, including structured/hybrid PDFs. CSV/JSON/XML and older
captures retain the original-document viewer/download. Email intake's separate
review flow is not changed by this slice.

Rendering reuses installed pypdfium2 and Pillow through an isolated subprocess;
it does not run on a read request or the API thread. It has these independent
preview limits:

- First **6 pages**, at most **960 pixels** on the long edge.
- **512 KiB per JPEG**, **2 MiB total JPEG bytes** per capture.
- **32 MiB input** and **8 million source-image pixels** for preview generation.
- **12 seconds wall time**; Linux children also have **10 CPU seconds** and
  **512 MiB address space** limits. These are limits, not measured peak RSS.

The renderer closes native page/bitmap/image handles, composites transparency
onto white and omits original image EXIF. The existing source remains unchanged
and authoritative. A manifest records actual source-page count and truncation;
the UI explicitly directs the user to the original for remaining pages. A
preview is not certification that every page was extracted or that a reduced
image is sufficient to verify fine detail.

Object bytes go through existing tenant-prefixed, content-addressed storage and
document registration under `capture-pages`. A nullable run `preview_json`
contains the bounded manifest. Existing runs stay NULL. Successful metadata is
reused on extraction retries, rather than rendering the immutable source again.
Timeouts, render errors and preview-storage failures are advisory: they record
unavailable status while successful extraction remains usable. Preview failure
does not fabricate a ready page. Original-download recovery stays available;
read retries reload stored metadata/bytes, they do not regenerate failed previews.

New read contracts inherit invoice-read permission:

- `GET /invoices/captures/{run_id}/pages` returns scoped metadata.
- `GET /invoices/captures/{run_id}/pages/{page_number}/doc` serves the referenced
  JPEG with inline disposition, nosniff and private/no-store headers. Unknown,
  other-tenant, out-of-range, missing or checksum-mismatched pages return opaque
  404. Storage keys and source filenames do not come from request-supplied paths.

The browser fetches only the selected page when previews exist. The original
downloads only on explicit request; unavailable previews offer **Load original
viewer** rather than automatically downloading a potentially large fallback.
Query keys include account, company, capture
and selected page; local selection remounts on scope change. Blob URLs are created
only by the mounted image view and revoked on change/unmount. Decoder failures
show recovery, rather than a silently broken image. This does not claim to repair
the separate ECC whole-application cache/session isolation findings.

## Buttons, descriptions and prompts

| Button | Description | Engineering prompt |
|---|---|---|
| Previous page | Show the preceding stored preview. | Fetch only the selected scoped JPEG, revoke the old Blob URL and disable at the first available page. |
| Next page | Show the next stored preview. | Advance only through recorded pages, disable at the last available preview and visibly explain truncation without inventing later pages. |
| Download original | Download the unchanged source, including pages beyond preview limits. | Fetch the existing authorized source route on demand, disable duplicate clicks while pending, parse failures into visible recovery and never label a JPEG as the original. |
| Retry previews | Reload preview metadata after a read failure. | Refetch in account/company/capture scope, retain original-document fallback and avoid rendering on the API tier. |
| Retry page | Reload the selected stored JPEG after a read/decode failure. | Preserve selection, clear decoder error, re-fetch authenticated bytes and keep original download available if the stored object is gone. |
| Load original viewer | Open the full-source fallback when page previews are unavailable. | Defer the large original fetch until explicit user action and preserve loading/error/download recovery in the existing viewer. |
| Original viewer Download (fallback) | Retain original viewing/downloading when no page previews were recorded. | Preserve the existing source viewer for unsupported/historical/unavailable captures; explain absence without adding a fake preview or extraction failure. |

## Migration and retention

Migration `c8d9e0f1a2b3` follows `b7c8d9e0f1a2` and adds nullable Text metadata to
the existing tenant-scoped run table, with no new RLS table or application
dependency. Migrate before serving this application. An application rollback may
retain the column; database downgrade discards manifest metadata and leaves the
unchanged original intact. Derived objects are registered tenant documents;
shared content-addressed objects are not deleted on a single run's retry or
removal. As with existing storage writes, object storage is outside the DB
transaction: failed registration can leave bytes requiring storage reconciliation.
This slice does not introduce a new original-document retention policy or an
automatic backfill/regeneration operation for historical captures.

## Local evidence

- **49 backend tests passed**: actual bounded PDF/JPEG rendering, PNG conversion,
  truncation, dimensions/bytes/EXIF, persisted worker manifest and registry,
  idempotent reuse, serving headers, missing bytes, tenant denial, timeout/storage
  recovery, historical fallback, upload/retry/provenance regressions and domain/
  authorization boundaries.
  A final N4-only rerun passed all **9 cases**, including malformed capture IDs
  returning opaque 404 before any database UUID conversion.
- **7 browser tests passed**: four new page-preview cases and three N1 line-amount
  cases. They verify decoded images, boundary disabling, lazy page fetch,
  truncation, missing-page retry, download errors, original fallback and capture
  correction behavior. The spec is in `npm run test:e2e`.
- **6 migration tests passed** (empty upgrade/downgrade/re-upgrade and model
  parity). Local SQLite and PostgreSQL upgrades passed; SQLite `alembic check`
  is clean.
- Unmocked PDF upload → real persisted extraction worker → JPEG object storage →
  authenticated Chromium review passed for two pages. Neither full source nor
  unselected pages were requested automatically; original download happened on
  explicit click. This is synthetic local evidence, not a provider certification.
- Ruff, whole-app mypy, frontend lint/production build and the existing bundle
  gate passed: **433.7 KiB raw / 127.0 KiB gzip**, under 460/135 budgets.

AI-assisted implementation, action prompts and tests use the existing
architecture. No full CI, real-provider delivery or financial hardware benchmark
is claimed. The earlier PostgreSQL column-selective SET NULL autogeneration
diagnostic remains separate from this additive migration.


Follow-on integration check (2026-10-15): fixed derived-output/memory byte budgets
now live in filesec, the existing cap owner. Renderer input is clamped by the
configured upload cap and its 32 MiB purpose; output/memory bounds are unchanged.
The structural upload-cap gate remains unchanged and passes. See the combined
[ECC validation record](ECC-SECURITY-2026-10-15.md).
