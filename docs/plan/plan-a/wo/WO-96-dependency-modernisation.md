# WO-96 — dependency modernisation: every backend and frontend dependency to latest

**Effort M (3–5d). Priority P1. Milestone M5 (platform hygiene, out-of-band).
Depends on: nothing in code. Converges with the eight Dependabot PRs merged to
`main` on 2026-08-08 (TODO.md "Dependency & CI health").**

---

## RECON — verified before any bump

Every version below was read from the installed tree
(`pip list --format=freeze`, `npm ls --depth=0`) and from the registries
(`pypi.org/pypi/<pkg>/json`, `npm view <pkg> version`) on 2026-08-11.
The **branch** column is what `6a3a43b` pins; the **`main`** column is what the
Dependabot merges already landed there, so this order converges rather than
conflicts.

### Backend — `backend/requirements.txt`

| Package | Branch | `main` | Latest | Class |
|---|---|---|---|---|
| fastapi | 0.139.2 | 0.141.1 | **0.141.1** | minor |
| uvicorn[standard] | 0.51.0 | 0.52.0 | **0.52.1** | minor |
| sqlalchemy[asyncio] | 2.0.51 | 2.0.51 | 2.0.51 | current |
| asyncpg | 0.31.0 | 0.31.0 | 0.31.0 | current |
| aiosqlite | 0.22.1 | 0.22.1 | 0.22.1 | current |
| alembic | 1.18.5 | 1.18.5 | **1.19.1** | minor |
| pydantic | 2.13.4 | 2.13.4 | 2.13.4 | current |
| pydantic-settings | 2.14.2 | 2.14.2 | **2.15.0** | minor |
| python-jose[cryptography] | 3.5.0 | 3.5.0 | 3.5.0 | current |
| bcrypt | 5.0.0 | 5.0.0 | 5.0.0 | current |
| python-multipart | 0.0.32 | 0.0.32 | 0.0.32 | current |
| email-validator | 2.3.0 | 2.3.0 | 2.3.0 | current |
| boto3 | 1.43.55 | 1.43.62 | **1.43.68** | patch |
| pdfplumber | 0.11.10 | 0.11.10 | 0.11.10 | current |
| **pypdf** | **5.1.0** | 6.14.2 | **6.15.0** | **MAJOR** |
| pypdfium2 | 5.12.1 | 5.12.1 | 5.12.1 | current |
| pytesseract | 0.3.13 | 0.3.13 | 0.3.13 | current |
| pillow | 12.3.0 | 12.3.0 | 12.3.0 | current |
| **reportlab** | **4.2.5** | 5.0.0 | **5.0.0** | **MAJOR** |
| defusedxml | 0.7.1 | 0.7.1 | 0.7.1 | current |
| openpyxl | 3.1.5 | 3.1.5 | 3.1.5 | current |
| pytest | 9.1.1 | 9.1.1 | 9.1.1 | current |
| pytest-asyncio | 1.4.0 | 1.4.0 | 1.4.0 | current |
| httpx | 0.28.1 | 0.28.1 | 0.28.1 | current |
| prometheus-client | 0.25.0 | 0.26.0 | **0.26.0** | minor |

Commented-out optionals (`stripe==11.4.1`, `clamd==1.0.2`) are **not installed
and not importable at rest** — `stripe` is imported lazily only when a Stripe
secret key is set, `clamd` only when `clamav_enabled=true`. Bumping a comment
would move a pin nothing verifies. `stripe` is left at its recorded 11.4.1 with
that reason; see "Deferred" below.

### Backend tooling — `backend/requirements-dev.txt`

| Package | Branch | Latest | Class |
|---|---|---|---|
| ruff | 0.16.0 | **0.16.2** | patch |
| mypy | 2.3.0 | 2.3.0 | current |
| pre-commit | 4.6.1 | **4.6.2** | patch |

> Recon finding: the venv had **ruff 0.15.22** installed against a `0.16.0` pin —
> `requirements-dev.txt` was never installed into it. Re-pinned as step 0.

### Frontend — `frontend/package.json`

| Package | Branch (resolved) | `main` | Latest | Class |
|---|---|---|---|---|
| @tanstack/react-query | 5.101.4 | ^5.101.4 | 5.101.4 | current |
| axios | 1.18.1 (`^1.7.9`) | ^1.19.0 | **1.19.0** | minor |
| **react** | **18.3.1** | ^18.3.1 | **19.2.8** | **MAJOR** |
| **react-dom** | **18.3.1** | ^18.3.1 | **19.2.8** | **MAJOR** |
| react-router-dom | 7.18.1 | ^7.18.2 | **7.18.2** | patch |
| recharts | 3.10.0 | ^3.10.1 | **3.10.1** | patch |
| @playwright/test | 1.61.1 | ^1.62.1 | **1.62.1** | minor |
| @tailwindcss/postcss | 4.3.3 | ^4.3.3 | 4.3.3 | current |
| **@types/react** | **18.3.31** | ^18.3.17 | **19.2.18** | **MAJOR** |
| **@types/react-dom** | **18.3.7** | ^18.3.5 | **19.2.4** | **MAJOR** |
| **@vitejs/plugin-react** | **4.7.0** | ^6.0.5 | **6.0.5** | **MAJOR** |
| postcss | 8.5.22 | ^8.5.25 | **8.5.26** | patch |
| tailwindcss | 4.3.3 | ^4.3.3 | 4.3.3 | current |
| typescript | 7.0.2 | ^7.0.2 | 7.0.2 | current |
| **vite** | **6.4.3** | ^8.1.5 | **8.2.1** | **MAJOR** |

### Matched pairs — identified BEFORE any bump

1. **`vite` ↔ `@vitejs/plugin-react`** — plugin-react 4's peer range stops at
   vite 7. Vite 8 alone makes `npm ci` fail repo-wide. This is exactly the
   failure `main` took on 2026-08-08. **Move together, one commit.**
2. **`react` ↔ `react-dom` ↔ `@types/react` ↔ `@types/react-dom`** — React 19's
   runtime and its type packages are one unit; react-dom 19 against react 18 is
   an immediate runtime error. **Move all four together, one commit.**
3. **`@playwright/test` ↔ `mcr.microsoft.com/playwright:v<X>-jammy` in
   `.github/workflows/ci.yml`** — the image ships the browser build the library
   expects. A skew fails in the most misleading way available (a few passes then
   "did not run").

   > **RECON FINDING — this pair is ALREADY SKEWED on this branch.** `ci.yml`
   > line 153 pins `v1.62.1-jammy` while `package.json` pins `^1.61.1`, which
   > resolves to 1.61.1. The guard step added at `3c651f1` reproduces locally as
   > `lib=1.61.1 img=1.62.1 → GUARD_FAIL`. `main` carries `^1.62.1`; only the
   > `ci.yml` half of that fix reached this branch. Stage A closes it by moving
   > the library to 1.62.1, matching the image already pinned.

4. **`reportlab` ↔ the PDF byte-level tests**; **`pypdf` ↔ e-invoice parsing,
   PDF extraction and the OCR fallback**. Not a version pair, but a
   library↔canary pair: these tests parse generated documents and assert
   content, and a rendering change there reaches a tax authority or a supplier.

### Known breaking changes already handled — do not regress

- `frontend/vite.config.ts` uses the **FUNCTION form** of `manualChunks`.
  Vite 8 bundles with rolldown, which rejects the object form
  (`manualChunks is not a function`). The function form builds on rollup
  (vite 6/7) **and** rolldown, so it survives the upgrade in both directions.
  Chunk boundaries (`vendor`, `recharts`) must remain unchanged after the bump.

---

## Objective and business value

The branch pins two backend majors and three frontend majors behind `main` and
behind the registry, and carries one **live matched-pair skew** (Playwright
library vs CI container image) that would fail the frontend-e2e job the moment
runners return. Two of the stale pins — `reportlab` and `pypdf` — sit directly
under documents that leave the building: the VAT claim pack, the supplier
overcharge demand letter, the issued sales invoice, and the inbound e-invoice
parser. Staying behind on those is not neutral; it accumulates a rendering and
parsing delta that nobody has measured.

Nobody pays more because a dependency is current. They stop paying when a
security advisory lands on a pin the product cannot move because five majors
have piled up behind it, or when the branch that has never merged diverges
further from `main`. This order removes that debt while the suite is green and
the tree is calm, converging on the versions `main` already carries so the
long-overdue merge is a smaller event than it currently is.

## Scope

**In scope:**
- `backend/requirements.txt` — every runtime pin to latest.
- `backend/requirements-dev.txt` — ruff, pre-commit to latest.
- `frontend/package.json` + `package-lock.json` — every dependency to latest.
- `.github/workflows/ci.yml` — the Playwright container image tag, kept matched
  to the library, and the guard step kept correct.
- Any application code a major genuinely breaks (React 19 across the SPA pages,
  reportlab 5 renderers, pypdf 6 parsing call sites).
- `README.md` where a bump changes a docs-truth-pinned claim ("React 18").
- `TODO.md` (the WO row, the suite line, the dependency section) and
  `docs/RELEASE-READINESS.md` (its verified table and §3) — **last**.

**Out of scope:**
- Adding, removing or replacing any dependency. This order moves versions only.
- Enabling `stripe` or `clamd` (both commented, both optional, both lazily
  imported — owned by ADR-0013 and the filesec backlog respectively).
- Any behaviour change not forced by a bump. If a new library version offers a
  better API, that is a proposal, not this order.
- Node or Python base-image versions in the Dockerfiles (`node:22-alpine`,
  `python:3.14-slim`) — infrastructure pins, owned by `docs/DEPLOYMENT.md`.
- The `manualChunks` chunking strategy itself — it is already correct for both
  bundlers and must be left alone.

## Files to touch

| File | Change |
|---|---|
| `backend/requirements.txt` | version pins only |
| `backend/requirements-dev.txt` | ruff, pre-commit pins |
| `frontend/package.json` | version ranges |
| `frontend/package-lock.json` | regenerated by `npm install` |
| `.github/workflows/ci.yml` | Playwright image tag, kept matched |
| `README.md` | "React 18" → "React 19" in the same commit as the bump |
| `TODO.md` | WO-96 row, suite line, dependency section |
| `docs/RELEASE-READINESS.md` | verified table + §3 |
| `frontend/src/**`, `backend/app/**` | **only if** a major forces it |

## Implementation guidance

Staged, never one sweep. Each stage is its own commit with its own full
verification, pushed before the next begins.

0. **Baseline.** `python -m pytest -q` at `6a3a43b` and record the exact count.
   Re-pin the venv with **both** requirements files (the recon found ruff drifted).
1. **Stage A — patches and minors, backend and frontend, one commit.**
   Backend: fastapi, uvicorn, alembic, pydantic-settings, boto3,
   prometheus-client, ruff, pre-commit. Frontend: axios, react-router-dom,
   recharts, postcss, **@playwright/test → 1.62.1 (closes the live skew)**.
   Verify: ruff, mypy, alembic heads/check, full pytest, `npm run build`,
   `npm run test:e2e`, the Playwright guard reproduced locally.
2. **Stage B — vite 8 + @vitejs/plugin-react 6** (matched pair, one commit).
   Confirm `manualChunks` stays the function form and the emitted chunk names are
   unchanged. Frontend-only: build + full e2e; no backend run needed.
3. **Stage C — reportlab 5.** Run the PDF canaries FIRST for a fast signal, then
   the full suite. Then **parse a generated PDF and compare its extracted text
   against the same document rendered under 4.2.5** — a green suite is not
   sufficient evidence for a renderer major.
4. **Stage D — pypdf 6.** Same shape: e-invoice/extraction/OCR canaries first,
   then the full suite, then a parsed-bytes comparison.
5. **Stage E — react 19 + react-dom 19 + both @types** (matched pair, one
   commit). `tsc --noEmit` will surface the type-level breaks; the e2e suite is
   the behavioural net. `README.md` updates in this same commit.
6. If a stage cannot be made green with reasonable effort: **stop on that one**,
   restore its pin, record the reason in this order and in `TODO.md`, and
   continue with the rest. A documented deferral is a good outcome.

**Never weaken, skip or delete a test to make a bump pass.** If a test fails
after a bump, either the bump broke behaviour (fix the code) or the test encoded
an implementation detail of the old version (fix the test, and say so explicitly
with the reasoning). Every such case is reported.

**Watch for behaviour changes that pass the tests.** A bumped library can change
PDF layout, decimal formatting or JSON serialisation without failing an
assertion. Where a major touches money formatting or document rendering, verify
by parsing a sample, not by trusting green.

## Invariants this order must preserve

- **§4.9 (Decimal, ROUND_HALF_UP, never float)** — `reportlab` and `openpyxl`
  render money that `app/core/money.py` has already quantized to a string. A
  renderer major must not reformat it. Proven by the byte-level tests that assert
  exact cell and text content, plus an explicit parsed-sample comparison.
- **§4.14 / §4.15 (no cross-currency sums; one FX convention)** — untouched by
  any version change; the full suite's FX tests are the net.
- **§4.16 (every mutating operation audited, hash-chained)** — a SQLAlchemy or
  FastAPI minor must not alter session flush ordering such that an audit row
  lands outside its operation's transaction. Proven by the audit chain tests.
- **§4.20 (wire contract frozen: `{"detail", "code"}` + `X-Request-ID`)** — the
  FastAPI and pydantic bumps are the live risk here: a serialization change would
  alter the shape the SPA and the suite both depend on. Proven by the route tests
  and by the full e2e suite driving the real SPA against the real API.
- **Layering (`models → core → services → api`)** — `tests/test_boundaries.py`
  is version-independent and stays green.

## Database / migration impact

**None.** No schema change. `alembic` moves 1.18.5 → 1.19.1, so
`alembic heads` (single head `d4c7b1e93f27`) and `alembic check` (no drift) are
re-run under the new version as part of Stage A's verification — a migration
tool major/minor can change autogenerate comparison behaviour, and `alembic
check` is the assertion that it did not.

## Testing requirements

No new tests. This order's evidence is the **existing** suites run in full under
each new pin, plus per-major parsed-sample verification:

- `python -m pytest -q` — full backend suite, every stage that touches backend.
  Baseline 2403 passed / 10 skipped; any delta explained line by line.
- `npm run test:e2e` — the CI list, every stage that touches frontend.
  Baseline 270 passed.
- PDF canaries (Stage C): `tests/test_invoice_pdf.py`,
  `tests/transport/test_g2_12_claim_pack.py`,
  `tests/transport/test_wo83_overcharge_artifacts.py`,
  `tests/transport/test_wo91_excise_packet.py`, `tests/test_report_writers.py`.
- pypdf canaries (Stage D): `tests/test_einvoice.py`, `tests/test_pdf_ocr.py`,
  `tests/test_pdf_transactions.py`, `tests/test_receipt_ocr.py`,
  `tests/test_expenses.py`, `tests/test_email_intake.py`.
- Postgres gates on a scratch cluster with a NOSUPERUSER role:
  `tests/test_rls.py tests/test_numbering_concurrency.py
  tests/test_transport_lock_concurrency.py`.
- The Playwright pair guard reproduced locally from `ci.yml`.

## Acceptance criteria (verifiable checklist)

- [ ] `pip list --format=freeze` shows every `requirements.txt` pin at the
      version recon named as latest, or the pin is unchanged with a recorded
      deferral reason in this file.
- [ ] `npm ls --depth=0` shows every `package.json` dependency at latest, or
      unchanged with a recorded reason.
- [ ] `lib="$(npx playwright --version)"` equals the tag in `ci.yml` — the guard
      step prints `Playwright <v> matches the container image.`
- [ ] `grep -n "manualChunks(id: string)" frontend/vite.config.ts` still matches
      (the function form was not regressed).
- [ ] `npm run build` emits `vendor-*.js` and `recharts-*.js` chunks, same as
      before the vite bump.
- [ ] `python -m pytest -q` reports **2403 passed, 10 skipped** — or a delta with
      a per-test explanation and **zero** assertions weakened, zero skips added.
- [ ] `npm run test:e2e` reports **270 passed**.
- [ ] `ruff check app tests`, `ruff format --check app tests`, `mypy app` clean.
- [ ] `alembic heads | wc -l` is 1 and `alembic check` is clean on Postgres 16.
- [ ] Postgres gates pass on a NOSUPERUSER role.
- [ ] `python scripts/pii_scan.py --tree` clean.
- [ ] `README.md` says React 19 in the same commit that ships react 19.
- [ ] Each major has its own commit; no commit mixes two majors.

## Rollback strategy

Pure code revert, per stage. Each major is its own commit, so a regression
discovered later reverts exactly one library without touching the others —
this is the entire reason the order is staged rather than swept. No migration,
so no downgrade path is needed and nothing is one-way. `package-lock.json`
regenerates deterministically from a reverted `package.json` via `npm install`;
`pip install -r requirements.txt` restores a reverted backend pin.

The narrow mitigation short of a revert: pin the single offending package back
one version while leaving the rest of the stage in place — safe for every
package in this order **except** a matched pair, where both halves must move
back together.

## Documentation to update

- `README.md` — the "React 18 + Vite + TypeScript + Tailwind" stack line, in the
  same commit as the React major.
- `TODO.md` — the WO-96 row, the suite line, and the "Dependency & CI health"
  section, whose two open checkboxes (react/react-dom unmerged; the Playwright
  skew) this order resolves on this branch.
- `docs/RELEASE-READINESS.md` — §2's verified table (re-run under the new pins)
  and §3/§5, where owner action 5 ("land react/react-dom together, or neither")
  is answered by this branch.
- This file — the per-stage results, every behaviour change, every deferral.

No ADR is contradicted: no ADR names a dependency version.

## Self-verification block

```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt -q
ruff check app tests && ruff format --check app tests && mypy app
test "$(alembic heads | wc -l)" -eq 1 && alembic check
python -m pytest -q                                    # full baseline

# Postgres gates on a scratch NOSUPERUSER cluster
export RLS_TEST_DATABASE_URL=postgresql+asyncpg://appuser:apppw@localhost:5433/invoiceiq
python -m pytest tests/test_rls.py tests/test_numbering_concurrency.py \
                 tests/test_transport_lock_concurrency.py -q

cd ../frontend && npm ci && npm run build && npm run test:e2e

# DEMONSTRATE, not just "tests pass":
#  1. the matched pair actually agrees
lib="$(npx playwright --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
img="$(grep -oE 'playwright:v[0-9]+\.[0-9]+\.[0-9]+' ../.github/workflows/ci.yml \
       | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
[ "$lib" = "$img" ] && echo "matched pair OK: $lib"
#  2. the chunk boundaries survived the bundler swap
ls dist/assets | grep -E '^(vendor|recharts)-'
#  3. a generated PDF still renders the same money text under the new reportlab
cd ../backend && python - <<'PY'
# renders a claim letter / invoice and prints the extracted text for diffing
PY
cd .. && python scripts/pii_scan.py --tree
```

---

## RESULTS

### Baseline at `6a3a43b`

```
2403 passed, 10 skipped, 2 warnings in 2037.98s (0:33:57)
```

`npm run test:e2e` → `270 passed (2.4m)`. Both match the figures
`docs/RELEASE-READINESS.md` §2 records, re-derived here rather than quoted.

Golden samples of the rendered documents were captured **before** any bump
(`report_writers.to_pdf` over a money-edge-case cut, `to_xlsx` over the same,
and a `PdfWriter.add_attachment` → `PdfReader.attachments` factur-x round-trip),
because a green suite is not sufficient evidence for a renderer major. The
capture prints parsed text, page geometry and attachment bytes; only reportlab's
`Generated at` stamp is non-deterministic and is normalised when diffing. The
methodology was validated by re-running it under Stage A, where reportlab and
pypdf are unchanged: the diff came back empty.

### Stage A — patches and minors (commit `<this>`)

Backend: fastapi 0.139.2→0.141.1, uvicorn 0.51.0→0.52.1, alembic 1.18.5→1.19.1,
pydantic-settings 2.14.2→2.15.0, boto3 1.43.55→1.43.68,
prometheus-client 0.25.0→0.26.0, ruff 0.16.0→0.16.2, pre-commit 4.6.1→4.6.2.
Frontend: axios 1.18.1→1.19.0, react-router-dom 7.18.1→7.18.2,
recharts 3.10.0→3.10.1, postcss 8.5.22→8.5.26, **@playwright/test 1.61.1→1.62.1**.

```
ruff 0.16.2 → All checks passed! · 563 files already formatted
mypy app    → Success: no issues found in 328 source files
alembic heads → d4c7b1e93f27 (head)   count=1
alembic upgrade head → applied to Postgres 16.13 clean
alembic check → No new upgrade operations detected.
pytest tests/test_rls.py tests/test_numbering_concurrency.py \
       tests/test_transport_lock_concurrency.py -q → 6 passed
python -m pytest -q → 2403 passed, 10 skipped, 2 warnings in 1979.76s (0:32:59)
npm run build → ✓ built in 5.65s (vendor-*.js 273.49 kB, recharts-*.js 418.11 kB)
npm run test:e2e → 270 passed (2.4m)
lib=1.62.1 img=1.62.1 → GUARD PASS
```

**The Playwright pair skew is closed.** Before: `lib=1.61.1 img=1.62.1 →
GUARD_FAIL`. `main` carried `^1.62.1`; only the `ci.yml` half of the `3c651f1`
fix had reached this branch, so the guard added to catch exactly this drift was
itself red here. The library moved to the image, not the image to the library —
1.62.1 is also latest, so the pair converges on `main` rather than away from it.

**Security, unlooked for:** `npm ci` at `6a3a43b` reported *"4 vulnerabilities
(1 moderate, 3 high)"*. After Stage A: *"found 0 vulnerabilities"* — carried by
the axios and postcss bumps.

**Behaviour changes:** none observed. FastAPI 0.140/0.141 are a dependency
memory optimisation, an `app.frontend()` dev-server feature this app does not
use, and a bug fix inside it — no change to error serialization, `response_model`
handling or OpenAPI generation, which is what §4.20's frozen wire contract would
have felt. `openapi.json` is generated on demand and not checked in, so there is
no schema artifact to drift. The golden document samples are byte-identical
after normalisation.

**Baseline delta: 0.** 2403 → 2403 passed, 10 → 10 skipped, 270 → 270 e2e.
Zero assertions weakened, zero tests skipped, zero fixtures touched.

### Stage B — vite 6.4.3 → 8.2.1 + @vitejs/plugin-react 4.7.0 → 6.0.5 (MAJOR pair)

Moved together, as the pair analysis required. Frontend-only: no backend run.

```
npm ls --depth=0 → @vitejs/plugin-react@6.0.5 · vite@8.2.1
npm run build    → ✓ built in 695ms   (tsc --noEmit clean)
npm run test:e2e → 270 passed (1.9m)
grep -n "manualChunks(id: string)" vite.config.ts → 28:  (function form NOT regressed)
ls dist/assets | grep -E '^(vendor|recharts)-' → both chunks still emit
```

`vite.config.ts` is **byte-identical** — `git diff --stat vite.config.ts` empty.
The function form of `manualChunks` did its job: the same config built on rollup
under vite 6 and on rolldown under vite 8.

#### BEHAVIOUR CHANGE — a first-load regression that passes every test

The chunk *names* survive, so nothing in the suite notices. What moved is which
modules land in them. Measured by building both versions and reading the
**sourcemap `sources` lists**, not by grepping minified output:

| | vite 6 (rollup) | vite 8 (rolldown) |
|---|---|---|
| `react.production.min.js`, `react-dom.production.min.js`, `scheduler` | **`vendor`** | **`recharts`** |
| `vendor` chunk | 273.49 kB | 77.18 kB |
| `recharts` chunk | 418.11 kB | 544.26 kB |
| `index.html` modulepreloads | `index`, `vendor` | `index`, `vendor`, **`recharts`**, `rolldown-runtime`, `chunk-62JRHF6Z` |
| total JS emitted | 1,304,910 B / 96 files | 1,290,897 B / 79 files |

Because the React runtime now lives inside the `recharts` chunk, and React is
needed on every page, **the 544 kB chart bundle is now modulepreloaded on every
page load — including pages with no chart.** Under vite 6 it was fetched only by
the pages that render charts. Critical-path JS goes from roughly
`index 55.93 + vendor 273.49 ≈ 329 kB` to
`index 61.64 + vendor 77.18 + recharts 544.26 + chunk 39.33 + runtime ≈ 722 kB`.

Total emitted bytes actually fell 14,013 B (−1.1%) and the file count fell from
96 to 79, so an aggregate size check would have called this an improvement. It
is not one: the aggregate got smaller while the *first paint* roughly doubled.

**Cause: rolldown, not our config.** Tested directly — reordering the
`manualChunks` rules so the `VENDOR_CHUNK` match wins before the `recharts`
match produced a byte-identical result (`vendor` 77.22 kB, `recharts` 544.30 kB,
same preload set). Rolldown treats `manualChunks` as a hint and reassigns the
shared runtime itself. The experiment was reverted; `vite.config.ts` is unchanged.

**Not fixed here, deliberately.** The only levers that would move it are
rolldown-specific (`build.rolldownOptions.output.codeSplitting`, which the build
warning itself suggests, or `advancedChunks`). Adopting one would destroy the
property this repo deliberately bought at `9540ab3` — *one config shape that
builds on rollup AND rolldown* — and would be inventing a chunking strategy
under cover of a version bump. Correctness is unaffected: 270 e2e green, build
clean, every chunk the config asks for still emitted. **Recorded as a follow-up**
(see "Left undone"), with the measurement above so whoever picks it up starts
from evidence rather than from a rebuild.

**Baseline delta: 0.** 270 → 270 e2e. No backend surface touched.

### Stage C — reportlab 4.2.5 → 5.0.0 (MAJOR)

The renderer behind the VAT claim pack, the supplier overcharge demand letter,
the issued sales invoice and the expense report.

```
pytest <11 PDF canary modules> -q → 128 passed in 118.38s
ruff check / ruff format --check  → clean, 563 files
mypy app                          → Success: no issues found in 328 source files
python -m pytest -q               → 2403 passed, 10 skipped in 1893.43s (0:31:33)
postgres gates                    → 6 passed (NOSUPERUSER, fresh database)
```

**Parsed-sample verification — the green suite was not treated as sufficient.**
The golden capture was re-run under 5.0.0 and diffed against the 4.2.5 output
with only reportlab's `Generated at` stamp and the version banner normalised:

```
GOLDEN IDENTICAL — no rendering or money-formatting change
```

That covers page geometry (`595.28x841.89`), the full extracted text in order,
and every money edge case rendered exactly as the server quantized it —
`1234567.89`, `0.01`, `-100.00`, `0.00`, `2.005`. **§4.9 holds:** the renderer
reproduces the Decimal-derived strings and does not reformat them.

**The one real breaking change in 5.0.0, assessed and closed.** Its changelog
carries exactly one behavioural entry: *"make `trustedHosts` None mean no hosts
are trusted in `open_for_read`"* — a security inversion, where `None` previously
meant *trust everything*. It can only bite a renderer that fetches a resource by
URL. Ours never does: the only image path is
`app/services/invoice_pdf.py:148`, `Image(io.BytesIO(logo[1]), …)`, whose logo
arrives as `tuple[str, bytes]` already in memory. `grep` for
`trustedHosts|open_for_read|https?://` across all four renderer modules returns
nothing. So the change is inert here — and it is an improvement in posture, not
a risk.

**Baseline delta: 0.** 2403 → 2403 passed, 10 → 10 skipped.

> **Environment note, not a defect in this order.** The first Postgres-gate run
> failed `test_rls_users_visibility_is_membership_driven` with a duplicate
> `ix_users_email` on `switched@x.io` — a row left by the Stage A run in the
> scratch cluster I was *reusing*. CI provisions a fresh `postgres:16-alpine`
> service per job, so it never sees this. Fixed by dropping and recreating the
> database to match CI, after which all 6 pass. **No test was touched.** Worth
> knowing that this gate assumes a virgin database.

### Stage E — React 18.3.1 → 19.2.8 (MAJOR quartet)

`react`, `react-dom`, `@types/react` 18.3.31→19.2.18 and `@types/react-dom`
18.3.7→19.2.4 moved as one commit. Owner action 5 in
`docs/RELEASE-READINESS.md` — *"land react/react-dom together, or neither"* — is
now answered on this branch.

```
npx tsc --noEmit → TSC_EXIT=0        (clean on the first attempt, 55 pages)
npm run build    → ✓ built in 1.01s
npm run test:e2e → 270 passed (2.8m)
```

**No application code needed changing.** That was not luck; it was checked before
bumping. The SPA was already clear of every removed React 19 API:
`src/main.tsx` already uses `ReactDOM.createRoot` (not the removed
`ReactDOM.render`); no `propTypes` or `defaultProps` on any function component;
no string refs and no `findDOMNode`; every `useRef` call already passes an
argument (React 19 made it required); no `JSX.*` namespace references (the
namespace moved); no `React.FC`. The single `forwardRef` (`components/ui/Button.tsx`)
is deprecated in 19 but still supported, so it is left alone — rewriting it would
be scope creep, not a forced change.

**No React deprecation warnings at runtime.** The e2e output carries 100
warning-shaped lines; all 100 are `[WebServer] … vite http proxy error … connect
ECONNREFUSED …:8000`, which is expected — the browser suite runs against the
`/design` fixture showcase with no backend behind the dev proxy — and the same
lines appear in the Stage A run under React 18. Filtering for React/hydration/
deprecation vocabulary returns nothing.

#### The Stage B regression: rebalanced, NOT resolved

React 19 changes the chunk split again, because its build layout differs from
18's. Measured the same way (sourcemap `sources`):

| Critical-path JS (`index.html` preloads) | bytes |
|---|---|
| vite 6 + React 18 — `index` + `vendor` | **~329 kB** |
| vite 8 + React 18 — `index` + `runtime` + `recharts` + `vendor` + `chunk` | ~723 kB |
| vite 8 + React 19 — same five | **772,780 B** |

Under React 19 the *sizes* return close to the original split (`vendor`
77.18→255.31 kB, `recharts` 544.26→415.77 kB, both near their vite 6 values),
but **the preload set is unchanged: `recharts` is still fetched on every page**,
because `react.production.js` / `react-dom.production.js` still land in that
chunk while `react-dom-client.production.js` and `scheduler` land in `vendor`.
So the critical path stays roughly 2.3× the vite 6 baseline — very slightly
worse than Stage B, not better.

**This is a vite 8 / rolldown property, and React 19 neither causes nor cures
it.** It stays recorded as the Stage B follow-up. Stated here so nobody reads
the restored chunk sizes as the regression having gone away.

**Baseline delta: 0.** 270 → 270 e2e.
