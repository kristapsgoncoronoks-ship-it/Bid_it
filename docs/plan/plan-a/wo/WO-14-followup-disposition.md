# WO-14 follow-up stash — disposition record

**Decision: DISCARD. No code from the stash was applied.**

## What was in the stash

`stash@{0}` ("WO-14 follow-up WIP (uncommitted, agent killed by weekly limit
mid-task)"), last in-progress note "Now wire routes and nav", contained:

- Tracked-file changes to `backend/app/api/router.py`, `app/services/audit.py`,
  `app/services/costing.py`, `app/services/currencies.py`,
  `app/services/tax_codes.py`, `backend/tests/test_currencies.py`,
  `backend/tests/test_tax_codes.py`, `frontend/src/App.tsx`,
  `frontend/src/components/Layout.tsx`, `frontend/src/lib/types.ts`.
- A second stash commit (`stash@{0}^3`) holding the **untracked** new files:
  `backend/app/api/routes/costing.py`, `backend/app/schemas/costing.py`,
  `backend/tests/test_costing_api.py`, `frontend/e2e/masters.spec.ts`,
  `frontend/src/pages/{CostObjects,Currencies,Documents,TaxCodes}.tsx`.

## Investigation

1. Read `docs/plan/plan-a/wo/WO-14-F11.md` in full. It is the **same** work
   order this stash is building: costing-master CRUD routes, §4.16 audit
   coverage on `tax_codes`/`currencies`/`costing`, and the four admin screens
   (tax codes, currencies, cost objects, documents).
2. `git log --oneline` shows `0b0d259 feat(masters): master-data & document
   screens (WO-14 / F1.1)` already **shipped** this exact work order (commit
   dated before this stash existed). `git show 0b0d259 --stat` lists the
   identical file set, at near-identical line counts, as the stash.
3. Direct comparison against `HEAD` (`a29366f`):
   - `git diff stash@{0}^3 HEAD -- <the 8 untracked files>` → **0 lines of
     diff**. The stash's new files are byte-for-byte identical to what
     `0b0d259` already committed and what stands at `HEAD` today.
   - `git diff HEAD stash@{0} -- <the 10 tracked files>` → every line unique
     to the stash side is **older, already-superseded code**: the
     pre-WO-23 hardcoded `_STANDARD` currency tuple in `currencies.py`
     (superseded by the `fx.CURRENCY_BY_CODE` single-registry refactor), and
     the pre-WO-45 flat `Layout.tsx` nav array (superseded by the grouped
     `AppShell`/`LIVE_NAV` shell). Every line unique to the `HEAD` side is
     genuinely later work (WO-23 fx registry, WO-45 shell rework, WO-48
     dogfood billing audit action, WO-49 transport-claim audit action, WO-50
     fuel-ingest audit action, R6 SoD override) that postdates whatever base
     commit the stashing session branched from.

## Conclusion

The stash is not a distinct or further-reaching piece of work: it is an
**independent re-derivation of WO-14/F1.1 from an older point on this same
branch**, produced by a session that did not have commits `1e6c561` (WO-23)
or `WO-45` onward in its tree when it started. Its untracked deliverable
files are identical to what already shipped; its tracked-file edits are a
strict subset of, and in two files (`currencies.py`, `Layout.tsx`) a
**regression relative to**, what is already on `HEAD`.

Applying this stash would provide zero new capability and would silently
revert `currencies.py`'s currency-identity source-of-truth refactor (WO-23)
and `Layout.tsx`'s grouped-nav shell (WO-45) to their pre-refactor shapes —
a correctness regression, not a completion of unfinished scope. There is no
scope in this stash that is not already fully built, tested (`test_costing_api.py`,
audit assertions in `test_currencies.py`/`test_tax_codes.py`, `e2e/masters.spec.ts`),
documented (`docs/architecture/domain-modules.md`,
`docs/security/authorization-policy-matrix.md`) and shipped at `0b0d259`.

This meets the DISCARD bar from the task brief: the WIP duplicates
already-shipped work outright rather than extending unshipped scope, so there
is nothing to safely "finish" without reintroducing a regression.

## Action taken

- This decision record committed first.
- `git stash drop` run immediately after, once this record is committed.
- No application code, tests, or docs from the stash were merged. The tree at
  `HEAD` (`a29366f` plus this record) is unchanged in behavior.
