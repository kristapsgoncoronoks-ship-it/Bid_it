# WO-63 stash — disposition record

**Decision: DISCARD. No code from the stash was applied.**

## What was in the stash

`stash@{0}` ("WO-63 in-progress: E100 parser routes/nav wiring,
pre-weekly-limit-kill") is a merge of three commits:

- `0963dcf` "index on claude/bidit-invoice-data-analytics: `2df37a9`
  docs(plan): WO-14 — master-data & document screens (F1.1)" — the tracked
  changes, parented on `2df37a9`.
- `bd18895` "untracked files on ... : `2df37a9` ..." — the untracked new
  files, same parent.
- `2df37a9` itself, the WO-14 docs commit that immediately precedes the
  shipped `0b0d259 feat(masters): master-data & document screens (WO-14 /
  F1.1)` on this branch's own history.

Tracked-file changes: `backend/app/api/router.py`, `app/services/audit.py`,
`app/services/costing.py`, `app/services/currencies.py`,
`app/services/tax_codes.py`, `backend/tests/test_currencies.py`,
`backend/tests/test_tax_codes.py`, `frontend/src/App.tsx`,
`frontend/src/components/Layout.tsx`, `frontend/src/lib/types.ts`.
Untracked new files: `backend/app/api/routes/costing.py`,
`backend/app/schemas/costing.py`, `backend/tests/test_costing_api.py`,
`frontend/e2e/masters.spec.ts`, `frontend/src/pages/{CostObjects,Currencies,
Documents,TaxCodes}.tsx`.

This is **the identical file set** as the WO-14 follow-up stash resolved in
`docs/plan/plan-a/wo/WO-14-followup-disposition.md` — and, on inspection,
substantively the same stash content, not new WO-63 (E100 fuel-card parser)
work.

## Investigation

1. Read `docs/plan/shared/00_MASTER_CONTEXT.md`, the WO-14 precedent above,
   `docs/plan/plan-a/wo/WO-63-G3.2-slice2.md` (this order's actual scope:
   `app/services/transport/parsers/e100.py`, one registry-list line in
   `fuel_card_parser.py`, `tests/factories/transport.py`, two new test
   modules under `backend/tests/transport/`, and three doc updates), and
   `docs/plan/plan-a/wo/WO-62-G3.2-slice1.md` (the shipped Eurowag parser +
   registry this slice extends).
2. **Identified the stash's true parent commit.** `git log --oneline -1
   stash@{0}^1` / `^2` / `^3` all resolve to `2df37a9 docs(plan): WO-14 —
   master-data & document screens (F1.1)` — a commit that predates `0b0d259`
   (the shipped WO-14/F1.1 feature itself), `1e6c561`/WO-23 (the currency
   registry refactor), `ad0fa14`/WO-45 (the nav-shell rework), and every
   commit through WO-46..WO-62 that followed, i.e. **~100 commits of this
   branch's own subsequent history**, including WO-61 (supplier legal-entity
   registry) and WO-62 (the fuel-card parser registry + Eurowag parser)
   that this order, WO-63, is supposed to extend. The stash was never based
   on any commit at or after WO-62; it cannot contain WO-63 work because the
   registry it would extend did not exist yet in the tree it branched from.
3. **Grepped the entire stash diff for any WO-63-relevant content.**
   `git show stash@{0} | grep -i 'e100\|fuel_card\|transport\|parser'` and
   the same over `stash@{0}^3` (the untracked-files commit) returned **only**
   the stash's own commit-message line, an unrelated expense-category string
   literal (`"travel", "meals", ..., "transport", "supplies", ...`), and a
   docstring comment about invoice line-item "parsers" (deterministic
   extraction parsers, not fuel-card network parsers). **Zero** lines
   mention E100, `fuel_card_parser`, `app.services.transport`, or any of
   WO-63's actual file paths. The stash's commit message describing
   "E100 parser routes/nav wiring" does not match its content — the diff is
   entirely tax-code/currency/costing/document master-data screens, i.e.
   WO-14/F1.1, not WO-63.
4. **Direct comparison against `HEAD` (`58d3491`), file by file**, correcting
   for the two-parent stash structure (tracked changes live on `stash@{0}`
   itself; untracked new files live on `stash@{0}^3`, since a plain
   `git diff stash@{0} HEAD` against an untracked-only path incorrectly shows
   the whole file as "added"):
   - **Untracked files** — `git diff stash@{0}^3 HEAD -- <8 untracked
     paths>` → **0 lines of diff**. Byte-for-byte identical to what `0b0d259`
     already shipped and what stands at `HEAD` today (all 8 files exist at
     `HEAD`, confirmed via `git cat-file -e HEAD:<path>` before diffing).
   - **Tracked files** — `git diff stash@{0} HEAD -- <10 tracked paths>` →
     605 lines, all of it `HEAD` carrying **more** than the stash, never
     less:
     - `router.py`: stash lacks the later `dashboard` router registration —
       additive-only difference, nothing stash-side missing from `HEAD`.
     - `audit.py`: stash's `TAX_CODE_*`/`CURRENCY_*`/`MASTER_*` audit actions
       are present verbatim at `HEAD`; `HEAD` additionally carries every
       audit action added by WO-48 through WO-61 (`PLATFORM_SUBSCRIPTION_INVOICE`,
       `TRANSPORT_CLAIM_CREATE` through `TRANSPORT_SUPPLIER_REGISTRATION_SET`).
     - `currencies.py`: the stash still has the **pre-WO-23** hardcoded
       `_STANDARD` tuple of `(code, name, symbol, decimal_places)` — the exact
       same regression the WO-14 precedent flagged, now confirmed unchanged
       in this stash too. `HEAD` has the WO-23 `_STANDARD_CODES` + single
       `fx.CURRENCY_BY_CODE` registry lookup.
     - `Layout.tsx`: the stash still has the **pre-WO-45** flat `NAV` array
       (~28 items, `NavLink`-based). `HEAD` has the grouped `AppShell`/
       `LIVE_NAV` shell WO-45 introduced. Applying the stash would revert
       this shell rework.
     - `types.ts`: the stash's `UserRoleName`/`Usage` shapes are the
       **pre-A1.5/pre-WO-47** four-role, role-keyed usage shape. `HEAD` has
       the 8-role vocabulary and the plan-keyed (`PlanKey`/`PlanPolicy`)
       usage model. Applying the stash would revert this too.
5. **Conclusion of the file-by-file pass**: every one of the 18 files in
   this stash is either (a) byte-identical to `HEAD` (all 8 untracked files,
   `router.py`'s shared lines, `audit.py`'s shared lines) or (b) a strict,
   already-superseded predecessor of the `HEAD` version (`currencies.py`,
   `Layout.tsx`, `types.ts`). **None** contain category (c) — genuine new
   WO-63-related content (E100 parser routes, transport nav entry, fuel-card
   types) — because no such content exists anywhere in the stash.

## Why the task brief's "1503 lines, non-zero" observation doesn't change the verdict

An unscoped `git diff stash@{0} HEAD` (mixing the tracked-only `stash@{0}`
ref with paths that only exist on `stash@{0}^3`) reports the 8 untracked
files as wholesale "added" and, separately, ~100 commits of unrelated branch
history as different — because the stash's true base (`2df37a9`) sits **~100
commits behind** `HEAD`, not because the stash contains ~100 commits of
genuine additional work. Restricting the diff to the exact 18-file WO-14
stash file set and comparing each half against its correct stash parent
(`stash@{0}` for tracked, `stash@{0}^3` for untracked, per point 4 above)
produces the true picture: 0 lines of stash-unique content in the untracked
half, and 605 lines in the tracked half that are 100% already-superseded
predecessor code, not new work. There is no reading of this stash, scoped
correctly, that yields genuine unshipped WO-63 content.

## Conclusion

This stash is not WO-63 work despite its commit message. It is a
re-derivation of WO-14/F1.1, produced (or re-surfaced) from a branch point
that predates WO-62 — the very slice-1 registry WO-63 extends — so it
structurally cannot contain E100/fuel-card content, and inspection confirms
it contains none. Every file in it is either already shipped byte-for-byte
or superseded by later refactors (WO-23's currency registry, WO-45's nav
shell, A1.5's role vocabulary, WO-47's plan-keyed usage). Applying any part
of it would provide zero new capability toward WO-63 and would regress
`currencies.py`, `Layout.tsx`, and `types.ts` to their pre-refactor shapes.

This meets the DISCARD bar from `docs/plan/plan-a/wo/WO-14-followup-
disposition.md`'s own template, on stronger grounds than that precedent: WO-14's
stash was at least a full independent re-derivation of its own named work
order; this stash's message names a different work order (WO-63/E100) than
its actual content (WO-14/F1.1) entirely.

## Action taken

- This decision record committed first, on top of `58d3491`.
- `git stash drop` run immediately after this record is committed.
- No application code, tests, or docs from the stash were merged. `HEAD` is
  unchanged in behavior by this record; WO-63 is implemented from scratch in
  a following commit.
