## Objective

What problem does this change solve? Why is this change required now?

## Scope

What changes? What deliberately does not change?

## Risk / impact

Mark each relevant item and explain below. Do not tick a box merely to satisfy the template.

- [ ] Authentication / authorization / tenant isolation
- [ ] Financial calculation, payment, tax, billing, or reconciliation
- [ ] Personal data / retention / deletion / audit evidence
- [ ] Database schema / migration / data rewrite
- [ ] Public API / webhook / integration contract
- [ ] Background jobs / concurrency / idempotency
- [ ] GitHub Actions / deployment / secrets / infrastructure
- [ ] Dependency added or materially changed
- [ ] No material security/data/financial impact

**Impact explanation:**

## Evidence

What code, measurements, standards, issue reports, or repository evidence justify the change?

## Tests actually executed

List the exact commands/tests that were run and their results.
Do not write "all tests pass" unless they were actually executed.

## Failure / rollback

How can this fail? How do we recover or revert safely?

For migrations, include preflight/refusal behavior and whether downgrade is actually safe.

## External/network/secrets review

Does this add or change:

- outbound hosts/URLs;
- credentials/tokens;
- GitHub Actions permissions;
- file parsing/upload behavior;
- logging of sensitive data?

If yes, explain the threat model and controls.

## AI assistance disclosure

- [ ] No AI assistance was used in this change.
- [ ] AI assistance was used.

If AI was used, describe what it produced or changed and how the output was independently reviewed/tested.

AI-generated code, migrations, tests, security conclusions, and documentation have the same evidence and review requirements as human-authored work.

## Owner decisions / unresolved items

List any business/legal/security policy decision that cannot be inferred from code.
If none: `None`.

## Reviewer acceptance

- [ ] Root cause / objective is clear.
- [ ] No weaker or simpler solution was overlooked.
- [ ] Authorization / tenant boundaries are preserved.
- [ ] Financial/data invariants are preserved where applicable.
- [ ] Tests prove both success and important failure paths.
- [ ] Documentation/contract snapshots are updated when required.
