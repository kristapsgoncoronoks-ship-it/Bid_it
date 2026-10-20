# CAP-WORKER-SOURCE-HASH — verify originals before worker parsing

**18 October 2026 · implemented, pending exact-head CI and human/Lead review.**
AI-assisted investigation, implementation, tests and documentation.

## Confirmed problem and repair

Two regressions replaced a normally admitted CSV with another valid, equal-length
CSV at the same stored key. Both workers parsed **999 instead of 100**, while the
capture still named the original SHA-256. Size bounds alone did not prevent a
wrong-source draft. Both pre-fix tests failed.

`documents.load_capture` now compares the bounded fetched bytes with the recorded
SHA-256 before either worker can parse them. It reuses the existing digest helper
in the threadpool. A mismatch becomes `stored_file_changed`, a handled failed
capture with administrator restoration guidance. It publishes no draft, fields,
previews or invoice from those bytes. The original fingerprint, reference and UI
command remain unchanged; refusal deletes no object and invents no trusted hash.
The existing size cap and cap+1 read remain authoritative before hashing.

The worklist sets `document_retained=false` for a mismatched original: the stored
reference is not proof that a verified original can be read. The SPA explains that
the retrieved document could not be verified, rather than promising an intact
original or declaring it physically gone. It offers no unchanged-input Retry.
After an operator restores the exact original, the existing UI attempt-retry or
email worker replay can succeed. No end-user email retry endpoint was added.

## Evidence

[Sanitized source/image identities, measurements and artifact hashes](evidence/CAP-WORKER-SOURCE-HASH-2026-10-18.json).

- **138 related backend checks passed / 145 warnings**; two new actual-parse
  replacement/restoration regressions exercise both channels, cross-workspace
  worklist isolation, unchanged source/command identity and no invoice/field
  publication on mismatch.
- The **entire required PostgreSQL step passed 130 cases / 201 warnings** on a
  fresh migrated NOSUPERUSER/NOBYPASSRLS database. The delayed read-failure
  interleaving now covers both size refusal and hash mismatch: an old worker
  cannot overwrite a newer UI attempt's result or fields.
- **29 checks passed / 9 warnings** in the shipped Python 3.14.8 image.
- **9 worklist browser regressions passed**, one worker, with traces. An initial
  passing run's text log shared a filename with the native browser driver and
  was overwritten. Its nine traces and passed runner result were archived; a
  separate focused run with a distinct log supplies the credited summary.
- Ruff/format, mypy (**421 sources**), frontend lint/build/bundle passed. First
  load remains **435.8 kB raw / 127.6 kB gzip**, under unchanged 460/135 limits.
- Both rebuilt final application images have fresh **zero HIGH/CRITICAL** scans.
  This does not clear the full build/service image gate.

### Native workflow

The rebuilt API, compiled SPA/NGINX and real workers used PostgreSQL **16.15** with
fresh migrations, a NOSUPERUSER/NOBYPASSRLS application role and an owned local
object volume. API: one worker, 1 CPU/768 MiB. Extraction: two worker containers,
1 CPU/1 GiB each, read-only root, UID 10001, dropped capabilities,
no-new-privileges and separate 128 MiB temporary filesystems. Native PDF/Tesseract
and preview children remained enabled; no parser or API response was mocked.

Normally admitted UI/email CSV objects were changed from 100 to 999 within the
unchanged 15 MiB cap. Both captures became `stored_file_changed`; instrumentation
proved neither changed source reached the parser. Four valid PDF controls still
completed text extraction/OCR, and a subsequent healthy CSV parsed.

Real Chromium showed both failed filenames and administrator restoration guidance,
without a Retry button or false retained/missing-original messages. Readiness was
200. The fixture then restored only its own objects to the exact original bytes.
The existing UI retry and an explicitly enqueued synthetic email worker replay
succeeded using the original hashes and amount **100**. Final totals were **nine
succeeded jobs, four UI commands/captures and three inbound records**, with an
empty failure worklist. Handled integrity failures are not successful extraction;
they succeeded only after restoration. All **nine source reads closed**, with
positive requests of at most 65,536 bytes. Refusal left the changed bytes untouched.

The two initial worker cgroup peaks were **383.46 / 183.93 MiB**, with no memory
max/OOM/oom_kill events. Different job mixes and scheduling prevent treating these
as a comparative benchmark or simultaneous host-memory measurement. No production
limits or deployment sizing changed. Owned containers/networks/volume and synthetic
credential files were removed. The run does not qualify live S3 restoration or
concurrent duplicate email extraction/confirmation.

## Alternatives, review and rollback

**Proposal:** verify identity at the shared, bounded capture-load boundary. This
prevents ordinary parser success from lending trust to changed storage content.

**Alternatives:** admission-only checks cannot detect a later replacement; a
background integrity sweep can find it after a draft has already been published.
Rebinding the capture to the new digest would silently bless different evidence.
Deleting objects is invalid cleanup and could destroy shared references.

**Adversarial objection:** a matching digest does not certify authenticity, safe
content, current permissions or database identity. Correct: existing admission,
security, RLS and UI attempt-publication guards remain required. This patch does
not add email attempt fencing or transactional confirmation guarantees; those
paths need their own PostgreSQL interleavings in CAP-EMAIL-STATE. It also does not
bound unrelated API/download reads or certify all restore/provider behavior.

**Benefit/complexity:** one shared bounded-load identity check and one classified
failure; no new dependency, migration, trusted replacement or financial policy.
**Rollback:** revert the helper check, failure kind/worklist semantics and matching
SPA message together. No data/receipt rewrite; the wrong-source parsing risk
returns. **Lead decision: pending** on integrity/error semantics, byte-reference
meaning, generation/tenant evidence and rollout. Prior head `5b552cb` passed all
functional/supply-chain CI; its image gate remains blocked. New-head CI and human
review remain required.
