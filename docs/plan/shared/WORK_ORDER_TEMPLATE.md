# PART C — WORK-ORDER TEMPLATE

Use this to generate every future work order from the roadmap (`ARCH_plan.md` §4 epics A–J, §9 TODO board, §3 milestones M0–M6). Fill every field; an empty field means the order is not ready to hand over.

<!-- ═══════════════ COPY FROM HERE: WORK-ORDER TEMPLATE ═══════════════ -->

**WORK ORDER <n> — <short title> (board <ids>). Effort <S 1–2d | M 3–5d | L 6–12d | XL 13–25d>. Priority <P0|P1|P2|P3>. Milestone <M0…M6>. Depends on: <WO ids or "nothing">.**

### Objective and business value
<Two paragraphs. First: the defect or gap, stated with the *verified* evidence — file, symbol, line — not a generality. Second: who pays more, churns less, or stops losing money because of it. If you cannot write the second paragraph, the order is not worth doing yet.>

### Scope
**In scope:** <bulleted, concrete, each item mapping to a file or a test>
**Out of scope:** <bulleted — name the adjacent work this order must NOT start, with the board id that owns it. This is the anti-scope-creep clause; it is not optional.>

### Files to touch
| File | Change |
|---|---|
| `<exact path>` | <what changes> |
> Every path must exist (or be explicitly marked **new**). Verify before handing over.

### Implementation guidance
<Numbered steps, in execution order. For a behaviour-preserving refactor, step 1 is ALWAYS "write characterisation tests against the current behaviour and confirm green". For anything touching money, state the rounding and the currency basis. For anything touching a gate, state whether it fails OPEN or CLOSED and why.>

### Invariants this order must preserve
<Name the specific §4 invariants this touches and how each stays true. Never write "all of them".>

### Database / migration impact
<"None." or: the exact columns/tables; the RLS policy for any new tenant table IN THE SAME MIGRATION; the backfill rule; whether the downgrade is safe and what it loses; whether a data migration must print a reconciliation report.>

### Testing requirements
<Named test files and named test functions. Include at minimum:
 - one granted-role and one denied-role authorization case,
 - one cross-tenant case asserting 404 (never 403),
 - one financial-correctness case if money is touched,
 - one concurrency/idempotency case if the write can race,
 - one negative case per adversarial category in §8 that applies.>

### Acceptance criteria (verifiable checklist)
- [ ] <Each item is a thing a reviewer can OBSERVE — a status code, a stored value, a test name, a file that exists. Never "works correctly", never "is robust".>

### Rollback strategy
<Code revert? Migration downgrade — is it written AND tested? What is lost on downgrade? Is any effect one-way (revoked sessions, corrected data)? What is the narrow mitigation that does not require a full revert?>

### Documentation to update
<Exact files. If the change contradicts an ADR, say which and how it is reconciled.>

### Self-verification block
```bash
cd /home/user/Bid_it/backend && . .venv/bin/activate
ruff check app tests && ruff format --check app tests && mypy app
test "$(alembic heads | wc -l)" -eq 1 && alembic upgrade head && alembic check   # if a migration
python -m pytest <the new/changed test files> -q
python -m pytest -q                                                              # full baseline
<a command that DEMONSTRATES the fix — a grep proving the old path is gone, a
 script printing the corrected figure, a parsed XML assertion. Not just "tests pass".>
cd ../frontend && npm run build                                                  # if the SPA changed
```

<!-- ═══════════════ END: WORK-ORDER TEMPLATE ═══════════════ -->

## How to write good acceptance criteria

**Rule:** every criterion must be falsifiable by a person who did not write the code, in under five minutes, without reading the diff.

| Bad | Good |
|---|---|
| "Validation added" | "`POST /vendors` with `iban="DE00000000000000000000"` returns 422 with `code="invalid_iban"` and creates no row" |
| "Permissions enforced" | "`role_client("user_free")` gets 403 on `GET /jobs`; `role_client("admin")` gets 200" |
| "Handles concurrency" | "Two concurrent submissions over an overlapping invoice: exactly one returns 200, the loser returns 409 and its claim status is **unchanged**" |
| "Audit logging works" | "An `AuditEvent` with `action="vendor.update"` exists, its `meta` contains `old`/`new` for each changed field, and no full IBAN appears anywhere in `meta`" |
| "No cross-currency bugs" | "`ap_aging` over EUR+SEK+PLN returns per-currency buckets; `test_fi15_no_aggregate_sums_across_currencies_without_conversion` is green" |
| "Migration is safe" | "`alembic upgrade head && alembic downgrade -1 && alembic upgrade head` is clean; the downgrade refuses while a `pending` change request exists" |

Additional rules:
1. **Quantify the tolerance.** "€0.02 allowed, €0.03 blocked" — never "within tolerance".
2. **Name the actor.** "the requester cannot approve" beats "SoD is enforced".
3. **State the negative.** For every "X is allowed", write the matching "Y is refused, with this code".
4. **Include the unchanged.** Behaviour-preserving refactors need a criterion saying which existing test file passes **unmodified**.
5. **Cap the count.** More than ~12 checkboxes means the order is two orders. Split it.

## How to write good test requirements

1. **Name files and functions.** `tests/test_vendors_authz.py::test_invalid_iban_rejected`, not "add authz tests".
2. **Test through the real path.** HTTP route or service function. A hand-written `select()` in a test proves your test works, not the app.
3. **Overlap the fixtures.** For isolation tests, tenant A and tenant B must carry *identical-looking* data. Distinct data can pass a broken filter by accident.
4. **Every gate gets a both-sides pair** — one input just inside the boundary and one just outside (`SEK 3,999` blocked / `SEK 4,000` allowed; `€399.99` blocked / `€400.00` allowed; `€0.03` blocked / `€0.02` allowed).
5. **Every predicate that must not drift gets a shared-usage test** — inject one bad input and assert *every* consumer of the predicate blocks with the same message set.
6. **A test that cannot fail proves nothing.** Any coverage/parity/scan test ships with a self-test that deliberately seeds a violation and asserts detection.
7. **Concurrency belongs on real Postgres.** SQLite will not reproduce a lost-update race. Put such tests where the `postgres` CI job runs them.
8. **Assert the absence, not only the presence** — no full IBAN in audit meta; no `"9"` emitted by any goods-code mapping; no `Ccy="EUR"` on a foreign amount; zero rows of tenant B.

---
---

